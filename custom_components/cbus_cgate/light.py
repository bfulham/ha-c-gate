"""C-Bus light entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_TRANSITION, ColorMode, LightEntity
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
        CbusLight(runtime, definition)
        for definition in runtime.group_definitions
        if definition.entity_type == "light"
    )


class CbusLight(CbusGroupEntity, LightEntity):
    """A dimmable C-Bus lighting group."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool | None:
        level = self.runtime.group_states[self.key].level
        return None if level is None else level > 0

    @property
    def brightness(self) -> int | None:
        return self.runtime.group_states[self.key].level

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = int(kwargs.get(ATTR_BRIGHTNESS, 255))
        transition = kwargs.get(ATTR_TRANSITION)
        await self.runtime.set_group(self.key, brightness, transition)

    async def async_turn_off(self, **kwargs: Any) -> None:
        transition = kwargs.get(ATTR_TRANSITION)
        await self.runtime.set_group(self.key, 0, transition)
