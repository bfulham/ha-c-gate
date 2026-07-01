"""C-Gate maintenance buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import hub_device_info, short_entity_id
from .runtime import CbusCgateRuntime

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: CbusCgateRuntime = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []
    for network in runtime.project["networks"]:
        entities.append(CbusReopenButton(runtime, network))
        entities.append(CbusResyncButton(runtime, network))
    async_add_entities(entities)


class _CbusHubButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime: CbusCgateRuntime, network: dict) -> None:
        self.runtime = runtime
        self.network = network
        self.network_address = network["address"]
        self._attr_device_info = hub_device_info(runtime, network)

    @property
    def available(self) -> bool:
        return self.network_address in self.runtime.manager_by_network


class CbusReopenButton(_CbusHubButton):
    _attr_name = "Reopen network"

    def __init__(self, runtime: CbusCgateRuntime, network: dict) -> None:
        super().__init__(runtime, network)
        self._attr_unique_id = (
            f"{runtime.installation_id}:n{self.network_address}:reopen"
        )
        self.entity_id = short_entity_id("button", self._attr_name)

    async def async_press(self) -> None:
        await self.runtime.reopen_network(self.network_address)


class CbusResyncButton(_CbusHubButton):
    _attr_name = "Resynchronise state"

    def __init__(self, runtime: CbusCgateRuntime, network: dict) -> None:
        super().__init__(runtime, network)
        self._attr_unique_id = (
            f"{runtime.installation_id}:n{self.network_address}:resync"
        )
        self.entity_id = short_entity_id("button", self._attr_name)

    async def async_press(self) -> None:
        await self.runtime.resync_network(self.network_address)
