"""C-Bus C-Gate integration."""

from __future__ import annotations

from contextlib import suppress

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr

from .const import CONF_PROJECT_KEY, DOMAIN, PLATFORMS
from .entity import hub_device_info, server_device_info
from .runtime import CbusCgateRuntime
from .storage import async_delete_project, async_load_project


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an imported C-Bus project."""
    project = await async_load_project(hass, entry.data[CONF_PROJECT_KEY])
    if project is None:
        raise ConfigEntryError(
            "The imported Toolkit project is missing. Reconfigure the integration and upload it again."
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
