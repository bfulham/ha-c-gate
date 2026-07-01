"""Tests for startup group-state synchronisation."""

from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# The repository's lightweight unit-test environment does not install Home
# Assistant. Runtime only needs these names at import time for the tests below.
if "homeassistant" not in sys.modules:
    homeassistant = ModuleType("homeassistant")
    config_entries = ModuleType("homeassistant.config_entries")
    core = ModuleType("homeassistant.core")

    class ConfigEntry:
        """Import-time ConfigEntry placeholder."""

    class HomeAssistant:
        """Import-time HomeAssistant placeholder."""

    def callback(func):
        return func

    config_entries.ConfigEntry = ConfigEntry
    core.HomeAssistant = HomeAssistant
    core.callback = callback
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core

_PACKAGE_NAME = "cbus_cgate_runtime_tests"
_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "cbus_cgate"
package = ModuleType(_PACKAGE_NAME)
package.__path__ = [str(_PACKAGE_PATH)]
sys.modules[_PACKAGE_NAME] = package


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{_PACKAGE_NAME}.{name}",
        _PACKAGE_PATH / f"{name}.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


client_module = _load_module("client")
runtime_module = _load_module("runtime")
CgateCommandError = client_module.CgateCommandError
CgateEndpoint = client_module.CgateEndpoint
CommandResult = client_module.CommandResult
EndpointManager = runtime_module.EndpointManager
GroupDefinition = runtime_module.GroupDefinition
GroupState = runtime_module.GroupState
HubState = runtime_module.HubState


def _definition(group: int) -> GroupDefinition:
    return GroupDefinition(
        network={"address": 254, "name": "Main"},
        application={"address": 56, "name": "Lighting"},
        group={"address": group, "name": f"Group {group}"},
        entity_type="light",
    )


class FakeRuntime:
    """Small runtime surface used by EndpointManager bootstrap tests."""

    command_pool_size = 2

    def __init__(self) -> None:
        self.group_definitions = [_definition(1), _definition(2)]
        self.group_states: defaultdict[tuple[int, int, int], GroupState] = defaultdict(
            GroupState
        )
        self.hub_states: defaultdict[int, HubState] = defaultdict(HubState)

    def update_group(
        self,
        key: tuple[int, int, int],
        level: int,
        source_unit: int | None,
        *,
        optimistic: bool,
    ) -> None:
        del source_unit, optimistic
        self.group_states[key].level = level

    def set_hub_state(
        self,
        network: int,
        connected: bool,
        network_state: str | None,
        error: str | None,
    ) -> None:
        state = self.hub_states[network]
        state.connected = connected
        state.network_state = network_state
        state.last_error = error


class RetryingPool:
    """C-Gate model reports syncing once, then returns all wildcard levels."""

    def __init__(self) -> None:
        self.state_calls = 0
        self.commands: list[str] = []

    async def execute(self, command: str) -> CommandResult:
        self.commands.append(command)
        if command.startswith("GETSTATE "):
            return CommandResult(command, 200, ["200 OK"])
        if command == "GET //TEST/254 state":
            self.state_calls += 1
            state = "sync" if self.state_calls == 1 else "ok"
            return CommandResult(command, 300, [f"300 //TEST/254: state={state}"])
        if command == "GET //TEST/254/56/* level":
            return CommandResult(
                command,
                300,
                [
                    "300-//TEST/254/56/1: level=0",
                    "300 //TEST/254/56/2: level=200",
                ],
            )
        raise AssertionError(f"Unexpected command: {command}")


class NoWildcardPool:
    """C-Gate release rejects wildcard reads but supports group GET."""

    async def execute(self, command: str) -> CommandResult:
        if command.startswith("GETSTATE "):
            return CommandResult(command, 200, ["200 OK"])
        if command == "GET //TEST/254 state":
            return CommandResult(command, 300, ["300 //TEST/254: state=ok"])
        if command == "GET //TEST/254/56/* level":
            raise CgateCommandError(401, "Invalid group", command)
        if command == "GET //TEST/254/56/1 level":
            return CommandResult(command, 300, ["300 //TEST/254/56/1: level=10"])
        if command == "GET //TEST/254/56/2 level":
            return CommandResult(command, 300, ["300 //TEST/254/56/2: level=20"])
        raise AssertionError(f"Unexpected command: {command}")


def _manager(pool: Any) -> tuple[EndpointManager, FakeRuntime]:
    runtime = FakeRuntime()
    endpoint = CgateEndpoint("cgate", 20023, 20024, 20025, 20026, "TEST")
    manager = EndpointManager(
        runtime,  # type: ignore[arg-type]
        endpoint,
        [254],
        {254: {"enabled": True, "auto_open": True}},
    )
    manager.pool = pool
    return manager, runtime


@pytest.mark.asyncio
async def test_bootstrap_waits_for_network_model_then_fetches_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "_INITIAL_STATE_RETRY_INTERVAL", 0)
    pool = RetryingPool()
    manager, runtime = _manager(pool)

    await manager._bootstrap_levels(max_attempts=2)

    assert runtime.group_states[(254, 56, 1)].level == 0
    assert runtime.group_states[(254, 56, 2)].level == 200
    assert pool.state_calls == 2
    assert pool.commands.count("GET //TEST/254/56/* level") == 1


@pytest.mark.asyncio
async def test_bootstrap_falls_back_when_wildcard_get_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "_INITIAL_STATE_RETRY_INTERVAL", 0)
    manager, runtime = _manager(NoWildcardPool())

    await manager._bootstrap_levels(max_attempts=1)

    assert runtime.group_states[(254, 56, 1)].level == 10
    assert runtime.group_states[(254, 56, 2)].level == 20
    assert manager._wildcard_level_reads is False
