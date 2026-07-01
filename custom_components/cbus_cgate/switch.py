"""C-Bus switch entities."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import CbusGroupEntity
from .runtime import CbusCgateRuntime

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: CbusCgateRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CbusSwitch(runtime, definition)
        for definition in runtime.group_definitions
        if definition.entity_type == "switch"
    )


class CbusSwitch(CbusGroupEntity, SwitchEntity):
    """A C-Bus relay or binary group."""

    @property
    def is_on(self) -> bool | None:
        level = self.runtime.group_states[self.key].level
        return None if level is None else level > 0

    async def async_turn_on(self, **kwargs) -> None:
        await self.runtime.set_group(self.key, 255)

    async def async_turn_off(self, **kwargs) -> None:
        await self.runtime.set_group(self.key, 0)
