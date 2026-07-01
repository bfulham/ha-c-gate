"""Tests for C-Gate response and status parsing."""

from __future__ import annotations

import asyncio

import pytest

from client import (
    CgateEndpoint,
    CommandConnection,
    LightingEvent,
    MeasurementEvent,
    parse_level,
    parse_status_line,
)


def test_parse_lighting_status() -> None:
    event = parse_status_line(
        "#s# lighting ramp //THEBEND/253/62/93 128 #sourceunit=21"
    )
    assert isinstance(event, LightingEvent)
    assert event.level == 128
    assert event.source_unit == 21


def test_parse_measurement_status() -> None:
    event = parse_status_line(
        "measurement data //THEBEND/254/228/1/2 235 -1 0 #sourceunit=20"
    )
    assert isinstance(event, MeasurementEvent)
    assert event.value == 23.5


@pytest.mark.asyncio
async def test_command_multiline_response() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"201 Service ready: test\r\n")
        await writer.drain()
        command = await reader.readline()
        assert command.startswith(b"GET")
        writer.write(b"300-//TEST/254/56/1: name=Hall\r\n")
        writer.write(b"300 //TEST/254/56/1: level=200\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    endpoint = CgateEndpoint("127.0.0.1", port, 0, 0, 0, "TEST")
    connection = CommandConnection(endpoint)
    result = await connection.execute("GET //TEST/254/56/1 *")
    assert result.code == 300
    assert parse_level(result) == 200
    await connection.close()
    server.close()
    await server.wait_closed()
