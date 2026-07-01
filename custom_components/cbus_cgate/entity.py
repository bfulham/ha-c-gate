"""Shared entity and device-registry helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .runtime import CbusCgateRuntime, GroupDefinition, GroupKey


def server_identifier(runtime: CbusCgateRuntime) -> tuple[str, str]:
    return DOMAIN, f"{runtime.installation_id}:server"


def hub_identifier(runtime: CbusCgateRuntime, network: int) -> tuple[str, str]:
    return DOMAIN, f"{runtime.installation_id}:hub:{network}"


def lights_identifier(runtime: CbusCgateRuntime, network: int) -> tuple[str, str]:
    return DOMAIN, f"{runtime.installation_id}:hub:{network}:lights"


def sensors_identifier(runtime: CbusCgateRuntime, network: int) -> tuple[str, str]:
    return DOMAIN, f"{runtime.installation_id}:hub:{network}:sensors"


def unit_identifier(runtime: CbusCgateRuntime, network: int, unit: int) -> tuple[str, str]:
    return DOMAIN, f"{runtime.installation_id}:hub:{network}:unit:{unit}"


def server_device_info(runtime: CbusCgateRuntime) -> DeviceInfo:
    return DeviceInfo(
        identifiers={server_identifier(runtime)},
        name=f"C-Gate {runtime.project['project_name']}",
        manufacturer="Schneider Electric / Clipsal",
        model="C-Gate project",
        sw_version=runtime.project.get("db_version") or None,
    )


def hub_device_info(runtime: CbusCgateRuntime, network: dict[str, Any]) -> DeviceInfo:
    interface = network.get("interface", {})
    return DeviceInfo(
        identifiers={hub_identifier(runtime, network["address"])},
        name=network["name"],
        manufacturer="Schneider Electric / Clipsal",
        model=f"C-Bus {interface.get('type') or 'network'} hub",
        via_device=server_identifier(runtime),
    )


def lights_device_info(runtime: CbusCgateRuntime, network: dict[str, Any]) -> DeviceInfo:
    return DeviceInfo(
        identifiers={lights_identifier(runtime, network["address"])},
        name=f"{network['name']} Lights",
        manufacturer="Schneider Electric / Clipsal",
        model="C-Bus lighting groups",
        via_device=hub_identifier(runtime, network["address"]),
    )


def sensors_device_info(runtime: CbusCgateRuntime, network: dict[str, Any]) -> DeviceInfo:
    return DeviceInfo(
        identifiers={sensors_identifier(runtime, network["address"])},
        name=f"{network['name']} Sensors",
        manufacturer="Schneider Electric / Clipsal",
        model="C-Bus sensor groups",
        via_device=hub_identifier(runtime, network["address"]),
    )


def unit_device_info(
    runtime: CbusCgateRuntime,
    network: dict[str, Any],
    unit: dict[str, Any],
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={unit_identifier(runtime, network["address"], unit["address"])},
        name=unit["name"],
        manufacturer="Schneider Electric / Clipsal",
        model=unit.get("catalog_number") or unit.get("unit_type") or "C-Bus sensor",
        sw_version=unit.get("firmware_version") or None,
        via_device=hub_identifier(runtime, network["address"]),
    )


class CbusGroupEntity(Entity):
    """Base entity backed by a C-Bus group."""

    _attr_has_entity_name = True

    def __init__(self, runtime: CbusCgateRuntime, definition: GroupDefinition) -> None:
        self.runtime = runtime
        self.definition = definition
        self.network = definition.network
        self.application = definition.application
        self.group = definition.group
        self.key: GroupKey = (
            self.network["address"],
            self.application["address"],
            self.group["address"],
        )
        self._attr_unique_id = (
            f"{runtime.installation_id}:n{self.key[0]}:a{self.key[1]}:g{self.key[2]}"
        )
        self._attr_name = self.group["name"]
        if definition.entity_type in {"light", "switch", "cover"}:
            self._attr_device_info = lights_device_info(runtime, self.network)
        else:
            self._attr_device_info = sensors_device_info(runtime, self.network)
        self._unsubscribe = None

    @property
    def available(self) -> bool:
        return self.runtime.hub_states[self.key[0]].connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.runtime.group_states[self.key]
        return {
            "cbus_network": self.key[0],
            "cbus_application": self.key[1],
            "cbus_group": self.key[2],
            "cbus_application_name": self.application["name"],
            "source_unit": state.source_unit,
            "optimistic": state.optimistic,
            "last_error": state.last_error,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = self.runtime.subscribe_group(self.key, self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await super().async_will_remove_from_hass()
