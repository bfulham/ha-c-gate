"""Config, reconfigure, and options flows for C-Bus C-Gate."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.components.hassio import (
    HassioNotReadyError,
    SupervisorError,
    get_addons_info,
    get_addons_list,
    get_supervisor_client,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .addon import (
    AddonProjectError,
    DetectedCgateAddon,
    addon_info_to_dict,
    async_fetch_addon_project_backup,
    detect_cgate_addons,
    is_cgate_addon,
)
from .client import (
    CgateEndpoint,
    CgateError,
    async_fetch_project_xml,
    async_validate_endpoint,
)
from .const import (
    CONF_ADDON_SLUG,
    CONF_APPLICATION,
    CONF_APPLICATION_MAPPINGS,
    CONF_APPLICATION_OVERRIDES,
    CONF_AUTO_OPEN,
    CONF_COMMAND_POOL_SIZE,
    CONF_COMMAND_PORT,
    CONF_CONFIG_PORT,
    CONF_CONTINUE_OFFLINE,
    CONF_ENABLED,
    CONF_ENTITY_TYPE,
    CONF_EVENT_PORT,
    CONF_GROUP,
    CONF_GROUP_OVERRIDES,
    CONF_HIDE_INDIVIDUAL_FIXTURES,
    CONF_HOST,
    CONF_HUB_CONNECTIONS,
    CONF_INCLUDE_INTERNAL,
    CONF_INSTALLATION_ID,
    CONF_NETWORK,
    CONF_OPTIMISTIC,
    CONF_PROJECT_FILE,
    CONF_PROJECT_HASH,
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_PROJECT_NAME,
    CONF_STATUS_PORT,
    DEFAULT_AUTO_OPEN,
    DEFAULT_COMMAND_POOL_SIZE,
    DEFAULT_COMMAND_PORT,
    DEFAULT_CONFIG_PORT,
    DEFAULT_EVENT_PORT,
    DEFAULT_HIDE_INDIVIDUAL_FIXTURES,
    DEFAULT_HOST,
    DEFAULT_INCLUDE_INTERNAL,
    DEFAULT_OPTIMISTIC,
    DEFAULT_STATUS_PORT,
    DOMAIN,
    ENTITY_TYPES,
)
from .project import (
    ProjectError,
    parse_project_archive_bytes,
    parse_project_bytes,
    parse_project_path,
    project_diff,
    project_summary,
)
from .storage import async_load_project, async_save_project

_LOGGER = logging.getLogger(__name__)


def _connection_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise common C-Gate endpoint fields from a flow form."""
    return {
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_COMMAND_PORT: int(user_input[CONF_COMMAND_PORT]),
        CONF_EVENT_PORT: int(user_input[CONF_EVENT_PORT]),
        CONF_STATUS_PORT: int(user_input[CONF_STATUS_PORT]),
        CONF_CONFIG_PORT: int(user_input[CONF_CONFIG_PORT]),
        CONF_AUTO_OPEN: bool(user_input[CONF_AUTO_OPEN]),
    }


def _connection_schema(defaults: dict[str, Any], *, include_project: bool = False) -> vol.Schema:
    """Build the shared C-Gate connection form schema."""
    fields: dict[vol.Marker, Any] = {}
    if include_project:
        fields[vol.Required(CONF_PROJECT_NAME, default=defaults.get(CONF_PROJECT_NAME, ""))] = (
            TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
        )
    fields.update(
        {
            vol.Required(CONF_HOST, default=defaults[CONF_HOST]): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Required(CONF_COMMAND_PORT, default=defaults[CONF_COMMAND_PORT]): _port_selector(
                DEFAULT_COMMAND_PORT
            ),
            vol.Required(CONF_EVENT_PORT, default=defaults[CONF_EVENT_PORT]): _port_selector(
                DEFAULT_EVENT_PORT
            ),
            vol.Required(CONF_STATUS_PORT, default=defaults[CONF_STATUS_PORT]): _port_selector(
                DEFAULT_STATUS_PORT
            ),
            vol.Required(CONF_CONFIG_PORT, default=defaults[CONF_CONFIG_PORT]): _port_selector(
                DEFAULT_CONFIG_PORT
            ),
            vol.Required(CONF_AUTO_OPEN, default=defaults[CONF_AUTO_OPEN]): BooleanSelector(),
        }
    )
    return vol.Schema(fields)


def _addon_schema(
    addons: list[DetectedCgateAddon],
    *,
    default_project: str = "",
) -> vol.Schema:
    """Build a form for selecting a detected C-Gate add-on."""
    first = addons[0]
    project_name = default_project or first.project_name
    addon_options = [
        {
            "value": addon.slug,
            "label": f"{addon.name} ({addon.slug})",
        }
        for addon in addons
    ]
    return vol.Schema(
        {
            vol.Required(CONF_ADDON_SLUG, default=first.slug): SelectSelector(
                SelectSelectorConfig(
                    options=addon_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_PROJECT_NAME, default=project_name): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
        }
    )


def _connection_for_addon(addon: DetectedCgateAddon) -> dict[str, Any]:
    """Return the Supervisor-network endpoint for a detected add-on."""
    return {
        CONF_HOST: addon.host,
        CONF_COMMAND_PORT: DEFAULT_COMMAND_PORT,
        CONF_EVENT_PORT: DEFAULT_EVENT_PORT,
        CONF_STATUS_PORT: DEFAULT_STATUS_PORT,
        CONF_CONFIG_PORT: DEFAULT_CONFIG_PORT,
        CONF_AUTO_OPEN: DEFAULT_AUTO_OPEN,
    }


def _entity_type_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=list(ENTITY_TYPES),
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="entity_type",
        )
    )


def _port_selector(default: int) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=0, max=65535, mode=NumberSelectorMode.BOX))


class CbusCgateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a C-Gate-backed C-Bus installation."""

    VERSION = 1
    MINOR_VERSION = 0

    def __init__(self) -> None:
        self._project: dict[str, Any] | None = None
        self._connection: dict[str, Any] = {}
        self._connection_error: str | None = None
        self._old_project: dict[str, Any] | None = None
        self._detected_addons: list[DetectedCgateAddon] | None = None
        self._reconfigure_connection: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CbusCgateOptionsFlow()

    def _parse_upload(self, upload_id: str) -> dict[str, Any]:
        with process_uploaded_file(self.hass, upload_id) as file_path:
            return parse_project_path(Path(file_path))

    async def _async_detect_addons(self, *, refresh: bool = False) -> list[DetectedCgateAddon]:
        """Return running companion add-ons using cache then the Supervisor API."""
        if self._detected_addons is not None and not refresh:
            return self._detected_addons

        addons_info: dict[str, dict[str, Any] | Any | None] = {}
        try:
            addons_info.update(get_addons_info(self.hass))
        except (HassioNotReadyError, KeyError):
            pass

        # The legacy cache may not be populated when a config flow opens. Query
        # Supervisor directly so add-on discovery is reliable at first setup.
        try:
            supervisor = get_supervisor_client(self.hass)
            installed = await supervisor.addons.list()
            for item in installed:
                summary = addon_info_to_dict(item)
                slug = str(summary.get("slug") or getattr(item, "slug", "")).strip()
                if not slug or not is_cgate_addon(slug, summary):
                    continue
                try:
                    complete = addon_info_to_dict(await supervisor.addons.addon_info(slug))
                except SupervisorError as err:
                    _LOGGER.debug("Unable to read add-on details for %s: %s", slug, err)
                    complete = {}
                addons_info[slug] = {**summary, **complete}
        except (SupervisorError, HassioNotReadyError, KeyError, AttributeError) as err:
            _LOGGER.debug("Supervisor add-on discovery is not available: %s", err)
            # Some HA versions populate the add-on list before detailed info.
            try:
                for item in get_addons_list(self.hass):
                    info = addon_info_to_dict(item)
                    slug = str(info.get("slug") or "").strip()
                    if slug:
                        addons_info.setdefault(slug, info)
            except (HassioNotReadyError, KeyError):
                pass

        self._detected_addons = detect_cgate_addons(addons_info)
        return self._detected_addons

    async def _async_addon_by_slug(self, slug: str) -> DetectedCgateAddon | None:
        return next(
            (
                addon
                for addon in await self._async_detect_addons(refresh=True)
                if addon.slug == slug
            ),
            None,
        )

    async def _async_fetch_addon_project(
        self,
        addon: DetectedCgateAddon,
    ) -> dict[str, Any]:
        """Download and parse the add-on's real SQLite/XML project backup."""
        raw = await async_fetch_addon_project_backup(async_get_clientsession(self.hass), addon)
        return await self.hass.async_add_executor_job(
            parse_project_archive_bytes,
            raw,
            f"{addon.name} current backup.cbz",
        )

    async def _async_fetch_project(
        self,
        project_name: str,
        connection: dict[str, Any],
        source_name: str,
    ) -> dict[str, Any]:
        """Fetch and parse a project using one normalised C-Gate connection."""
        endpoint = CgateEndpoint(
            host=connection[CONF_HOST],
            command_port=connection[CONF_COMMAND_PORT],
            event_port=connection[CONF_EVENT_PORT],
            status_port=connection[CONF_STATUS_PORT],
            config_port=connection[CONF_CONFIG_PORT],
            project=project_name,
        )
        raw = await async_fetch_project_xml(endpoint)
        return await self.hass.async_add_executor_job(
            parse_project_bytes,
            raw,
            source_name,
            "xml",
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose whether to fetch the project from C-Gate or upload it."""
        menu_options = ["fetch_project", "upload_project"]
        if await self._async_detect_addons(refresh=True):
            menu_options.insert(0, "addon_project")
        return self.async_show_menu(step_id="user", menu_options=menu_options)

    async def async_step_addon_project(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Import from a running C-Gate Server add-on detected by Supervisor."""
        addons = await self._async_detect_addons(refresh=True)
        if not addons:
            return self.async_abort(reason="addon_not_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            addon = await self._async_addon_by_slug(str(user_input[CONF_ADDON_SLUG]))
            if addon is None:
                errors["base"] = "addon_not_available"
            else:
                expected_project = str(user_input.get(CONF_PROJECT_NAME, "")).strip()
                self._connection = _connection_for_addon(addon)
                try:
                    self._project = await self._async_fetch_addon_project(addon)
                    if (
                        expected_project
                        and self._project["project_name"].casefold() != expected_project.casefold()
                    ):
                        raise ProjectError(
                            f"The add-on backup contains {self._project['project_name']}, "
                            f"not {expected_project}"
                        )
                except AddonProjectError as err:
                    _LOGGER.warning("Unable to download Toolkit backup from add-on: %s", err)
                    errors["base"] = "cannot_fetch_project"
                except (OSError, ProjectError, ValueError) as err:
                    _LOGGER.warning("C-Gate add-on returned an invalid Toolkit project: %s", err)
                    errors["base"] = "invalid_fetched_project"
                else:
                    return await self.async_step_confirm()

        return self.async_show_form(
            step_id="addon_project",
            data_schema=_addon_schema(addons),
            errors=errors,
            description_placeholders={
                "addon_count": str(len(addons)),
            },
        )

    async def async_step_fetch_project(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fetch and import the loaded Toolkit project from C-Gate."""
        errors: dict[str, str] = {}
        if user_input is not None:
            project_name = str(user_input[CONF_PROJECT_NAME]).strip()
            self._connection = _connection_from_input(user_input)
            try:
                self._project = await self._async_fetch_project(
                    project_name,
                    self._connection,
                    f"{project_name}.xml (fetched from C-Gate)",
                )
            except CgateError as err:
                _LOGGER.warning("Unable to fetch Toolkit project from C-Gate: %s", err)
                errors["base"] = "cannot_fetch_project"
            except (OSError, ProjectError, ValueError) as err:
                _LOGGER.warning("C-Gate returned an invalid Toolkit project: %s", err)
                errors["base"] = "invalid_fetched_project"
            else:
                return await self.async_step_confirm()

        defaults = {
            CONF_PROJECT_NAME: "",
            CONF_HOST: DEFAULT_HOST,
            CONF_COMMAND_PORT: DEFAULT_COMMAND_PORT,
            CONF_EVENT_PORT: DEFAULT_EVENT_PORT,
            CONF_STATUS_PORT: DEFAULT_STATUS_PORT,
            CONF_CONFIG_PORT: DEFAULT_CONFIG_PORT,
            CONF_AUTO_OPEN: DEFAULT_AUTO_OPEN,
            **self._connection,
        }
        return self.async_show_form(
            step_id="fetch_project",
            data_schema=_connection_schema(defaults, include_project=True),
            errors=errors,
        )

    async def async_step_upload_project(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Import a Toolkit project from an uploaded file."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._project = await self.hass.async_add_executor_job(
                    self._parse_upload, user_input[CONF_PROJECT_FILE]
                )
            except (OSError, ProjectError, ValueError):
                errors["base"] = "invalid_project"
            else:
                return await self.async_step_connection()
        return self.async_show_form(
            step_id="upload_project",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROJECT_FILE): FileSelector(
                        FileSelectorConfig(
                            accept=".cbz,.db,.xml,application/zip,application/xml,application/x-sqlite3"
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure and validate the default C-Gate endpoint."""
        assert self._project is not None
        if user_input is not None:
            self._connection = _connection_from_input(user_input)
            endpoint = CgateEndpoint(
                host=self._connection[CONF_HOST],
                command_port=self._connection[CONF_COMMAND_PORT],
                event_port=self._connection[CONF_EVENT_PORT],
                status_port=self._connection[CONF_STATUS_PORT],
                config_port=self._connection[CONF_CONFIG_PORT],
                project=self._project["project_name"],
            )
            valid, error = await async_validate_endpoint(endpoint)
            if valid:
                return await self.async_step_confirm()
            self._connection_error = error
            return await self.async_step_connection_failed()

        defaults = self._connection or {
            CONF_HOST: DEFAULT_HOST,
            CONF_COMMAND_PORT: DEFAULT_COMMAND_PORT,
            CONF_EVENT_PORT: DEFAULT_EVENT_PORT,
            CONF_STATUS_PORT: DEFAULT_STATUS_PORT,
            CONF_CONFIG_PORT: DEFAULT_CONFIG_PORT,
            CONF_AUTO_OPEN: DEFAULT_AUTO_OPEN,
        }
        return self.async_show_form(
            step_id="connection",
            data_schema=_connection_schema(defaults),
            description_placeholders={"project_summary": project_summary(self._project)},
        )

    async def async_step_connection_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow offline setup after a failed validation."""
        if user_input is not None:
            if bool(user_input[CONF_CONTINUE_OFFLINE]):
                return await self.async_step_confirm()
            return await self.async_step_connection()
        return self.async_show_form(
            step_id="connection_failed",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONTINUE_OFFLINE, default=True): BooleanSelector()}
            ),
            description_placeholders={
                "connection_error": self._connection_error or "Unknown error"
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the project and initial defaults."""
        assert self._project is not None
        if user_input is not None:
            await self.async_set_unique_id(self._project["project_id"].casefold())
            self._abort_if_unique_id_configured()
            project_key = await async_save_project(self.hass, self._project)
            connection_by_hub = {
                str(network["address"]): {
                    **self._connection,
                    CONF_ENABLED: True,
                }
                for network in self._project["networks"]
            }
            return self.async_create_entry(
                title=self._project["project_name"],
                data={
                    CONF_PROJECT_KEY: project_key,
                    CONF_PROJECT_NAME: self._project["project_name"],
                    CONF_PROJECT_ID: self._project["project_id"],
                    CONF_PROJECT_HASH: self._project["source_sha256"],
                    CONF_INSTALLATION_ID: uuid4().hex,
                    CONF_HUB_CONNECTIONS: connection_by_hub,
                    CONF_APPLICATION_MAPPINGS: self._project["default_application_mappings"],
                    CONF_APPLICATION_OVERRIDES: {},
                    CONF_GROUP_OVERRIDES: {},
                    CONF_INCLUDE_INTERNAL: DEFAULT_INCLUDE_INTERNAL,
                    CONF_COMMAND_POOL_SIZE: DEFAULT_COMMAND_POOL_SIZE,
                    CONF_OPTIMISTIC: DEFAULT_OPTIMISTIC,
                },
            )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "summary": project_summary(self._project),
                "connection": (
                    f"{self._connection.get(CONF_HOST)}:{self._connection.get(CONF_COMMAND_PORT)}"
                ),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to update the stored Toolkit project."""
        entry = self._get_reconfigure_entry()
        if self._old_project is None:
            self._old_project = await async_load_project(self.hass, entry.data[CONF_PROJECT_KEY])
        menu_options = ["reconfigure_fetch", "reconfigure_upload"]
        if await self._async_detect_addons(refresh=True):
            menu_options.insert(0, "reconfigure_addon")
        return self.async_show_menu(step_id="reconfigure", menu_options=menu_options)

    async def async_step_reconfigure_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fetch an update from a running C-Gate Server add-on."""
        entry = self._get_reconfigure_entry()
        if self._old_project is None:
            self._old_project = await async_load_project(self.hass, entry.data[CONF_PROJECT_KEY])
        assert self._old_project is not None

        addons = await self._async_detect_addons(refresh=True)
        if not addons:
            return self.async_abort(reason="addon_not_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            addon = await self._async_addon_by_slug(str(user_input[CONF_ADDON_SLUG]))
            if addon is None:
                errors["base"] = "addon_not_available"
            else:
                expected_project = str(user_input.get(CONF_PROJECT_NAME, "")).strip()
                connection = _connection_for_addon(addon)
                try:
                    project = await self._async_fetch_addon_project(addon)
                    if (
                        expected_project
                        and project["project_name"].casefold() != expected_project.casefold()
                    ):
                        raise ProjectError(
                            f"The add-on backup contains {project['project_name']}, "
                            f"not {expected_project}"
                        )
                    self._validate_reconfigured_project(project)
                    self._project = project
                    self._reconfigure_connection = connection
                except AddonProjectError as err:
                    _LOGGER.warning("Unable to download Toolkit backup from add-on: %s", err)
                    errors["base"] = "cannot_fetch_project"
                except ProjectError as err:
                    _LOGGER.warning("C-Gate add-on project update was rejected: %s", err)
                    errors["base"] = (
                        "different_project"
                        if "different C-Bus project" in str(err)
                        else "invalid_fetched_project"
                    )
                except (OSError, ValueError) as err:
                    _LOGGER.warning("C-Gate add-on returned an invalid Toolkit project: %s", err)
                    errors["base"] = "invalid_fetched_project"
                else:
                    return await self.async_step_reconfigure_confirm()

        default_project = entry.data.get(CONF_PROJECT_NAME, "")
        return self.async_show_form(
            step_id="reconfigure_addon",
            data_schema=_addon_schema(addons, default_project=default_project),
            errors=errors,
            description_placeholders={
                "addon_count": str(len(addons)),
            },
        )

    async def async_step_reconfigure_fetch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fetch the latest project database from the configured C-Gate server."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if self._old_project is None:
            self._old_project = await async_load_project(self.hass, entry.data[CONF_PROJECT_KEY])
        assert self._old_project is not None

        hubs = entry.data.get(CONF_HUB_CONNECTIONS, {})
        fallback = next(iter(hubs.values()), {})
        defaults = {
            CONF_PROJECT_NAME: entry.data[CONF_PROJECT_NAME],
            CONF_HOST: fallback.get(CONF_HOST, DEFAULT_HOST),
            CONF_COMMAND_PORT: fallback.get(CONF_COMMAND_PORT, DEFAULT_COMMAND_PORT),
            CONF_EVENT_PORT: fallback.get(CONF_EVENT_PORT, DEFAULT_EVENT_PORT),
            CONF_STATUS_PORT: fallback.get(CONF_STATUS_PORT, DEFAULT_STATUS_PORT),
            CONF_CONFIG_PORT: fallback.get(CONF_CONFIG_PORT, DEFAULT_CONFIG_PORT),
            CONF_AUTO_OPEN: fallback.get(CONF_AUTO_OPEN, DEFAULT_AUTO_OPEN),
        }

        if user_input is not None:
            project_name = str(user_input[CONF_PROJECT_NAME]).strip()
            connection = _connection_from_input(user_input)
            try:
                project = await self._async_fetch_project(
                    project_name,
                    connection,
                    f"{project_name}.xml (fetched from C-Gate)",
                )
                self._validate_reconfigured_project(project)
                self._project = project
            except CgateError as err:
                _LOGGER.warning("Unable to fetch Toolkit project from C-Gate: %s", err)
                errors["base"] = "cannot_fetch_project"
            except ProjectError as err:
                _LOGGER.warning("C-Gate project update was rejected: %s", err)
                errors["base"] = (
                    "different_project"
                    if "different C-Bus project" in str(err)
                    else "invalid_fetched_project"
                )
            except (OSError, ValueError) as err:
                _LOGGER.warning("C-Gate returned an invalid Toolkit project: %s", err)
                errors["base"] = "invalid_fetched_project"
            else:
                return await self.async_step_reconfigure_confirm()

        return self.async_show_form(
            step_id="reconfigure_fetch",
            data_schema=_connection_schema(defaults, include_project=True),
            errors=errors,
        )

    async def async_step_reconfigure_upload(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Upload a replacement Toolkit project."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if self._old_project is None:
            self._old_project = await async_load_project(self.hass, entry.data[CONF_PROJECT_KEY])
        assert self._old_project is not None
        if user_input is not None:
            try:
                project = await self.hass.async_add_executor_job(
                    self._parse_upload, user_input[CONF_PROJECT_FILE]
                )
                self._validate_reconfigured_project(project)
                self._project = project
            except ProjectError as err:
                errors["base"] = (
                    "different_project"
                    if "different C-Bus project" in str(err)
                    else "invalid_project"
                )
            except (OSError, ValueError):
                errors["base"] = "invalid_project"
            else:
                return await self.async_step_reconfigure_confirm()
        return self.async_show_form(
            step_id="reconfigure_upload",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROJECT_FILE): FileSelector(
                        FileSelectorConfig(
                            accept=".cbz,.db,.xml,application/zip,application/xml,application/x-sqlite3"
                        )
                    )
                }
            ),
            errors=errors,
        )

    def _validate_reconfigured_project(self, project: dict[str, Any]) -> None:
        """Prevent replacing an entry with a different C-Bus installation."""
        assert self._old_project is not None
        if project["project_id"].casefold() != self._old_project["project_id"].casefold():
            raise ProjectError("The update belongs to a different C-Bus project")

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._project is not None
        assert self._old_project is not None
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            project_key = await async_save_project(self.hass, self._project)
            old_hubs = entry.data[CONF_HUB_CONNECTIONS]
            fallback = next(iter(old_hubs.values()), {})
            new_hubs = {}
            for network in self._project["networks"]:
                key = str(network["address"])
                settings = old_hubs.get(key, {**fallback, CONF_ENABLED: True})
                if self._reconfigure_connection is not None:
                    settings = {**settings, **self._reconfigure_connection}
                new_hubs[key] = settings
            new_mappings = {
                **self._project["default_application_mappings"],
                **entry.data.get(CONF_APPLICATION_MAPPINGS, {}),
            }
            return self.async_update_and_abort(
                entry,
                data={
                    **entry.data,
                    CONF_PROJECT_KEY: project_key,
                    CONF_PROJECT_NAME: self._project["project_name"],
                    CONF_PROJECT_ID: self._project["project_id"],
                    CONF_PROJECT_HASH: self._project["source_sha256"],
                    CONF_HUB_CONNECTIONS: new_hubs,
                    CONF_APPLICATION_MAPPINGS: new_mappings,
                },
                reason="reconfigure_successful",
            )
        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"summary": project_diff(self._old_project, self._project)},
        )


class CbusCgateOptionsFlow(OptionsFlow):
    """Edit hub endpoints, application mappings, and group overrides."""

    def __init__(self) -> None:
        self._project: dict[str, Any] | None = None
        self._selected_network: int | None = None
        self._selected_application: int | None = None
        self._selected_group: int | None = None

    async def _load_project(self) -> dict[str, Any]:
        if self._project is None:
            project = await async_load_project(self.hass, self.config_entry.data[CONF_PROJECT_KEY])
            if project is None:
                raise RuntimeError("Stored Toolkit project is missing")
            self._project = project
        return self._project

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["connections", "applications", "groups", "performance"],
        )

    async def async_step_connections(self, user_input=None) -> ConfigFlowResult:
        project = await self._load_project()
        choices = {
            str(network["address"]): f"{network['address']} — {network['name']}"
            for network in project["networks"]
        }
        if user_input is not None:
            self._selected_network = int(user_input[CONF_NETWORK])
            return await self.async_step_connection()
        return self.async_show_form(
            step_id="connections",
            data_schema=vol.Schema({vol.Required(CONF_NETWORK): vol.In(choices)}),
        )

    async def async_step_connection(self, user_input=None) -> ConfigFlowResult:
        assert self._selected_network is not None
        options = dict(self.config_entry.options)
        all_connections = {
            **self.config_entry.data[CONF_HUB_CONNECTIONS],
            **options.get(CONF_HUB_CONNECTIONS, {}),
        }
        current = dict(all_connections[str(self._selected_network)])
        if user_input is not None:
            overrides = dict(options.get(CONF_HUB_CONNECTIONS, {}))
            overrides[str(self._selected_network)] = {
                CONF_ENABLED: bool(user_input[CONF_ENABLED]),
                CONF_HOST: str(user_input[CONF_HOST]).strip(),
                CONF_COMMAND_PORT: int(user_input[CONF_COMMAND_PORT]),
                CONF_EVENT_PORT: int(user_input[CONF_EVENT_PORT]),
                CONF_STATUS_PORT: int(user_input[CONF_STATUS_PORT]),
                CONF_CONFIG_PORT: int(user_input[CONF_CONFIG_PORT]),
                CONF_AUTO_OPEN: bool(user_input[CONF_AUTO_OPEN]),
            }
            options[CONF_HUB_CONNECTIONS] = overrides
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="connection",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENABLED, default=current.get(CONF_ENABLED, True)
                    ): BooleanSelector(),
                    vol.Required(CONF_HOST, default=current[CONF_HOST]): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(
                        CONF_COMMAND_PORT, default=current[CONF_COMMAND_PORT]
                    ): _port_selector(DEFAULT_COMMAND_PORT),
                    vol.Required(CONF_EVENT_PORT, default=current[CONF_EVENT_PORT]): _port_selector(
                        DEFAULT_EVENT_PORT
                    ),
                    vol.Required(
                        CONF_STATUS_PORT, default=current[CONF_STATUS_PORT]
                    ): _port_selector(DEFAULT_STATUS_PORT),
                    vol.Required(
                        CONF_CONFIG_PORT, default=current[CONF_CONFIG_PORT]
                    ): _port_selector(DEFAULT_CONFIG_PORT),
                    vol.Required(
                        CONF_AUTO_OPEN, default=current.get(CONF_AUTO_OPEN, True)
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_applications(self, user_input=None) -> ConfigFlowResult:
        project = await self._load_project()
        choices = {
            f"{network['address']}:{application['address']}": (
                f"{network['name']} — {application['address']} {application['name']}"
            )
            for network in project["networks"]
            for application in network["applications"]
            if application["groups"] or application.get("measurements")
        }
        if user_input is not None:
            network, application = str(user_input[CONF_APPLICATION]).split(":", 1)
            self._selected_network = int(network)
            self._selected_application = int(application)
            return await self.async_step_application()
        return self.async_show_form(
            step_id="applications",
            data_schema=vol.Schema({vol.Required(CONF_APPLICATION): vol.In(choices)}),
        )

    async def async_step_application(self, user_input=None) -> ConfigFlowResult:
        assert self._selected_network is not None
        assert self._selected_application is not None
        options = dict(self.config_entry.options)
        overrides = dict(options.get(CONF_APPLICATION_OVERRIDES, {}))
        key = f"{self._selected_network}:{self._selected_application}"
        default_mapping = options.get(CONF_APPLICATION_MAPPINGS, {}).get(
            str(self._selected_application),
            self.config_entry.data[CONF_APPLICATION_MAPPINGS].get(
                str(self._selected_application), "ignore"
            ),
        )
        if user_input is not None:
            selected = str(user_input[CONF_ENTITY_TYPE])
            if selected == default_mapping:
                overrides.pop(key, None)
            else:
                overrides[key] = selected
            options[CONF_APPLICATION_OVERRIDES] = overrides
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="application",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENTITY_TYPE,
                        default=overrides.get(key, default_mapping),
                    ): _entity_type_selector()
                }
            ),
        )

    async def async_step_groups(self, user_input=None) -> ConfigFlowResult:
        return await self.async_step_group_application(user_input)

    async def async_step_group_application(self, user_input=None) -> ConfigFlowResult:
        project = await self._load_project()
        choices = {
            f"{network['address']}:{application['address']}": (
                f"{network['name']} — {application['address']} {application['name']}"
            )
            for network in project["networks"]
            for application in network["applications"]
            if application["groups"]
        }
        if user_input is not None:
            network, application = str(user_input[CONF_APPLICATION]).split(":", 1)
            self._selected_network = int(network)
            self._selected_application = int(application)
            return await self.async_step_group()
        return self.async_show_form(
            step_id="group_application",
            data_schema=vol.Schema({vol.Required(CONF_APPLICATION): vol.In(choices)}),
        )

    async def async_step_group(self, user_input=None) -> ConfigFlowResult:
        project = await self._load_project()
        assert self._selected_network is not None
        assert self._selected_application is not None
        network = next(
            item for item in project["networks"] if item["address"] == self._selected_network
        )
        application = next(
            item
            for item in network["applications"]
            if item["address"] == self._selected_application
        )
        choices = {
            str(group["address"]): f"{group['address']} — {group['name']}"
            for group in application["groups"]
        }
        if user_input is not None:
            self._selected_group = int(user_input[CONF_GROUP])
            return await self.async_step_group_override()
        return self.async_show_form(
            step_id="group",
            data_schema=vol.Schema({vol.Required(CONF_GROUP): vol.In(choices)}),
        )

    async def async_step_group_override(self, user_input=None) -> ConfigFlowResult:
        assert self._selected_network is not None
        assert self._selected_application is not None
        assert self._selected_group is not None
        options = dict(self.config_entry.options)
        overrides = dict(options.get(CONF_GROUP_OVERRIDES, {}))
        key = f"{self._selected_network}:{self._selected_application}:{self._selected_group}"
        if user_input is not None:
            overrides[key] = str(user_input[CONF_ENTITY_TYPE])
            options[CONF_GROUP_OVERRIDES] = overrides
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="group_override",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENTITY_TYPE, default=overrides.get(key, "auto")
                    ): _entity_type_selector()
                }
            ),
        )

    async def async_step_performance(self, user_input=None) -> ConfigFlowResult:
        options = dict(self.config_entry.options)
        if user_input is not None:
            options[CONF_COMMAND_POOL_SIZE] = int(user_input[CONF_COMMAND_POOL_SIZE])
            options[CONF_OPTIMISTIC] = bool(user_input[CONF_OPTIMISTIC])
            options[CONF_INCLUDE_INTERNAL] = bool(user_input[CONF_INCLUDE_INTERNAL])
            options[CONF_HIDE_INDIVIDUAL_FIXTURES] = bool(
                user_input[CONF_HIDE_INDIVIDUAL_FIXTURES]
            )
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="performance",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_COMMAND_POOL_SIZE,
                        default=options.get(
                            CONF_COMMAND_POOL_SIZE,
                            self.config_entry.data.get(
                                CONF_COMMAND_POOL_SIZE, DEFAULT_COMMAND_POOL_SIZE
                            ),
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=8, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Required(
                        CONF_OPTIMISTIC,
                        default=options.get(
                            CONF_OPTIMISTIC,
                            self.config_entry.data.get(CONF_OPTIMISTIC, True),
                        ),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_INCLUDE_INTERNAL,
                        default=options.get(
                            CONF_INCLUDE_INTERNAL,
                            self.config_entry.data.get(CONF_INCLUDE_INTERNAL, False),
                        ),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_HIDE_INDIVIDUAL_FIXTURES,
                        default=options.get(
                            CONF_HIDE_INDIVIDUAL_FIXTURES,
                            self.config_entry.data.get(
                                CONF_HIDE_INDIVIDUAL_FIXTURES,
                                DEFAULT_HIDE_INDIVIDUAL_FIXTURES,
                            ),
                        ),
                    ): BooleanSelector(),
                }
            ),
        )
