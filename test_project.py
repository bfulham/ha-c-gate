"""C-Bus numeric and Measurement Application sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    UNIT_CODE_AMPS,
    UNIT_CODE_BOOLEAN,
    UNIT_CODE_CELSIUS,
    UNIT_CODE_HERTZ,
    UNIT_CODE_HOURS,
    UNIT_CODE_JOULES,
    UNIT_CODE_LITRES,
    UNIT_CODE_LITRES_PER_HOUR,
    UNIT_CODE_LITRES_PER_MIN,
    UNIT_CODE_LITRES_PER_SEC,
    UNIT_CODE_LUX,
    UNIT_CODE_MINUTES,
    UNIT_CODE_PERCENT,
    UNIT_CODE_SECONDS,
    UNIT_CODE_VOLTS,
    UNIT_CODE_WATT_HOURS,
    UNIT_CODE_WATTS,
)
from .entity import CbusGroupEntity, application_device_info
from .project import is_light_level_group_name, light_level_broadcast_to_lux
from .runtime import CbusCgateRuntime, GroupDefinition, MeasurementDefinition

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: CbusCgateRuntime = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    entities.extend(
        CbusGroupSensor(runtime, definition)
        for definition in runtime.group_definitions
        if definition.entity_type == "sensor"
    )
    entities.extend(
        CbusMeasurementSensor(runtime, definition)
        for definition in runtime.measurement_definitions
    )
    async_add_entities(entities)


class CbusGroupSensor(CbusGroupEntity, SensorEntity):
    """A group level exposed as a read-only numeric sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime: CbusCgateRuntime, definition: GroupDefinition) -> None:
        super().__init__(runtime, definition)
        self._is_light_level_broadcast = bool(self.group.get("light_level_broadcast"))
        if self._is_light_level_broadcast:
            self._attr_native_unit_of_measurement = LIGHT_LUX
            self._attr_device_class = SensorDeviceClass.ILLUMINANCE
            self._attr_icon = "mdi:brightness-6"
        else:
            self._attr_native_unit_of_measurement = PERCENTAGE
            if is_light_level_group_name(self.group["name"]):
                self._attr_icon = "mdi:brightness-percent"

    @property
    def native_value(self) -> float | None:
        level = self.runtime.group_states[self.key].level
        if level is None:
            return None
        if self._is_light_level_broadcast:
            return light_level_broadcast_to_lux(
                level, int(self.group.get("lux_per_level", 10))
            )
        return round(level * 100 / 255, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        if self._is_light_level_broadcast:
            attributes["raw_cbus_level"] = self.runtime.group_states[self.key].level
            attributes["lux_per_level"] = int(self.group.get("lux_per_level", 10))
        return attributes


_UNIT_INFO: dict[int, tuple[str | None, SensorDeviceClass | None]] = {
    UNIT_CODE_CELSIUS: (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    UNIT_CODE_AMPS: (UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
    UNIT_CODE_BOOLEAN: (None, None),
    UNIT_CODE_HERTZ: (UnitOfFrequency.HERTZ, SensorDeviceClass.FREQUENCY),
    UNIT_CODE_JOULES: (UnitOfEnergy.JOULE, SensorDeviceClass.ENERGY),
    UNIT_CODE_LITRES: (UnitOfVolume.LITERS, SensorDeviceClass.VOLUME),
    UNIT_CODE_LITRES_PER_HOUR: (
        UnitOfVolumeFlowRate.LITERS_PER_HOUR,
        SensorDeviceClass.VOLUME_FLOW_RATE,
    ),
    UNIT_CODE_LITRES_PER_MIN: (
        UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        SensorDeviceClass.VOLUME_FLOW_RATE,
    ),
    UNIT_CODE_LITRES_PER_SEC: (
        UnitOfVolumeFlowRate.LITERS_PER_SECOND,
        SensorDeviceClass.VOLUME_FLOW_RATE,
    ),
    UNIT_CODE_LUX: (LIGHT_LUX, SensorDeviceClass.ILLUMINANCE),
    UNIT_CODE_PERCENT: (PERCENTAGE, None),
    UNIT_CODE_SECONDS: (UnitOfTime.SECONDS, SensorDeviceClass.DURATION),
    UNIT_CODE_MINUTES: (UnitOfTime.MINUTES, SensorDeviceClass.DURATION),
    UNIT_CODE_HOURS: (UnitOfTime.HOURS, SensorDeviceClass.DURATION),
    UNIT_CODE_VOLTS: (UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    UNIT_CODE_WATT_HOURS: (UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY),
    UNIT_CODE_WATTS: (UnitOfPower.WATT, SensorDeviceClass.POWER),
}


class CbusMeasurementSensor(SensorEntity):
    """A C-Bus Measurement Application channel."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        runtime: CbusCgateRuntime,
        definition: MeasurementDefinition,
    ) -> None:
        self.runtime = runtime
        self.definition = definition
        self.network = definition.network
        self.application = definition.application
        self.measurement = definition.measurement
        self.key = (
            self.network["address"],
            self.application["address"],
            self.measurement["device"],
            self.measurement["channel"],
        )
        self._attr_unique_id = (
            f"{runtime.installation_id}:n{self.key[0]}:a{self.key[1]}:"
            f"d{self.key[2]}:c{self.key[3]}"
        )
        self._attr_name = self.measurement["name"]
        self._attr_device_info = application_device_info(
            runtime, self.network, self.application
        )
        self._unsubscribe = None

    @property
    def available(self) -> bool:
        return self.runtime.hub_states[self.key[0]].connected

    @property
    def native_value(self) -> float | None:
        return self.runtime.measurement_states[self.key].value

    @property
    def native_unit_of_measurement(self) -> str | None:
        unit_code = self.runtime.measurement_states[self.key].unit_code
        if unit_code is None:
            return self.measurement.get("units_type") or None
        return _UNIT_INFO.get(
            unit_code, (self.measurement.get("units_type") or None, None)
        )[0]

    @property
    def device_class(self) -> SensorDeviceClass | None:
        unit_code = self.runtime.measurement_states[self.key].unit_code
        return _UNIT_INFO.get(unit_code, (None, None))[1] if unit_code is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.runtime.measurement_states[self.key]
        return {
            "cbus_network": self.key[0],
            "cbus_application": self.key[1],
            "cbus_device": self.key[2],
            "cbus_channel": self.key[3],
            "raw_value": state.raw_value,
            "exponent": state.exponent,
            "unit_code": state.unit_code,
            "source_unit": state.source_unit,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = self.runtime.subscribe_measurement(
            self.key, self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await super().async_will_remove_from_hass()
