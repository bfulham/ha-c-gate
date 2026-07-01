"""Shared entity and device-registry helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import DOMAIN
from .runtime import CbusCgateRuntime, GroupDefinition, GroupKey


def short_entity_id(domain: str, name: str) -> str:
    """Return an integration-suggested entity ID using only the entity name."""
    object_id = slugify(name) or "cbus_entity"
    return f"{domain}.{object_id}"


def server_identifier(runtime: CbusCgateRuntime) -> tuple[str, str]:
    return DOMAIN, f"{runtime.installation_id}:server"


def hub_identifier(runtime: CbusCgateRuntime, network: int) -> tuple[str, str]:
    return DOMAIN, f"{runtime.installation_id}:hub:{network}"


def application_identifier(
    runtime: CbusCgateRuntime, network: int, application: int
) -> tuple[str, str]:
    return (
        DOMAIN,
        f"{runtime.installation_id}:hub:{network}:application:{application}",
    )


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


def application_device_info(
    runtime: CbusCgateRuntime,
    network: dict[str, Any],
    application: dict[str, Any],
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={
            application_identifier(
                runtime, network["address"], application["address"]
            )
        },
        name=f"{network['name']} — {application['name']}",
        manufacturer="Schneider Electric / Clipsal",
        model=f"C-Bus application {application['address']}",
        via_device=hub_identifier(runtime, network["address"]),
    )


class CbusGroupEntity(Entity):
    """Base entity backed by a C-Bus group."""

    # Keep the visible entity name exactly as the Toolkit group name.
    # Device context remains available through the device association.
    _attr_has_entity_name = False

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
        self.entity_id = short_entity_id(definition.entity_type, self.group["name"])
        self._attr_device_info = application_device_info(
            runtime, self.network, self.application
        )
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
