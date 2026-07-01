"""Regression tests for unprefixed Home Assistant entity names."""

from __future__ import annotations

import ast
from pathlib import Path

_COMPONENT = Path(__file__).parents[1] / "custom_components" / "cbus_cgate"


def _class_bool_assignment(filename: str, class_name: str, attribute: str) -> bool:
    """Read a literal boolean class assignment without importing Home Assistant."""
    tree = ast.parse((_COMPONENT / filename).read_text())
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id == attribute:
                assert isinstance(statement.value, ast.Constant)
                return bool(statement.value.value)
    raise AssertionError(f"{class_name}.{attribute} was not assigned in {filename}")


def test_all_entity_families_disable_device_name_prefixes() -> None:
    """The associated device must not be prepended to visible entity names."""
    assert not _class_bool_assignment("entity.py", "CbusGroupEntity", "_attr_has_entity_name")
    assert not _class_bool_assignment(
        "sensor.py", "CbusMeasurementSensor", "_attr_has_entity_name"
    )
    assert not _class_bool_assignment(
        "binary_sensor.py", "CbusHubConnectivity", "_attr_has_entity_name"
    )
    assert not _class_bool_assignment("button.py", "_CbusHubButton", "_attr_has_entity_name")
