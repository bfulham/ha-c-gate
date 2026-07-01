"""C-Bus C-Gate integration."""

from __future__ import annotations

import re
from contextlib import suppress

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_PROJECT_KEY, DOMAIN, PLATFORMS
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
