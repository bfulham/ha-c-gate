"""C-Bus C-Gate integration."""

from __future__ import annotations

import re
from contextlib import suppress

import attr
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    CONF_ENTITY_ID_STYLE_VERSION,
    CONF_PROJECT_KEY,
    DOMAIN,
    PLATFORMS,
    SHORT_ENTITY_ID_STYLE_VERSION,
)
from .entity import application_device_info, hub_device_info, server_device_info
from .runtime import CbusCgateRuntime
from .storage import async_delete_project, async_load_project


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an imported C-Bus project."""
    project = await async_load_project(hass, entry.data[CONF_PROJECT_KEY])
    if project is None:
        raise ConfigEntryError(
            "The imported Toolkit project is missing. "
            "Reconfigure the integration and upload it again."
        )

    domain_data = hass.data.setdefault(DOMAIN, {})
    stale: CbusCgateRuntime | None = domain_data.pop(entry.entry_id, None)
    if stale is not None:
        await stale.stop()

    runtime = CbusCgateRuntime(hass, entry, project)
    domain_data[entry.entry_id] = runtime

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **server_device_info(runtime),
    )
    for network in project["networks"]:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **hub_device_info(runtime, network),
        )
    for network, application in runtime.application_definitions:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **application_device_info(runtime, network, application),
        )

    _cleanup_legacy_registry_entries(hass, entry, runtime)
    _cleanup_changed_group_domains(hass, entry, runtime)
    _migrate_entity_ids_to_short_names(hass, entry, runtime)

    try:
        await runtime.start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await runtime.stop()
        with suppress(Exception):
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        domain_data.pop(entry.entry_id, None)
        raise

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _cleanup_legacy_registry_entries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: CbusCgateRuntime,
) -> None:
    """Remove registry entries from the pre-application-device layout."""
    entity_registry = er.async_get(hass)
    motion_unique_id = re.compile(
        rf"^{re.escape(runtime.installation_id)}:n\d+:u\d+:motion$"
    )
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if motion_unique_id.fullmatch(entity_entry.unique_id):
            entity_registry.async_remove(entity_entry.entity_id)

    device_registry = dr.async_get(hass)
    legacy_prefix = f"{runtime.installation_id}:hub:"
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        identifiers = [
            identifier
            for domain, identifier in device_entry.identifiers
            if domain == DOMAIN and identifier.startswith(legacy_prefix)
        ]
        if any(
            identifier.endswith((":lights", ":sensors"))
            or ":unit:" in identifier
            for identifier in identifiers
        ):
            device_registry.async_remove_device(device_entry.id)


def _cleanup_changed_group_domains(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: CbusCgateRuntime,
) -> None:
    """Remove stale domains and fixture entities hidden by groups-only mode.

    Home Assistant keys entity-registry entries by entity domain as well as
    integration platform and unique ID. A group changing domain therefore
    leaves the previous entry behind. The registry also needs explicit cleanup
    when the DALI groups-only option hides an individual fitting.
    """
    expected_domains = {
        (
            f"{runtime.installation_id}:n{definition.network['address']}:"
            f"a{definition.application['address']}:g{definition.group['address']}"
        ): definition.entity_type
        for definition in runtime.group_definitions
    }
    hidden_fixture_ids = (
        {
            (
                f"{runtime.installation_id}:n{network['address']}:"
                f"a{application['address']}:g{group['address']}"
            )
            for network in runtime.project["networks"]
            for application in network["applications"]
            for group in application["groups"]
            if group.get("individual_fixture", False)
        }
        if runtime.hide_individual_fixtures
        else set()
    )

    entity_registry = er.async_get(hass)
    group_unique_id = re.compile(
        rf"^{re.escape(runtime.installation_id)}:n\d+:a\d+:g\d+$"
    )
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if not group_unique_id.fullmatch(entity_entry.unique_id):
            continue
        expected_domain = expected_domains.get(entity_entry.unique_id)
        domain_changed = (
            expected_domain is not None and entity_entry.domain != expected_domain
        )
        if not domain_changed and entity_entry.unique_id not in hidden_fixture_ids:
            continue
        entity_registry.async_remove(entity_entry.entity_id)


def _desired_short_entity_ids(
    runtime: CbusCgateRuntime,
) -> dict[str, tuple[str, str]]:
    """Return unique IDs mapped to the entity domain and unprefixed name."""
    desired: dict[str, tuple[str, str]] = {}

    for definition in runtime.group_definitions:
        unique_id = (
            f"{runtime.installation_id}:n{definition.network['address']}:"
            f"a{definition.application['address']}:g{definition.group['address']}"
        )
        desired[unique_id] = (definition.entity_type, definition.group["name"])

    for definition in runtime.measurement_definitions:
        measurement = definition.measurement
        unique_id = (
            f"{runtime.installation_id}:n{definition.network['address']}:"
            f"a{definition.application['address']}:d{measurement['device']}:"
            f"c{measurement['channel']}"
        )
        desired[unique_id] = ("sensor", measurement["name"])

    for network in runtime.project["networks"]:
        network_address = network["address"]
        desired[
            f"{runtime.installation_id}:n{network_address}:connectivity"
        ] = ("binary_sensor", "C-Gate connection")
        desired[f"{runtime.installation_id}:n{network_address}:reopen"] = (
            "button",
            "Reopen network",
        )
        desired[f"{runtime.installation_id}:n{network_address}:resync"] = (
            "button",
            "Resynchronise state",
        )

    return desired


def _entry_uses_generated_entity_id(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    entity_entry: er.RegistryEntry,
    entity_name: str,
) -> bool:
    """Return whether an existing ID still looks integration-generated.

    User-chosen IDs are preserved. The registry regeneration check handles the
    normal case, while the device-prefix fallback covers older registry data.
    """
    try:
        entry_without_user_name = attr.evolve(entity_entry, name=None)
        if (
            entity_registry.async_regenerate_entity_id(entry_without_user_name)
            == entity_entry.entity_id
        ):
            return True
    except (AttributeError, TypeError, ValueError):
        pass

    object_id = entity_entry.entity_id.partition(".")[2]
    entity_slug = slugify(entity_name)
    if not entity_slug:
        return False

    if object_id == entity_slug or re.fullmatch(
        rf"{re.escape(entity_slug)}_\d+", object_id
    ):
        return True

    if entity_entry.device_id is None:
        return False
    device = dr.async_get(hass).async_get(entity_entry.device_id)
    if device is None:
        return False
    device_slugs = {
        slugify(name)
        for name in (device.name_by_user, device.name)
        if name and slugify(name)
    }
    for device_slug in device_slugs:
        base_suffix = f"{device_slug}_{entity_slug}"
        if object_id == base_suffix or object_id.endswith(f"_{base_suffix}"):
            return True
        if re.fullmatch(rf".*{re.escape(base_suffix)}_\d+", object_id):
            return True
    return False


def _migrate_entity_ids_to_short_names(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: CbusCgateRuntime,
) -> None:
    """One-time migration from device-prefixed IDs to name-only IDs."""
    if (
        int(entry.data.get(CONF_ENTITY_ID_STYLE_VERSION, 0) or 0)
        >= SHORT_ENTITY_ID_STYLE_VERSION
    ):
        return

    desired = _desired_short_entity_ids(runtime)
    entity_registry = er.async_get(hass)
    get_available_id = getattr(
        entity_registry,
        "async_get_available_entity_id",
        entity_registry.async_generate_entity_id,
    )

    entries = sorted(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id),
        key=lambda registry_entry: registry_entry.unique_id,
    )
    for entity_entry in entries:
        target = desired.get(entity_entry.unique_id)
        if target is None:
            continue
        domain, entity_name = target
        if entity_entry.domain != domain:
            continue

        new_entity_id = get_available_id(
            domain,
            entity_name,
            current_entity_id=entity_entry.entity_id,
        )
        if new_entity_id == entity_entry.entity_id:
            continue
        if not _entry_uses_generated_entity_id(
            hass, entity_registry, entity_entry, entity_name
        ):
            continue

        entity_registry.async_update_entity(
            entity_entry.entity_id, new_entity_id=new_entity_id
        )

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_ENTITY_ID_STYLE_VERSION: SHORT_ENTITY_ID_STYLE_VERSION,
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime: CbusCgateRuntime = hass.data[DOMAIN].pop(entry.entry_id)
        await runtime.stop()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_delete_project(hass, entry.data[CONF_PROJECT_KEY])
