"""Diagnostics for C-Bus C-Gate."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import CONF_HOST, CONF_HUB_CONNECTIONS, DOMAIN
from .runtime import CbusCgateRuntime

TO_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    runtime: CbusCgateRuntime = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "project": {
            "name": runtime.project["project_name"],
            "db_version": runtime.project.get("db_version"),
            "source_format": runtime.project.get("source_format"),
            "networks": [
                {
                    "address": network["address"],
                    "name": network["name"],
                    "interface": network["interface"],
                    "application_count": len(network["applications"]),
                    "unit_count": network.get("unit_count", 0),
                }
                for network in runtime.project["networks"]
            ],
        },
        "hubs": {
            str(network): {
                "connected": state.connected,
                "network_state": state.network_state,
                "last_error": state.last_error,
                "last_event": state.last_event.isoformat() if state.last_event else None,
                "command_count": state.command_count,
                "failed_command_count": state.failed_command_count,
            }
            for network, state in runtime.hub_states.items()
        },
        "entities": {
            "groups": len(runtime.group_definitions),
            "measurements": len(runtime.measurement_definitions),
            "motion_units": len(runtime.motion_definitions),
        },
        "endpoint_count": len(runtime.managers),
    }
