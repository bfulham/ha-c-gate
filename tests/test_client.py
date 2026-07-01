"""Tests for C-Gate response and status parsing."""

from __future__ import annotations

import asyncio

import pytest

from client import (
    CgateConnectionError,
    CgateEndpoint,
    CommandConnection,
    LightingEvent,
    MeasurementEvent,
    async_fetch_project_xml,
    extract_dbgetxml,
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


def test_extract_dbgetxml() -> None:
    result = type(
        "Result",
        (),
        {
            "lines": [
                "343-Begin XML snippet",
                "347-<Installation>",
                "347-<Project><TagName>TEST</TagName></Project>",
                "347-</Installation>",
                "344 End XML snippet",
            ]
        },
    )()
    assert extract_dbgetxml(result) == (
        b"<Installation><Project><TagName>TEST</TagName></Project></Installation>"
    )


@pytest.mark.asyncio
async def test_fetch_project_xml_from_cgate() -> None:
    commands: list[str] = []
    expected = b"<Installation><Project><TagName>TEST</TagName></Project></Installation>"

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"201 Service ready: test\r\n")
        await writer.drain()
        while command_raw := await reader.readline():
            command = command_raw.decode().strip()
            commands.append(command)
            if command == "NOOP":
                writer.write(b"200 OK\r\n")
            elif command == "PROJECT USE TEST":
                writer.write(b"200 OK\r\n")
            elif command == "DBGETXML //TEST/":
                writer.write(b"343-Begin XML snippet\r\n")
                writer.write(b"347-<Installation><Project>\r\n")
                writer.write(b"347-<TagName>TEST</TagName></Project></Installation>\r\n")
                writer.write(b"344 End XML snippet\r\n")
            await writer.drain()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    endpoint = CgateEndpoint("127.0.0.1", port, 0, 0, 0, "TEST")
    try:
        assert await async_fetch_project_xml(endpoint) == expected
        assert commands == ["NOOP", "PROJECT USE TEST", "DBGETXML //TEST/"]
    finally:
        server.close()
        await server.wait_closed()


def test_extract_dbgetxml_rejects_incomplete_response() -> None:
    result = type(
        "Result",
        (),
        {"lines": ["343-Begin XML snippet", "347-<Installation>"]},
    )()
    with pytest.raises(CgateConnectionError, match="complete project XML"):
        extract_dbgetxml(result)


@pytest.mark.asyncio
async def test_fetch_project_xml_rejects_unsafe_project_name() -> None:
    endpoint = CgateEndpoint("127.0.0.1", 20023, 0, 0, 0, "TEST PROJECT")
    with pytest.raises(CgateConnectionError, match="Project names"):
        await async_fetch_project_xml(endpoint)
