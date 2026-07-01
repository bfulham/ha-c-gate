"""Config, reconfigure, and options flows for C-Bus C-Gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
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

from .client import CgateEndpoint, async_validate_endpoint
from .const import (
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
    DEFAULT_HOST,
    DEFAULT_INCLUDE_INTERNAL,
    DEFAULT_OPTIMISTIC,
    DEFAULT_STATUS_PORT,
    DOMAIN,
    ENTITY_TYPES,
)
from .project import ProjectError, parse_project_path, project_diff, project_summary
from .storage import async_load_project, async_save_project


def _entity_type_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=list(ENTITY_TYPES),
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="entity_type",
        )
    )


def _port_selector(default: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=0, max=65535, mode=NumberSelectorMode.BOX)
    )


class CbusCgateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a C-Gate-backed C-Bus installation."""

    VERSION = 1
    MINOR_VERSION = 0

    def __init__(self) -> None:
        self._project: dict[str, Any] | None = None
        self._connection: dict[str, Any] = {}
        self._connection_error: str | None = None
        self._old_project: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CbusCgateOptionsFlow()

    def _parse_upload(self, upload_id: str) -> dict[str, Any]:
        with process_uploaded_file(self.hass, upload_id) as file_path:
            return parse_project_path(Path(file_path))

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Import the Toolkit project."""
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
            step_id="user",
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
            self._connection = {
                CONF_HOST: str(user_input[CONF_HOST]).strip(),
                CONF_COMMAND_PORT: int(user_input[CONF_COMMAND_PORT]),
                CONF_EVENT_PORT: int(user_input[CONF_EVENT_PORT]),
                CONF_STATUS_PORT: int(user_input[CONF_STATUS_PORT]),
                CONF_CONFIG_PORT: int(user_input[CONF_CONFIG_PORT]),
                CONF_AUTO_OPEN: bool(user_input[CONF_AUTO_OPEN]),
            }
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
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=defaults[CONF_HOST]): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(
                        CONF_COMMAND_PORT, default=defaults[CONF_COMMAND_PORT]
                    ): _port_selector(DEFAULT_COMMAND_PORT),
                    vol.Required(
                        CONF_EVENT_PORT, default=defaults[CONF_EVENT_PORT]
                    ): _port_selector(DEFAULT_EVENT_PORT),
                    vol.Required(
                        CONF_STATUS_PORT, default=defaults[CONF_STATUS_PORT]
                    ): _port_selector(DEFAULT_STATUS_PORT),
                    vol.Required(
                        CONF_CONFIG_PORT, default=defaults[CONF_CONFIG_PORT]
                    ): _port_selector(DEFAULT_CONFIG_PORT),
                    vol.Required(
                        CONF_AUTO_OPEN, default=defaults[CONF_AUTO_OPEN]
                    ): BooleanSelector(),
                }
            ),
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
            description_placeholders={"connection_error": self._connection_error or "Unknown error"},
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
                    CONF_APPLICATION_MAPPINGS: self._project[
                        "default_application_mappings"
                    ],
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
                    f"{self._connection.get(CONF_HOST)}:"
                    f"{self._connection.get(CONF_COMMAND_PORT)}"
                ),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Upload a replacement Toolkit project."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if self._old_project is None:
            self._old_project = await async_load_project(
                self.hass, entry.data[CONF_PROJECT_KEY]
            )
        if user_input is not None:
            try:
                self._project = await self.hass.async_add_executor_job(
                    self._parse_upload, user_input[CONF_PROJECT_FILE]
                )
            except (OSError, ProjectError, ValueError):
                errors["base"] = "invalid_project"
            else:
                return await self.async_step_reconfigure_confirm()
        return self.async_show_form(
            step_id="reconfigure",
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
            new_hubs = {
                str(network["address"]): old_hubs.get(
                    str(network["address"]),
                    {**fallback, CONF_ENABLED: True},
                )
                for network in self._project["networks"]
            }
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
            description_placeholders={
                "summary": project_diff(self._old_project, self._project)
            },
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
            project = await async_load_project(
                self.hass, self.config_entry.data[CONF_PROJECT_KEY]
            )
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
                    vol.Required(CONF_ENABLED, default=current.get(CONF_ENABLED, True)): BooleanSelector(),
                    vol.Required(CONF_HOST, default=current[CONF_HOST]): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Required(CONF_COMMAND_PORT, default=current[CONF_COMMAND_PORT]): _port_selector(DEFAULT_COMMAND_PORT),
                    vol.Required(CONF_EVENT_PORT, default=current[CONF_EVENT_PORT]): _port_selector(DEFAULT_EVENT_PORT),
                    vol.Required(CONF_STATUS_PORT, default=current[CONF_STATUS_PORT]): _port_selector(DEFAULT_STATUS_PORT),
                    vol.Required(CONF_CONFIG_PORT, default=current[CONF_CONFIG_PORT]): _port_selector(DEFAULT_CONFIG_PORT),
                    vol.Required(CONF_AUTO_OPEN, default=current.get(CONF_AUTO_OPEN, True)): BooleanSelector(),
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
        network = next(item for item in project["networks"] if item["address"] == self._selected_network)
        application = next(item for item in network["applications"] if item["address"] == self._selected_application)
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
                        NumberSelectorConfig(
                            min=1, max=8, step=1, mode=NumberSelectorMode.BOX
                        )
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
                }
            ),
        )
