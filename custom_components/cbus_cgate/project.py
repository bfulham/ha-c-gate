"""Toolkit project import and normalisation.

The importer supports legacy Toolkit CBZ/XML projects and modern Toolkit/C-Gate 3
CBZ/SQLite projects. The normalised model is intentionally independent of the
C-Gate runtime so setup and project updates can succeed while C-Gate is offline.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import io
import re
import sqlite3
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 64
ILLUMINANCE_UNIT_TYPES = {"SENPIRIB", "SENLL"}
ILLUMINANCE_CATALOG_NUMBERS = {"5753L", "5753PEIRL", "5031PE"}
MOTION_UNIT_TYPES = {"SENPIRIB"}
MOTION_CATALOG_NUMBERS = {"5753L", "5753PEIRL"}
_MOTION_NAME_TOKENS = ("motion", "occupancy", "pir")
_LIGHT_LEVEL_NAME_TOKENS = (
    "light level",
    "ambient light",
    "illuminance",
    "lux",
)
_INTERNAL_PATTERNS = (
    re.compile(r"^z", re.IGNORECASE),
    re.compile(r"^group\s+\d+$", re.IGNORECASE),
    re.compile(r"^d\d+[ab]\s+(?:group|fitting)\s*\d+$", re.IGNORECASE),
    re.compile(r"^d\d+[ab]\s+broadcast$", re.IGNORECASE),
)


class ProjectError(ValueError):
    """Raised when a Toolkit project cannot be imported."""


@dataclass(slots=True, frozen=True)
class ProjectPayload:
    """Project file extracted from an upload."""

    content: bytes
    source_name: str
    format: str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        text = str(value or "").strip()
        return int(text, 0)
    except (TypeError, ValueError):
        return default


def _hex_values(value: str | None) -> list[int]:
    result: list[int] = []
    for token in (value or "").replace(",", " ").split():
        try:
            result.append(int(token, 16) if token.lower().startswith("0x") else int(token, 0))
        except ValueError:
            continue
    return result


def _parse_endpoint(interface_type: str, address: str) -> dict[str, Any]:
    host: str | None = None
    port: int | None = None
    if interface_type.casefold() in {"cni", "socket"} and address:
        if address.startswith("[") and "]:" in address:
            host_part, port_part = address.rsplit(":", 1)
            host = host_part[1:-1]
        elif ":" in address:
            host, port_part = address.rsplit(":", 1)
        else:
            host, port_part = address, "10001"
        try:
            port = int(port_part)
        except ValueError:
            host, port = address, 10001
    return {
        "type": interface_type or "Unknown",
        "address": address,
        "host": host,
        "port": port,
    }


def is_internal_group(name: str) -> bool:
    """Return whether a tag looks generated, unused, or commissioning-only."""
    cleaned = name.strip()
    if not cleaned or cleaned.casefold() in {"<unused>", "unused", "untitled"}:
        return True
    if "DONT USE" in cleaned.upper():
        return True
    return any(pattern.search(cleaned) for pattern in _INTERNAL_PATTERNS)


def is_motion_group_name(name: str) -> bool:
    """Return whether a tag explicitly describes occupancy state."""
    lower = name.casefold()
    return any(token in lower for token in _MOTION_NAME_TOKENS)


def is_light_level_group_name(name: str) -> bool:
    """Return whether a tag explicitly describes an ambient light level."""
    lower = name.casefold()
    return any(token in lower for token in _LIGHT_LEVEL_NAME_TOKENS)


def classify_group(name: str, relay: bool, output_assigned: bool) -> str:
    """Infer a conservative default platform for a group."""
    lower = name.casefold()
    if is_light_level_group_name(name) and not output_assigned:
        return "sensor"
    if is_motion_group_name(name) and "light" not in lower and not output_assigned:
        return "binary_sensor"
    if relay or any(
        token in lower
        for token in (
            "relay",
            "master off",
            "pir enable",
            "pir disable",
            "enable control",
        )
    ):
        return "switch"
    return "light"


def default_application_mapping(application: int) -> str:
    """Return the initial entity mapping for an application address."""
    if 48 <= application <= 127:
        return "auto"
    if application == 200:
        return "sensor"
    if application == 203:
        return "switch"
    if application == 228:
        return "sensor"
    return "ignore"


def _resolve_motion_groups(
    unit: dict[str, Any],
    group_lookup: dict[tuple[int, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    applications: list[int] = unit.pop("_applications", [])
    groups: list[int] = unit.pop("_group_addresses", [])
    light_mask = int(unit.pop("_pir_light_movement", 0))
    dark_mask = int(unit.pop("_pir_dark_movement", 0))
    second_app_mask = int(unit.pop("_second_application_blocks", 0))

    if not applications or not groups:
        return []

    union_mask = light_mask | dark_mask
    common_mask = light_mask & dark_mask
    candidates: list[dict[str, Any]] = []
    for block, group in enumerate(groups[:8]):
        bit = 1 << block
        if group == 0xFF or not union_mask & bit:
            continue
        use_second = bool(second_app_mask & bit) and len(applications) > 1
        application = applications[1] if use_second else applications[0]
        if application == 0xFF:
            continue
        project_group = group_lookup.get((application, group), {})
        name = str(project_group.get("name") or f"Group {group}")
        candidates.append(
            {
                "application": application,
                "group": group,
                "name": name,
                "block": block,
                "dedicated": is_motion_group_name(name),
                "active_in_light": bool(light_mask & bit),
                "active_in_dark": bool(dark_mask & bit),
                "active_in_both": bool(common_mask & bit),
            }
        )

    explicit = [item for item in candidates if item["dedicated"]]
    common = [item for item in candidates if item["active_in_both"]]
    selected = explicit or common or candidates
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for item in selected:
        key = (item["application"], item["group"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _extract_payload(path: Path) -> ProjectPayload:
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ProjectError("Project upload is too large")

    if not zipfile.is_zipfile(path):
        raw = path.read_bytes()
        if raw.startswith(b"SQLite format 3\x00"):
            return ProjectPayload(raw, path.name, "sqlite")
        return ProjectPayload(raw, path.name, "xml")

    with zipfile.ZipFile(path) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ProjectError("Project archive contains too many files")
        for member in members:
            parts = Path(member.filename).parts
            if member.filename.startswith("/") or ".." in parts:
                raise ProjectError("Project archive contains an unsafe path")
        candidates = [
            member
            for member in members
            if Path(member.filename).suffix.casefold() in {".db", ".xml"}
        ]
        if not candidates:
            raise ProjectError("No C-Bus Toolkit project database or XML was found")
        candidate = max(candidates, key=lambda item: item.file_size)
        if candidate.file_size > MAX_EXPANDED_BYTES:
            raise ProjectError("Expanded project file is too large")
        raw = archive.read(candidate)
        fmt = "sqlite" if raw.startswith(b"SQLite format 3\x00") else "xml"
        return ProjectPayload(raw, candidate.filename, fmt)


def parse_project_bytes(
    raw: bytes,
    source_name: str = "project.xml",
    source_format: str | None = None,
) -> dict[str, Any]:
    """Parse raw Toolkit project bytes into a compact versioned model."""
    if len(raw) > MAX_EXPANDED_BYTES:
        raise ProjectError("Project data is too large")
    detected_format = source_format or (
        "sqlite" if raw.startswith(b"SQLite format 3\x00") else "xml"
    )
    if detected_format not in {"sqlite", "xml"}:
        raise ProjectError(f"Unsupported Toolkit project format: {detected_format}")

    digest = hashlib.sha256(raw).hexdigest()
    project = _parse_sqlite(raw) if detected_format == "sqlite" else _parse_xml(raw)
    project.update(
        {
            "schema_version": 1,
            "source_name": source_name,
            "source_format": detected_format,
            "source_sha256": digest,
        }
    )
    project["default_application_mappings"] = {
        str(app): default_application_mapping(app)
        for app in sorted(
            {
                application["address"]
                for network in project["networks"]
                for application in network["applications"]
            }
        )
    }
    return project


def parse_project_path(path: Path) -> dict[str, Any]:
    """Parse a Toolkit project file into a compact versioned model."""
    payload = _extract_payload(path)
    return parse_project_bytes(payload.content, payload.source_name, payload.format)


def _parse_xml(raw: bytes) -> dict[str, Any]:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ProjectError("DTD and XML entity declarations are not permitted")
    try:
        root = ET.parse(io.BytesIO(raw)).getroot()
    except ET.ParseError as err:
        raise ProjectError(f"Invalid Toolkit XML: {err}") from err

    project_el = root.find("Project") if root.tag != "Project" else root
    if project_el is None:
        raise ProjectError("The upload does not contain a C-Bus Project element")

    project_name = (project_el.findtext("TagName") or "C-Bus Project").strip()
    project_id = (project_el.findtext("OID") or project_el.findtext("Address") or project_name).strip()
    networks: list[dict[str, Any]] = []

    for network_el in project_el.findall("Network"):
        network_address = _safe_int(network_el.findtext("Address"), -1)
        if not 0 <= network_address <= 255:
            continue
        network_name = (network_el.findtext("TagName") or f"Network {network_address}").strip()
        interface_el = network_el.find("Interface")
        interface_type = (interface_el.findtext("InterfaceType") if interface_el is not None else "None") or "None"
        interface_address = (interface_el.findtext("InterfaceAddress") if interface_el is not None else "") or ""

        app_use_counts: Counter[int] = Counter()
        relay_groups: set[tuple[int, int]] = set()
        output_groups: set[tuple[int, int]] = set()
        units: list[dict[str, Any]] = []

        for unit_el in network_el.findall("Unit"):
            pp = {
                element.get("Name", ""): element.get("Value", "")
                for element in unit_el.findall("PP")
            }
            applications = [value for value in _hex_values(pp.get("Application")) if value != 0xFF]
            groups = _hex_values(pp.get("GroupAddress"))
            app_use_counts.update(applications)
            unit_address = _safe_int(unit_el.findtext("Address"), -1)
            unit_type = (unit_el.findtext("UnitType") or "").strip().upper()
            catalog = (unit_el.findtext("CatalogNumber") or "").strip().upper()
            is_relay = unit_type.startswith("REL") or "RELAY" in unit_type
            is_output = is_relay or unit_type.startswith(
                ("DIM", "DMX", "ANOD", "PC_DAL", "DALI", "IOPE")
            )
            for application in applications:
                for group in groups:
                    if group == 0xFF:
                        continue
                    if is_output:
                        output_groups.add((application, group))
                    if is_relay:
                        relay_groups.add((application, group))

            if 0 <= unit_address <= 255:
                supports_illuminance = unit_type in ILLUMINANCE_UNIT_TYPES or catalog in ILLUMINANCE_CATALOG_NUMBERS
                supports_motion = unit_type in MOTION_UNIT_TYPES or catalog in MOTION_CATALOG_NUMBERS
                if supports_illuminance or supports_motion:
                    units.append(
                        {
                            "address": unit_address,
                            "name": (unit_el.findtext("TagName") or f"Unit {unit_address}").strip(),
                            "unit_type": unit_type,
                            "catalog_number": catalog,
                            "firmware_version": (unit_el.findtext("FirmwareVersion") or "").strip(),
                            "supports_illuminance": supports_illuminance,
                            "supports_motion": supports_motion,
                            "_applications": applications,
                            "_group_addresses": groups,
                            "_pir_light_movement": _first(pp.get("PIRLightMovement")),
                            "_pir_dark_movement": _first(pp.get("PIRDarkMovement")),
                            "_second_application_blocks": _first(pp.get("SecondApplicationBlocks")),
                        }
                    )

        applications_model: list[dict[str, Any]] = []
        for application_el in network_el.findall("Application"):
            app_address = _safe_int(application_el.findtext("Address"), -1)
            if not 0 <= app_address <= 255:
                continue
            groups_model: list[dict[str, Any]] = []
            for group_el in application_el.findall("Group"):
                group_address = _safe_int(group_el.findtext("Address"), -1)
                if not 0 <= group_address <= 255:
                    continue
                name = (group_el.findtext("TagName") or f"Group {group_address}").strip()
                relay = (app_address, group_address) in relay_groups
                output_assigned = (app_address, group_address) in output_groups
                groups_model.append(
                    {
                        "address": group_address,
                        "name": name,
                        "description": (group_el.findtext("Description") or "").strip(),
                        "internal": is_internal_group(name) or group_address == 255,
                        "relay": relay,
                        "output_assigned": output_assigned,
                        "suggested_platform": classify_group(name, relay, output_assigned),
                        "phantom": False,
                    }
                )
            applications_model.append(
                {
                    "address": app_address,
                    "name": (application_el.findtext("TagName") or f"Application {app_address}").strip(),
                    "referenced_by_units": app_use_counts.get(app_address, 0),
                    "groups": groups_model,
                    "measurements": [],
                }
            )

        _finish_units(units, applications_model)
        networks.append(
            {
                "address": network_address,
                "name": network_name,
                "interface": _parse_endpoint(interface_type.strip(), interface_address.strip()),
                "applications": applications_model,
                "units": units,
                "unit_count": len(network_el.findall("Unit")),
            }
        )

    if not networks:
        raise ProjectError("No C-Bus networks were found in the project")
    return {
        "db_version": (root.findtext("DBVersion") or "").strip(),
        "project_name": project_name,
        "project_id": project_id,
        "networks": networks,
    }


def _first(value: str | None) -> int:
    values = _hex_values(value)
    return values[0] if values else 0


def _parse_sqlite(raw: bytes) -> dict[str, Any]:
    with NamedTemporaryFile(suffix=".db") as temp:
        temp.write(raw)
        temp.flush()
        try:
            connection = sqlite3.connect(f"file:{temp.name}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error as err:
            raise ProjectError(f"Invalid Toolkit project database: {err}") from err
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if str(integrity).casefold() != "ok":
                raise ProjectError(f"Toolkit project database integrity check failed: {integrity}")
            required = {"project", "network", "application", "_group", "tagged_entity"}
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = required - tables
            if missing:
                raise ProjectError("Toolkit database is missing required tables: " + ", ".join(sorted(missing)))
            return _parse_sqlite_connection(connection)
        except sqlite3.Error as err:
            raise ProjectError(f"Unable to read Toolkit project database: {err}") from err
        finally:
            connection.close()


def _parse_sqlite_connection(connection: sqlite3.Connection) -> dict[str, Any]:
    project_row = connection.execute(
        """
        SELECT p.oid, te.tag_name
        FROM project p JOIN tagged_entity te ON te.id=p.tagged_entity_id
        ORDER BY p.id LIMIT 1
        """
    ).fetchone()
    if project_row is None:
        raise ProjectError("Toolkit database does not contain a project")
    project_name = str(project_row["tag_name"] or "C-Bus Project").strip()
    project_id = str(project_row["oid"] or project_name).strip()
    db_row = connection.execute("SELECT db_version FROM installation ORDER BY id LIMIT 1").fetchone()
    db_version = str(db_row[0]) if db_row else ""

    network_rows = connection.execute(
        """
        SELECT n.id, n.network_number, te.tag_name,
               i.interface_type, i.interface_address
        FROM network n
        JOIN tagged_entity te ON te.id=n.tagged_entity_id
        LEFT JOIN interface i ON i.network_id=n.id
        ORDER BY CAST(n.network_number AS INTEGER) DESC
        """
    ).fetchall()

    pp_by_unit: dict[int, dict[str, str]] = defaultdict(dict)
    if {"pp_properties", "property"}.issubset(
        {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ):
        for row in connection.execute(
            """
            SELECT pp.unit_id, p.name, p.value
            FROM pp_properties pp JOIN property p ON p.id=pp.property_id
            """
        ):
            pp_by_unit[int(row["unit_id"])][str(row["name"])] = str(row["value"])

    networks: list[dict[str, Any]] = []
    for network_row in network_rows:
        network_id = int(network_row["id"])
        network_address = _safe_int(network_row["network_number"], -1)
        if not 0 <= network_address <= 255:
            continue
        network_name = str(network_row["tag_name"] or f"Network {network_address}").strip()
        interface_type = str(network_row["interface_type"] or "None")
        interface_address = str(network_row["interface_address"] or "")

        unit_rows = connection.execute(
            """
            SELECT u.id, u.unit_type, u.catalog_number, u.firmware_version,
                   te.address, te.tag_name
            FROM unit u JOIN tagged_entity te ON te.id=u.tagged_entity_id
            WHERE u.network_id=?
            ORDER BY CAST(te.address AS INTEGER)
            """,
            (network_id,),
        ).fetchall()
        app_use_counts: Counter[int] = Counter()
        relay_groups: set[tuple[int, int]] = set()
        output_groups: set[tuple[int, int]] = set()
        units: list[dict[str, Any]] = []
        for unit_row in unit_rows:
            unit_id = int(unit_row["id"])
            properties = pp_by_unit.get(unit_id, {})
            applications = [value for value in _hex_values(properties.get("Application")) if value != 0xFF]
            groups = _hex_values(properties.get("GroupAddress"))
            app_use_counts.update(applications)
            unit_type = str(unit_row["unit_type"] or "").strip().upper()
            catalog = str(unit_row["catalog_number"] or "").strip().upper()
            is_relay = unit_type.startswith("REL") or "RELAY" in unit_type
            is_output = is_relay or unit_type.startswith(
                ("DIM", "DMX", "ANOD", "PC_DAL", "DALI", "IOPE")
            )
            for application in applications:
                for group in groups:
                    if group == 0xFF:
                        continue
                    if is_output:
                        output_groups.add((application, group))
                    if is_relay:
                        relay_groups.add((application, group))

            unit_address = _safe_int(unit_row["address"], -1)
            if 0 <= unit_address <= 255:
                supports_illuminance = unit_type in ILLUMINANCE_UNIT_TYPES or catalog in ILLUMINANCE_CATALOG_NUMBERS
                supports_motion = unit_type in MOTION_UNIT_TYPES or catalog in MOTION_CATALOG_NUMBERS
                if supports_illuminance or supports_motion:
                    units.append(
                        {
                            "address": unit_address,
                            "name": str(unit_row["tag_name"] or f"Unit {unit_address}").strip(),
                            "unit_type": unit_type,
                            "catalog_number": catalog,
                            "firmware_version": str(unit_row["firmware_version"] or "").strip(),
                            "supports_illuminance": supports_illuminance,
                            "supports_motion": supports_motion,
                            "_applications": applications,
                            "_group_addresses": groups,
                            "_pir_light_movement": _first(properties.get("PIRLightMovement")),
                            "_pir_dark_movement": _first(properties.get("PIRDarkMovement")),
                            "_second_application_blocks": _first(properties.get("SecondApplicationBlocks")),
                        }
                    )

        applications_model: list[dict[str, Any]] = []
        app_rows = connection.execute(
            """
            SELECT a.id, te.address, te.tag_name
            FROM application a JOIN tagged_entity te ON te.id=a.tagged_entity_id
            WHERE a.network_id=?
            ORDER BY CAST(te.address AS INTEGER)
            """,
            (network_id,),
        ).fetchall()
        for app_row in app_rows:
            app_id = int(app_row["id"])
            app_address = _safe_int(app_row["address"], -1)
            if not 0 <= app_address <= 255:
                continue
            groups_model: list[dict[str, Any]] = []
            for group_row in connection.execute(
                """
                SELECT g.phantom, te.address, te.tag_name, te.description
                FROM _group g JOIN tagged_entity te ON te.id=g.tagged_entity_id
                WHERE g.application_id=?
                ORDER BY CAST(te.address AS INTEGER)
                """,
                (app_id,),
            ):
                group_address = _safe_int(group_row["address"], -1)
                if not 0 <= group_address <= 255:
                    continue
                name = str(group_row["tag_name"] or f"Group {group_address}").strip()
                relay = (app_address, group_address) in relay_groups
                output_assigned = (app_address, group_address) in output_groups
                groups_model.append(
                    {
                        "address": group_address,
                        "name": name,
                        "description": str(group_row["description"] or "").strip(),
                        "internal": is_internal_group(name) or group_address == 255,
                        "relay": relay,
                        "output_assigned": output_assigned,
                        "suggested_platform": classify_group(name, relay, output_assigned),
                        "phantom": bool(group_row["phantom"]),
                    }
                )

            measurements: list[dict[str, Any]] = []
            for measurement in connection.execute(
                """
                SELECT dte.address device_address, dte.tag_name device_name,
                       cte.address channel_address, cte.tag_name channel_name,
                       c.units_type
                FROM device d
                JOIN tagged_entity dte ON dte.id=d.tagged_entity_id
                JOIN channel c ON c.device_id=d.id
                JOIN tagged_entity cte ON cte.id=c.tagged_entity_id
                WHERE d.application_id=?
                ORDER BY CAST(dte.address AS INTEGER), CAST(cte.address AS INTEGER)
                """,
                (app_id,),
            ):
                device_address = _safe_int(measurement["device_address"], -1)
                channel_address = _safe_int(measurement["channel_address"], -1)
                if not 0 <= device_address <= 255 or not 0 <= channel_address <= 255:
                    continue
                measurements.append(
                    {
                        "device": device_address,
                        "channel": channel_address,
                        "device_name": str(measurement["device_name"] or f"Device {device_address}"),
                        "name": str(measurement["channel_name"] or f"Channel {channel_address}"),
                        "units_type": str(measurement["units_type"] or ""),
                    }
                )

            applications_model.append(
                {
                    "address": app_address,
                    "name": str(app_row["tag_name"] or f"Application {app_address}").strip(),
                    "referenced_by_units": app_use_counts.get(app_address, 0),
                    "groups": groups_model,
                    "measurements": measurements,
                }
            )

        _finish_units(units, applications_model)
        networks.append(
            {
                "address": network_address,
                "name": network_name,
                "interface": _parse_endpoint(interface_type, interface_address),
                "applications": applications_model,
                "units": units,
                "unit_count": len(unit_rows),
            }
        )

    if not networks:
        raise ProjectError("No C-Bus networks were found in the project")
    return {
        "db_version": db_version,
        "project_name": project_name,
        "project_id": project_id,
        "networks": networks,
    }


def _finish_units(units: list[dict[str, Any]], applications: list[dict[str, Any]]) -> None:
    group_lookup = {
        (application["address"], group["address"]): group
        for application in applications
        for group in application["groups"]
    }
    for unit in units:
        unit["motion_groups"] = (
            _resolve_motion_groups(unit, group_lookup)
            if unit.get("supports_motion")
            else []
        )
        unit.pop("_applications", None)
        unit.pop("_group_addresses", None)
        unit.pop("_pir_light_movement", None)
        unit.pop("_pir_dark_movement", None)
        unit.pop("_second_application_blocks", None)


def project_summary(project: dict[str, Any]) -> str:
    """Build a concise setup summary."""
    networks = project["networks"]
    groups = [
        group
        for network in networks
        for application in network["applications"]
        for group in application["groups"]
    ]
    visible = [group for group in groups if not group["internal"]]
    applications = {
        application["address"]
        for network in networks
        for application in network["applications"]
        if application["groups"] or application["measurements"]
    }
    connections = "\n".join(
        f"• {network['address']} — {network['name']}: "
        f"{network['interface']['address'] or network['interface']['type']}"
        for network in networks
    )
    return (
        f"Project **{project['project_name']}** ({project['source_format']}, DB {project['db_version'] or 'unknown'}) "
        f"contains {len(networks)} networks, {len(applications)} populated applications, "
        f"{len(groups)} group records and {len(visible)} named entity candidates.\n\n"
        + connections
    )


def project_diff(old: dict[str, Any], new: dict[str, Any]) -> str:
    """Build a safe project replacement preview."""
    def groups(project: dict[str, Any]) -> dict[tuple[int, int, int], dict[str, Any]]:
        return {
            (network["address"], application["address"], group["address"]): group
            for network in project["networks"]
            for application in network["applications"]
            for group in application["groups"]
        }

    old_groups = groups(old)
    new_groups = groups(new)
    common = old_groups.keys() & new_groups.keys()
    renamed = [key for key in common if old_groups[key]["name"] != new_groups[key]["name"]]
    old_networks = {item["address"]: item for item in old["networks"]}
    new_networks = {item["address"]: item for item in new["networks"]}
    connection_changes = [
        address
        for address in old_networks.keys() & new_networks.keys()
        if old_networks[address]["interface"] != new_networks[address]["interface"]
    ]
    return "\n\n".join(
        (
            f"Networks added: {len(new_networks.keys() - old_networks.keys())}",
            f"Networks removed: {len(old_networks.keys() - new_networks.keys())}",
            f"Groups added: {len(new_groups.keys() - old_groups.keys())}",
            f"Groups removed: {len(old_groups.keys() - new_groups.keys())}",
            f"Groups renamed: {len(renamed)}",
            f"Connection definitions changed: {len(connection_changes)}",
        )
    )
