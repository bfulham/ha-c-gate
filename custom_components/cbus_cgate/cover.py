"""C-Bus cover entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverEntity, CoverEntityFeature
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
        CbusCover(runtime, definition)
        for definition in runtime.group_definitions
        if definition.entity_type == "cover"
    )


class CbusCover(CbusGroupEntity, CoverEntity):
    """A position-based C-Bus group exposed as a cover."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
    )

    @property
    def current_cover_position(self) -> int | None:
        level = self.runtime.group_states[self.key].level
        return None if level is None else round(level * 100 / 255)

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        return None if position is None else position == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.runtime.set_group(self.key, 255)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.runtime.set_group(self.key, 0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        position = max(0, min(100, int(kwargs[ATTR_POSITION])))
        await self.runtime.set_group(self.key, round(position * 255 / 100))
