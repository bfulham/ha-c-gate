"""Runtime model and C-Gate connection management."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .client import (
    CgateCommandError,
    CgateConnectionError,
    CgateEndpoint,
    CommandPool,
    LightingEvent,
    MeasurementEvent,
    StatusEvent,
    StatusStream,
    parse_level,
    parse_state,
)
from .const import (
    CONF_APPLICATION_MAPPINGS,
    CONF_APPLICATION_OVERRIDES,
    CONF_AUTO_OPEN,
    CONF_COMMAND_POOL_SIZE,
    CONF_ENABLED,
    CONF_GROUP_OVERRIDES,
    CONF_HUB_CONNECTIONS,
    CONF_INCLUDE_INTERNAL,
    CONF_INSTALLATION_ID,
    CONF_OPTIMISTIC,
    CONF_PROJECT_NAME,
    DEFAULT_AUTO_OPEN,
    DEFAULT_COMMAND_POOL_SIZE,
    DEFAULT_INCLUDE_INTERNAL,
    DEFAULT_KEEPALIVE,
    DEFAULT_OPTIMISTIC,
    DEFAULT_RECONNECT_MAX,
    DEFAULT_RECONNECT_MIN,
    EVENT_CBUS,
    TYPE_AUTO,
    TYPE_IGNORE,
)

from .project import classify_group

_LOGGER = logging.getLogger(__name__)

GroupKey = tuple[int, int, int]
MeasurementKey = tuple[int, int, int, int]


@dataclass(slots=True)
class GroupState:
    """Live state of one C-Bus group."""

    level: int | None = None
    source_unit: int | None = None
    updated_at: datetime | None = None
    optimistic: bool = False
    last_error: str | None = None


@dataclass(slots=True)
class MeasurementState:
    """Live state of one C-Bus measurement channel."""

    value: float | None = None
    raw_value: int | None = None
    exponent: int | None = None
    unit_code: int | None = None
    source_unit: int | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class HubState:
    """Connection and network state for one C-Bus hub."""

    connected: bool = False
    network_state: str | None = None
    last_error: str | None = None
    last_event: datetime | None = None
    command_count: int = 0
    failed_command_count: int = 0


@dataclass(slots=True, frozen=True)
class GroupDefinition:
    network: dict[str, Any]
    application: dict[str, Any]
    group: dict[str, Any]
    entity_type: str


@dataclass(slots=True, frozen=True)
class MeasurementDefinition:
    network: dict[str, Any]
    application: dict[str, Any]
    measurement: dict[str, Any]


class EndpointManager:
    """Manage one unique C-Gate endpoint shared by one or more hubs."""

    def __init__(
        self,
        runtime: "CbusCgateRuntime",
        endpoint: CgateEndpoint,
        networks: list[int],
        connection_settings: dict[int, dict[str, Any]],
    ) -> None:
        self.runtime = runtime
        self.endpoint = endpoint
        self.networks = networks
        self.connection_settings = connection_settings
        self.pool = CommandPool(
            endpoint,
            runtime.command_pool_size,
        )
        self.status_stream = StatusStream(endpoint, self._handle_status)
        self._task: asyncio.Task[None] | None = None
        self._bootstrap_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._connected = False
        self.last_error: str | None = None
        self.status_fallback = False

    async def start(self) -> None:
        self._task = asyncio.create_task(
            self._run(),
            name=f"cbus-cgate-{self.endpoint.host}-{self.endpoint.command_port}",
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._bootstrap_task is not None:
            self._bootstrap_task.cancel()
            try:
                await self._bootstrap_task
            except asyncio.CancelledError:
                pass
            self._bootstrap_task = None
        await self.status_stream.close()
        await self.pool.close()
        self._set_connected(False, "Stopped")

    async def _run(self) -> None:
        delay = DEFAULT_RECONNECT_MIN
        while not self._stopping.is_set():
            status_task: asyncio.Task[None] | None = None
            health_task: asyncio.Task[None] | None = None
            try:
                await self.pool.validate()
                await self._prepare_project()
                self._connected = True
                self.last_error = None
                self._set_connected(True, None)
                status_task = asyncio.create_task(self.status_stream.run())
                health_task = asyncio.create_task(self._health_loop())
                delay = DEFAULT_RECONNECT_MIN
                await asyncio.sleep(0)
                self.status_fallback = self.status_stream.using_fallback
                await asyncio.gather(status_task, health_task)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - connection supervisor boundary
                self.last_error = str(err)
                _LOGGER.warning(
                    "C-Gate endpoint %s:%s disconnected: %s",
                    self.endpoint.host,
                    self.endpoint.command_port,
                    err,
                )
                self._set_connected(False, str(err))
            finally:
                active_tasks = [task for task in (status_task, health_task) if task is not None]
                for task in active_tasks:
                    task.cancel()
                if active_tasks:
                    await asyncio.gather(*active_tasks, return_exceptions=True)
                if self._bootstrap_task is not None:
                    self._bootstrap_task.cancel()
                    try:
                        await self._bootstrap_task
                    except asyncio.CancelledError:
                        pass
                    self._bootstrap_task = None
                await self.status_stream.close()
                await self.pool.close()
                self._connected = False
            if not self._stopping.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, DEFAULT_RECONNECT_MAX)

    async def _prepare_project(self) -> None:
        """Load/start the project and open the configured networks."""
        await self.pool.execute(f"PROJECT USE {self.endpoint.project}")
        try:
            await self.pool.execute(f"PROJECT START {self.endpoint.project}")
        except CgateCommandError as err:
            # Already-started projects and some C-Gate releases return an error here.
            _LOGGER.debug("PROJECT START returned %s", err)
        await self.pool.execute(f"NET LOAD DB {self.endpoint.project}")

        for network in self.networks:
            settings = self.connection_settings[network]
            if not settings.get(CONF_ENABLED, True):
                continue
            if settings.get(CONF_AUTO_OPEN, DEFAULT_AUTO_OPEN):
                try:
                    await self.pool.execute(
                        f"NET OPEN //{self.endpoint.project}/{network}"
                    )
                except (CgateCommandError, CgateConnectionError, OSError, asyncio.TimeoutError) as err:
                    self.runtime.set_hub_state(
                        network,
                        connected=False,
                        network_state="closed",
                        error=str(err),
                    )
            await self.refresh_hub_state(network)

        if self._bootstrap_task is not None:
            self._bootstrap_task.cancel()
        self._bootstrap_task = asyncio.create_task(
            self._bootstrap_levels(),
            name=f"cbus-cgate-bootstrap-{self.endpoint.host}-{self.endpoint.command_port}",
        )

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(DEFAULT_KEEPALIVE)
            await self.pool.execute("NOOP")
            for network in self.networks:
                if self.connection_settings[network].get(CONF_ENABLED, True):
                    await self.refresh_hub_state(network)

    async def refresh_hub_state(self, network: int) -> None:
        try:
            result = await self.pool.execute(
                f"GET //{self.endpoint.project}/{network} state"
            )
            state = parse_state(result) or "unknown"
            available = state in {"ok", "running", "connected", "sync"}
            self.runtime.set_hub_state(
                network,
                connected=available,
                network_state=state,
                error=None if available else self.runtime.hub_states[network].last_error,
            )
        except (CgateCommandError, CgateConnectionError, OSError, asyncio.TimeoutError) as err:
            self.runtime.set_hub_state(
                network,
                connected=False,
                network_state="unavailable",
                error=str(err),
            )

    async def reopen_network(self, network: int) -> None:
        await self.pool.execute(f"NET OPEN //{self.endpoint.project}/{network}")
        await self.refresh_hub_state(network)

    async def resync_network(self, network: int) -> None:
        try:
            await self.pool.execute(f"GETSTATE //{self.endpoint.project}/{network}")
        finally:
            await self._bootstrap_levels(network_filter=network)

    async def _bootstrap_levels(self, network_filter: int | None = None) -> None:
        """Fetch current levels in parallel without blocking setup."""
        definitions = [
            definition
            for definition in self.runtime.group_definitions
            if definition.network["address"] in self.networks
            and (network_filter is None or definition.network["address"] == network_filter)
        ]

        semaphore = asyncio.Semaphore(max(1, self.runtime.command_pool_size * 2))

        async def fetch(definition: GroupDefinition) -> None:
            key = (
                definition.network["address"],
                definition.application["address"],
                definition.group["address"],
            )
            async with semaphore:
                try:
                    result = await self.pool.execute(
                        "GET //"
                        f"{self.endpoint.project}/{key[0]}/{key[1]}/{key[2]} level"
                    )
                    level = parse_level(result)
                    if level is not None:
                        self.runtime.update_group(key, level, None, optimistic=False)
                except (CgateCommandError, CgateConnectionError, OSError, asyncio.TimeoutError):
                    return

        await asyncio.gather(*(fetch(item) for item in definitions), return_exceptions=True)

    async def set_group(
        self,
        key: GroupKey,
        level: int,
        transition: float | None = None,
    ) -> None:
        address = f"//{self.endpoint.project}/{key[0]}/{key[1]}/{key[2]}"
        if level <= 0 and not transition:
            command = f"OFF {address}"
        elif level >= 255 and not transition:
            command = f"ON {address}"
        else:
            seconds = max(0, min(1020, int(round(transition or 0))))
            command = f"RAMP {address} {max(0, min(255, int(level)))} {seconds}s"
        self.runtime.hub_states[key[0]].command_count += 1
        try:
            await self.pool.execute(command)
        except Exception:  # noqa: BLE001
            self.runtime.hub_states[key[0]].failed_command_count += 1
            raise

    async def _handle_status(self, event: StatusEvent) -> None:
        if event.project.casefold() != self.endpoint.project.casefold():
            return
        if event.network not in self.networks:
            return
        self.runtime.hub_states[event.network].last_event = datetime.now(timezone.utc)
        if isinstance(event, LightingEvent):
            if event.level is not None:
                self.runtime.update_group(
                    (event.network, event.application, event.group),
                    event.level,
                    event.source_unit,
                    optimistic=False,
                )
        else:
            self.runtime.update_measurement(event)
        self.runtime.hass.bus.async_fire(
            EVENT_CBUS,
            {
                "project": event.project,
                "network": event.network,
                "raw": event.raw,
            },
        )

    def _set_connected(self, connected: bool, error: str | None) -> None:
        for network in self.networks:
            if not self.connection_settings[network].get(CONF_ENABLED, True):
                self.runtime.set_hub_state(
                    network,
                    connected=False,
                    network_state="disabled",
                    error=None,
                )
            elif not connected:
                self.runtime.set_hub_state(
                    network,
                    connected=False,
                    network_state="disconnected",
                    error=error,
                )


class CbusCgateRuntime:
    """One Home Assistant config entry backed by an imported project."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        project: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.project = project
        self.installation_id: str = entry.data[CONF_INSTALLATION_ID]
        self.command_pool_size = int(
            entry.options.get(
                CONF_COMMAND_POOL_SIZE,
                entry.data.get(CONF_COMMAND_POOL_SIZE, DEFAULT_COMMAND_POOL_SIZE),
            )
        )
        self.optimistic = bool(
            entry.options.get(
                CONF_OPTIMISTIC,
                entry.data.get(CONF_OPTIMISTIC, DEFAULT_OPTIMISTIC),
            )
        )
        self.include_internal = bool(
            entry.options.get(
                CONF_INCLUDE_INTERNAL,
                entry.data.get(CONF_INCLUDE_INTERNAL, DEFAULT_INCLUDE_INTERNAL),
            )
        )
        self.group_states: defaultdict[GroupKey, GroupState] = defaultdict(GroupState)
        self.measurement_states: defaultdict[MeasurementKey, MeasurementState] = defaultdict(MeasurementState)
        self.hub_states: defaultdict[int, HubState] = defaultdict(HubState)
        self._group_callbacks: defaultdict[GroupKey, list[Callable[[], None]]] = defaultdict(list)
        self._measurement_callbacks: defaultdict[MeasurementKey, list[Callable[[], None]]] = defaultdict(list)
        self._hub_callbacks: defaultdict[int, list[Callable[[], None]]] = defaultdict(list)
        self.managers: list[EndpointManager] = []
        self.manager_by_network: dict[int, EndpointManager] = {}

        self.application_mappings: dict[str, str] = {
            **project.get("default_application_mappings", {}),
            **entry.data.get(CONF_APPLICATION_MAPPINGS, {}),
        }
        self.application_mappings.update(entry.options.get(CONF_APPLICATION_MAPPINGS, {}))
        self.application_overrides: dict[str, str] = {
            **entry.data.get(CONF_APPLICATION_OVERRIDES, {}),
            **entry.options.get(CONF_APPLICATION_OVERRIDES, {}),
        }
        self.group_overrides: dict[str, str] = {
            **entry.data.get(CONF_GROUP_OVERRIDES, {}),
            **entry.options.get(CONF_GROUP_OVERRIDES, {}),
        }

        self.group_definitions = self._build_group_definitions()
        self.measurement_definitions = self._build_measurement_definitions()
        active_application_keys = {
            (definition.network["address"], definition.application["address"])
            for definition in (*self.group_definitions, *self.measurement_definitions)
        }
        self.application_definitions = [
            (network, application)
            for network in self.project["networks"]
            for application in network["applications"]
            if (network["address"], application["address"]) in active_application_keys
        ]

    def _hub_connections(self) -> dict[int, dict[str, Any]]:
        defaults = self.entry.data[CONF_HUB_CONNECTIONS]
        overrides = self.entry.options.get(CONF_HUB_CONNECTIONS, {})
        result: dict[int, dict[str, Any]] = {}
        for network in self.project["networks"]:
            key = str(network["address"])
            result[network["address"]] = {
                **defaults.get(key, {}),
                **overrides.get(key, {}),
            }
        return result

    def _build_group_definitions(self) -> list[GroupDefinition]:
        result: list[GroupDefinition] = []
        for network in self.project["networks"]:
            for application in network["applications"]:
                for group in application["groups"]:
                    if group["internal"] and not self.include_internal:
                        continue
                    key = (network["address"], application["address"], group["address"])
                    entity_type = self.effective_entity_type(key, group)
                    if entity_type == TYPE_IGNORE:
                        continue
                    result.append(GroupDefinition(network, application, group, entity_type))
        return result

    def _build_measurement_definitions(self) -> list[MeasurementDefinition]:
        result: list[MeasurementDefinition] = []
        for network in self.project["networks"]:
            for application in network["applications"]:
                mapping = self.effective_application_type(
                    network["address"], application["address"]
                )
                if mapping != "sensor":
                    continue
                for measurement in application.get("measurements", []):
                    result.append(MeasurementDefinition(network, application, measurement))
        return result

    def effective_application_type(self, network: int, application: int) -> str:
        return self.application_overrides.get(
            f"{network}:{application}",
            self.application_mappings.get(str(application), TYPE_IGNORE),
        )

    def effective_entity_type(self, key: GroupKey, group: dict[str, Any]) -> str:
        override = self.group_overrides.get(f"{key[0]}:{key[1]}:{key[2]}")
        if override:
            return override
        mapping = self.effective_application_type(key[0], key[1])
        if mapping == TYPE_AUTO:
            return classify_group(
                group["name"],
                bool(group.get("relay")),
                bool(group.get("output_assigned")),
            )
        return mapping

    async def start(self) -> None:
        connections = self._hub_connections()
        grouped: defaultdict[CgateEndpoint, list[int]] = defaultdict(list)
        for network, settings in connections.items():
            if not settings.get(CONF_ENABLED, True):
                self.set_hub_state(network, False, "disabled", None)
                continue
            endpoint = CgateEndpoint(
                host=str(settings["host"]),
                command_port=int(settings["command_port"]),
                event_port=int(settings["event_port"]),
                status_port=int(settings["status_port"]),
                config_port=int(settings["config_port"]),
                project=self.entry.data[CONF_PROJECT_NAME],
            )
            grouped[endpoint].append(network)

        for endpoint, networks in grouped.items():
            manager = EndpointManager(self, endpoint, networks, connections)
            self.managers.append(manager)
            for network in networks:
                self.manager_by_network[network] = manager
            await manager.start()

    async def stop(self) -> None:
        await asyncio.gather(*(manager.stop() for manager in self.managers), return_exceptions=True)
        self.managers.clear()
        self.manager_by_network.clear()

    async def set_group(
        self,
        key: GroupKey,
        level: int,
        transition: float | None = None,
    ) -> None:
        manager = self.manager_by_network.get(key[0])
        if manager is None:
            raise CgateConnectionError(f"No C-Gate endpoint is configured for network {key[0]}")
        previous = self.group_states[key].level
        if self.optimistic:
            self.update_group(key, level, None, optimistic=True)
        try:
            await manager.set_group(key, level, transition)
        except Exception as err:
            self.group_states[key].last_error = str(err)
            if self.optimistic and previous is not None:
                self.update_group(key, previous, None, optimistic=False)
            raise

    async def reopen_network(self, network: int) -> None:
        manager = self.manager_by_network[network]
        await manager.reopen_network(network)

    async def resync_network(self, network: int) -> None:
        manager = self.manager_by_network[network]
        await manager.resync_network(network)

    def update_group(
        self,
        key: GroupKey,
        level: int,
        source_unit: int | None,
        *,
        optimistic: bool,
    ) -> None:
        state = self.group_states[key]
        state.level = max(0, min(255, int(level)))
        state.source_unit = source_unit
        state.updated_at = datetime.now(timezone.utc)
        state.optimistic = optimistic
        state.last_error = None
        for callback_fn in tuple(self._group_callbacks[key]):
            callback_fn()

    def update_measurement(self, event: MeasurementEvent) -> None:
        key = (event.network, event.application, event.device, event.channel)
        state = self.measurement_states[key]
        state.value = event.value
        state.raw_value = event.raw_value
        state.exponent = event.exponent
        state.unit_code = event.unit_code
        state.source_unit = event.source_unit
        state.updated_at = datetime.now(timezone.utc)
        for callback_fn in tuple(self._measurement_callbacks[key]):
            callback_fn()

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
        for callback_fn in tuple(self._hub_callbacks[network]):
            callback_fn()

    @callback
    def subscribe_group(self, key: GroupKey, cb: Callable[[], None]) -> Callable[[], None]:
        self._group_callbacks[key].append(cb)
        return lambda: self._group_callbacks[key].remove(cb)

    @callback
    def subscribe_measurement(
        self, key: MeasurementKey, cb: Callable[[], None]
    ) -> Callable[[], None]:
        self._measurement_callbacks[key].append(cb)
        return lambda: self._measurement_callbacks[key].remove(cb)

    @callback
    def subscribe_hub(self, network: int, cb: Callable[[], None]) -> Callable[[], None]:
        self._hub_callbacks[network].append(cb)
        return lambda: self._hub_callbacks[network].remove(cb)
