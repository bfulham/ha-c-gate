"""Tests for concise entity-ID suggestions."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PACKAGE_NAME = "cbus_cgate_entity_id_tests"
_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "cbus_cgate"


def _slugify(value: str) -> str:
    """Small test replacement for Home Assistant's slugify helper."""
    value = value.casefold().replace("-", " ")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value)).strip("_")


def _load_entity_module():
    homeassistant = ModuleType("homeassistant")
    helpers = ModuleType("homeassistant.helpers")
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    entity_helper = ModuleType("homeassistant.helpers.entity")
    util = ModuleType("homeassistant.util")

    class DeviceInfo(dict):
        """Minimal DeviceInfo replacement."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    class Entity:
        """Minimal Entity replacement."""

    device_registry.DeviceInfo = DeviceInfo
    entity_helper.Entity = Entity
    util.slugify = _slugify

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    sys.modules["homeassistant.helpers.entity"] = entity_helper
    sys.modules["homeassistant.util"] = util

    package = ModuleType(_PACKAGE_NAME)
    package.__path__ = [str(_PACKAGE_PATH)]
    sys.modules[_PACKAGE_NAME] = package

    const_spec = importlib.util.spec_from_file_location(
        f"{_PACKAGE_NAME}.const", _PACKAGE_PATH / "const.py"
    )
    assert const_spec is not None and const_spec.loader is not None
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    const_spec.loader.exec_module(const_module)

    runtime_module = ModuleType(f"{_PACKAGE_NAME}.runtime")
    runtime_module.CbusCgateRuntime = object
    runtime_module.GroupDefinition = object
    runtime_module.GroupKey = tuple[int, int, int]
    sys.modules[runtime_module.__name__] = runtime_module

    entity_spec = importlib.util.spec_from_file_location(
        f"{_PACKAGE_NAME}.entity", _PACKAGE_PATH / "entity.py"
    )
    assert entity_spec is not None and entity_spec.loader is not None
    entity_module = importlib.util.module_from_spec(entity_spec)
    sys.modules[entity_spec.name] = entity_module
    entity_spec.loader.exec_module(entity_module)
    return entity_module


def test_short_entity_id_uses_only_entity_name() -> None:
    entity_module = _load_entity_module()

    assert entity_module.short_entity_id("light", "Green Room") == "light.green_room"
    assert entity_module.short_entity_id("sensor", "Ambient Light (Lux)") == (
        "sensor.ambient_light_lux"
    )


def test_group_entity_keeps_address_unique_id_but_suggests_short_entity_id() -> None:
    entity_module = _load_entity_module()
    runtime = SimpleNamespace(installation_id="installation")
    definition = SimpleNamespace(
        network={"address": 250, "name": "DB-L1-1 Function Rooms"},
        application={"address": 56, "name": "DB-L1-1 Function Rooms"},
        group={"address": 12, "name": "Green Room"},
        entity_type="light",
    )

    entity = entity_module.CbusGroupEntity(runtime, definition)

    assert entity.entity_id == "light.green_room"
    assert entity._attr_unique_id == "installation:n250:a56:g12"
    assert entity._attr_name == "Green Room"
    assert entity._attr_has_entity_name is False
