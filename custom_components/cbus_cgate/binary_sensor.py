"""C-Bus binary sensor and hub connectivity entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import CbusGroupEntity, hub_device_info, unit_device_info
from .runtime import CbusCgateRuntime, MotionDefinition

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: CbusCgateRuntime = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    entities.extend(
        CbusGroupBinarySensor(runtime, definition)
        for definition in runtime.group_definitions
        if definition.entity_type == "binary_sensor"
    )
    entities.extend(CbusMotionSensor(runtime, definition) for definition in runtime.motion_definitions)
    entities.extend(
        CbusHubConnectivity(runtime, network) for network in runtime.project["networks"]
    )
    async_add_entities(entities)


class CbusGroupBinarySensor(CbusGroupEntity, BinarySensorEntity):
    """A generic C-Bus group exposed as a binary sensor."""

    @property
    def is_on(self) -> bool | None:
        level = self.runtime.group_states[self.key].level
        return None if level is None else level > 0


class CbusMotionSensor(BinarySensorEntity):
    """A physical C-Bus multisensor motion entity."""

    _attr_has_entity_name = True
    _attr_name = "Motion"
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, runtime: CbusCgateRuntime, definition: MotionDefinition) -> None:
        self.runtime = runtime
        self.definition = definition
        self.network = definition.network
        self.unit = definition.unit
        self.key = (self.network["address"], self.unit["address"])
        self._attr_unique_id = (
            f"{runtime.installation_id}:n{self.key[0]}:u{self.key[1]}:motion"
        )
        self._attr_device_info = unit_device_info(runtime, self.network, self.unit)
        self._unsubscribe = None

    @property
    def is_on(self) -> bool:
        return self.runtime.motion_states[self.key]

    @property
    def available(self) -> bool:
        return self.runtime.hub_states[self.key[0]].connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "cbus_network": self.key[0],
            "cbus_unit": self.key[1],
            "source_groups": [f"{app}/{group}" for app, group in self.definition.mappings],
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = self.runtime.subscribe_motion(self.key, self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await super().async_will_remove_from_hass()


class CbusHubConnectivity(BinarySensorEntity):
    """Connection state for one C-Bus network hub."""

    _attr_has_entity_name = True
    _attr_name = "C-Gate connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: CbusCgateRuntime, network: dict[str, Any]) -> None:
        self.runtime = runtime
        self.network = network
        self.network_address = network["address"]
        self._attr_unique_id = (
            f"{runtime.installation_id}:n{self.network_address}:connectivity"
        )
        self._attr_device_info = hub_device_info(runtime, network)
        self._unsubscribe = None

    @property
    def is_on(self) -> bool:
        return self.runtime.hub_states[self.network_address].connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.runtime.hub_states[self.network_address]
        manager = self.runtime.manager_by_network.get(self.network_address)
        return {
            "network_address": self.network_address,
            "network_state": state.network_state,
            "last_error": state.last_error,
            "last_event": state.last_event.isoformat() if state.last_event else None,
            "commands": state.command_count,
            "failed_commands": state.failed_command_count,
            "status_via_command_fallback": manager.status_stream.using_fallback if manager else None,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = self.runtime.subscribe_hub(
            self.network_address, self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await super().async_will_remove_from_hass()
