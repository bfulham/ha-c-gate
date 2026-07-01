"""Asynchronous C-Gate TCP client.

C-Gate exposes a transactional command interface and push status interfaces. This
module deliberately keeps those paths separate: command responses can never block
status processing, and a small persistent command pool allows group calls from a
Home Assistant service to execute concurrently.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

_RESPONSE_RE = re.compile(r"^(\d{3})([- ])?(.*)$")
_LEVEL_RE = re.compile(r"\blevel=(\d+)\b", re.IGNORECASE)
_GROUP_LEVEL_RE = re.compile(
    r"(?://(?P<project>[^/\s:]+)/)?"
    r"(?P<network>\d+)/(?P<application>\d+)/(?P<group>\d+)"
    r"(?:\s|:).*?\blevel=(?P<level>\d+)\b",
    re.IGNORECASE,
)
_STATE_RE = re.compile(r"\bstate=([^\s]+)", re.IGNORECASE)
_SOURCE_RE = re.compile(r"#sourceunit=(\d+)", re.IGNORECASE)
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_XML_FRAGMENT_RE = re.compile(r"^(?:\[\d+\]\s+)?347-(.*)$")
_XML_BEGIN_RE = re.compile(r"^(?:\[\d+\]\s+)?343-Begin XML snippet$")
_XML_END_RE = re.compile(r"^(?:\[\d+\]\s+)?344(?:\s+.*)?$")
_MAX_PROJECT_XML_BYTES = 64 * 1024 * 1024

# Examples accepted:
# lighting on //PROJECT/253/62/93 #sourceunit=21
# #s# lighting ramp //PROJECT/253/62/93 128 #sourceunit=21
# # lighting off //PROJECT/253/62/93
_LIGHTING_RE = re.compile(
    r"^(?:#s#\s*)?(?:#\s*)?lighting\s+(on|off|ramp|terminateramp)\s+"
    r"//([^/\s]+)/([0-9]+)/([0-9]+)/([0-9]+)"
    r"(?:\s+([0-9]+)%?)?",
    re.IGNORECASE,
)
_MEASUREMENT_RE = re.compile(
    r"^(?:#s#\s*)?(?:#\s*)?measurement\s+data\s+"
    r"//([^/\s]+)/([0-9]+)/([0-9]+)/([0-9]+)/([0-9]+)\s+"
    r"(-?[0-9]+)\s+(-?[0-9]+)\s+(-?[0-9]+)",
    re.IGNORECASE,
)


class CgateError(Exception):
    """Base C-Gate error."""


class CgateConnectionError(CgateError):
    """Raised when a C-Gate connection is unavailable."""


class CgateCommandError(CgateError):
    """Raised when C-Gate rejects a command."""

    def __init__(self, code: int, message: str, command: str) -> None:
        super().__init__(f"C-Gate error {code} for {command}: {message}")
        self.code = code
        self.message = message
        self.command = command


@dataclass(slots=True, frozen=True)
class CgateEndpoint:
    """One C-Gate server endpoint."""

    host: str
    command_port: int
    event_port: int
    status_port: int
    config_port: int
    project: str


@dataclass(slots=True)
class CommandResult:
    """C-Gate response lines."""

    command: str
    code: int
    lines: list[str]

    @property
    def final(self) -> str:
        return self.lines[-1] if self.lines else ""


class CommandConnection:
    """One persistent transactional C-Gate command session."""

    def __init__(self, endpoint: CgateEndpoint, timeout: float = 20.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Open the command socket and validate the C-Gate greeting."""
        await self.close()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.endpoint.host,
                    self.endpoint.command_port,
                    limit=8 * 1024 * 1024,
                ),
                timeout=10,
            )
            greeting = await asyncio.wait_for(self._reader.readline(), timeout=10)
        except (TimeoutError, OSError) as err:
            await self.close()
            raise CgateConnectionError(
                f"Unable to connect to {self.endpoint.host}:{self.endpoint.command_port}: {err}"
            ) from err
        text = greeting.decode("utf-8", errors="replace").strip()
        if not text.startswith("201 "):
            await self.close()
            raise CgateConnectionError(f"Unexpected C-Gate greeting: {text or '<empty>'}")

    async def close(self) -> None:
        """Close the command socket."""
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def execute(self, command: str) -> CommandResult:
        """Execute one command and read through its final response line."""
        async with self._lock:
            if not self.connected:
                await self.connect()
            assert self._reader is not None
            assert self._writer is not None
            try:
                self._writer.write((command + "\r\n").encode("utf-8"))
                await self._writer.drain()
                lines: list[str] = []
                while True:
                    raw = await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
                    if not raw:
                        raise CgateConnectionError("C-Gate closed the command socket")
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not text:
                        continue
                    lines.append(text)
                    match = _RESPONSE_RE.match(text)
                    if match is None or match.group(2) == "-":
                        continue
                    code = int(match.group(1))
                    if code >= 400:
                        raise CgateCommandError(code, match.group(3).strip(), command)
                    return CommandResult(command, code, lines)
            except CgateConnectionError:
                await self.close()
                raise
            except (TimeoutError, OSError) as err:
                await self.close()
                raise CgateConnectionError(
                    f"C-Gate command connection failed while running {command}: {err}"
                ) from err


class CommandPool:
    """A bounded pool of persistent command sessions."""

    def __init__(self, endpoint: CgateEndpoint, size: int) -> None:
        self.endpoint = endpoint
        self.size = max(1, min(int(size), 8))
        self._clients = [CommandConnection(endpoint) for _ in range(self.size)]
        self._available: asyncio.Queue[CommandConnection] = asyncio.Queue()
        for client in self._clients:
            self._available.put_nowait(client)

    async def validate(self) -> None:
        """Validate at least one command session."""
        result = await self.execute("NOOP")
        if result.code != 200:
            raise CgateConnectionError(f"Unexpected NOOP response: {result.final}")

    async def execute(self, command: str) -> CommandResult:
        client = await self._available.get()
        try:
            return await client.execute(command)
        finally:
            self._available.put_nowait(client)

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in self._clients), return_exceptions=True)


@dataclass(slots=True, frozen=True)
class LightingEvent:
    project: str
    network: int
    application: int
    group: int
    level: int | None
    action: str
    source_unit: int | None
    raw: str


@dataclass(slots=True, frozen=True)
class MeasurementEvent:
    project: str
    network: int
    application: int
    device: int
    channel: int
    raw_value: int
    exponent: int
    unit_code: int
    source_unit: int | None
    raw: str

    @property
    def value(self) -> float:
        return self.raw_value * (10**self.exponent)


StatusEvent = LightingEvent | MeasurementEvent
StatusCallback = Callable[[StatusEvent], Awaitable[None] | None]


def parse_status_line(text: str) -> StatusEvent | None:
    """Parse one Status Change Port line."""
    cleaned = text.strip()
    lighting = _LIGHTING_RE.match(cleaned)
    if lighting:
        action = lighting.group(1).casefold()
        level_text = lighting.group(6)
        level: int | None
        if action == "on":
            level = 255
        elif action == "off":
            level = 0
        elif action == "ramp" and level_text is not None:
            level = max(0, min(255, int(level_text)))
        else:
            level = None
        source = _SOURCE_RE.search(cleaned)
        return LightingEvent(
            project=lighting.group(2),
            network=int(lighting.group(3)),
            application=int(lighting.group(4)),
            group=int(lighting.group(5)),
            level=level,
            action=action,
            source_unit=int(source.group(1)) if source else None,
            raw=cleaned,
        )

    measurement = _MEASUREMENT_RE.match(cleaned)
    if measurement:
        source = _SOURCE_RE.search(cleaned)
        return MeasurementEvent(
            project=measurement.group(1),
            network=int(measurement.group(2)),
            application=int(measurement.group(3)),
            device=int(measurement.group(4)),
            channel=int(measurement.group(5)),
            raw_value=int(measurement.group(6)),
            exponent=int(measurement.group(7)),
            unit_code=int(measurement.group(8)),
            source_unit=int(source.group(1)) if source else None,
            raw=cleaned,
        )
    return None


def extract_dbgetxml(result: CommandResult) -> bytes:
    """Extract and validate XML returned by C-Gate's DBGETXML command."""
    started = False
    ended = False
    fragments: list[str] = []
    total_bytes = 0

    for line in result.lines:
        if _XML_BEGIN_RE.match(line):
            started = True
            continue
        if _XML_END_RE.match(line):
            ended = True
            break
        fragment = _XML_FRAGMENT_RE.match(line)
        if fragment is None:
            continue
        if not started:
            raise CgateConnectionError("C-Gate sent project XML before the snippet header")
        content = fragment.group(1)
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > _MAX_PROJECT_XML_BYTES:
            raise CgateConnectionError("C-Gate project XML exceeds the 64 MiB safety limit")
        fragments.append(content)

    if not started or not ended or not fragments:
        raise CgateConnectionError("C-Gate did not return a complete project XML snippet")
    return "".join(fragments).encode("utf-8")


async def async_fetch_project_xml(endpoint: CgateEndpoint) -> bytes:
    """Fetch the loaded Toolkit project from C-Gate using DBGETXML."""
    if not _PROJECT_NAME_RE.fullmatch(endpoint.project):
        raise CgateConnectionError(
            "Project names may contain only letters, numbers, dots, underscores, and hyphens"
        )

    connection = CommandConnection(endpoint, timeout=60)
    try:
        await connection.connect()
        await connection.execute("NOOP")
        await connection.execute(f"PROJECT USE {endpoint.project}")
        result = await connection.execute(f"DBGETXML //{endpoint.project}/")
        return extract_dbgetxml(result)
    finally:
        await connection.close()


async def async_validate_endpoint(endpoint: CgateEndpoint) -> tuple[bool, str | None]:
    """Validate a server and project without requiring a live C-Bus network."""
    connection = CommandConnection(endpoint, timeout=10)
    try:
        await connection.connect()
        await connection.execute("NOOP")
        try:
            await connection.execute(f"PROJECT USE {endpoint.project}")
            await connection.execute(f"GET //{endpoint.project} state")
        except CgateCommandError as err:
            return (
                False,
                "C-Gate is reachable, but project "
                f"{endpoint.project} was not ready: {err.message}",
            )
        return True, None
    except CgateError as err:
        return False, str(err)
    finally:
        await connection.close()


def parse_level(result: CommandResult) -> int | None:
    """Extract a C-Bus level from a GET response."""
    for line in reversed(result.lines):
        match = _LEVEL_RE.search(line)
        if match:
            return max(0, min(255, int(match.group(1))))
    return None


def parse_group_levels(result: CommandResult) -> dict[tuple[int, int, int], int]:
    """Extract one or more addressed group levels from a C-Gate response.

    C-Gate supports wildcard reads such as ``GET //PROJECT/254/56/* level``.
    The response contains one addressed line per group, so parsing the address as
    well as the level lets startup synchronisation populate an entire application
    with a single command.
    """
    levels: dict[tuple[int, int, int], int] = {}
    for line in result.lines:
        match = _GROUP_LEVEL_RE.search(line)
        if match is None:
            continue
        key = (
            int(match.group("network")),
            int(match.group("application")),
            int(match.group("group")),
        )
        levels[key] = max(0, min(255, int(match.group("level"))))
    return levels


def parse_state(result: CommandResult) -> str | None:
    """Extract an object state from a GET response."""
    for line in reversed(result.lines):
        match = _STATE_RE.search(line)
        if match:
            return match.group(1).casefold()
    return None


class StatusStream:
    """Long-running C-Gate status stream with command-port fallback."""

    def __init__(
        self,
        endpoint: CgateEndpoint,
        callback: StatusCallback,
    ) -> None:
        self.endpoint = endpoint
        self.callback = callback
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.using_fallback = False

    async def connect(self) -> None:
        """Connect to SCP, or use a command session with status events enabled."""
        await self.close()
        if self.endpoint.status_port > 0:
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        self.endpoint.host,
                        self.endpoint.status_port,
                        limit=2 * 1024 * 1024,
                    ),
                    timeout=5,
                )
                self.using_fallback = False
                return
            except (TimeoutError, OSError):
                await self.close()
                _LOGGER.debug(
                    "C-Gate status port %s:%s unavailable; using command-event fallback",
                    self.endpoint.host,
                    self.endpoint.status_port,
                )

        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.endpoint.host,
                self.endpoint.command_port,
                limit=2 * 1024 * 1024,
            ),
            timeout=10,
        )
        greeting = await asyncio.wait_for(self._reader.readline(), timeout=10)
        if not greeting.decode("utf-8", errors="replace").startswith("201 "):
            raise CgateConnectionError("Status fallback received an invalid C-Gate greeting")
        self._writer.write(b"EVENT e0s1c0\r\n")
        await self._writer.drain()
        while True:
            line = await asyncio.wait_for(self._reader.readline(), timeout=10)
            if not line:
                raise CgateConnectionError("C-Gate closed the status fallback session")
            text = line.decode("utf-8", errors="replace").strip()
            match = _RESPONSE_RE.match(text)
            if match and match.group(2) != "-":
                if int(match.group(1)) >= 400:
                    raise CgateConnectionError(f"Unable to enable C-Gate status events: {text}")
                break
        self.using_fallback = True

    async def run(self) -> None:
        """Read and dispatch status lines until disconnected."""
        await self.connect()
        assert self._reader is not None
        while True:
            raw = await self._reader.readline()
            if not raw:
                raise CgateConnectionError("C-Gate status stream closed")
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            event = parse_status_line(text)
            if event is None:
                continue
            result = self.callback(event)
            if asyncio.iscoroutine(result):
                await result

    async def close(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
