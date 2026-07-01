"""Tests for Toolkit project import."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

from project import (
    classify_group,
    effective_group_platform,
    light_level_broadcast_to_lux,
    parse_project_archive_bytes,
    parse_project_bytes,
    parse_project_path,
)

XML = b"""<?xml version='1.0'?>
<Installation><DBVersion>1.15.7</DBVersion><Project>
<OID>project-oid</OID><TagName>TEST</TagName>
<Network><TagName>Main</TagName><Address>254</Address>
<Interface><InterfaceType>CNI</InterfaceType><InterfaceAddress>10.0.0.2:10001</InterfaceAddress></Interface>
<Application><TagName>Lighting</TagName><Address>56</Address>
<Group><TagName>Hall Light</TagName><Address>1</Address></Group>
<Group><TagName>Hall Motion</TagName><Address>2</Address></Group>
<Group><TagName>Broadcast Reading</TagName><Address>3</Address></Group>
</Application>
<Unit><TagName>Hall PIR</TagName><Address>20</Address><UnitType>SENPIRIB</UnitType><CatalogNumber>5753L</CatalogNumber><FirmwareVersion>2.4.00</FirmwareVersion>
<PP Name='Application' Value='0x38 0xff'/><PP Name='GroupAddress' Value='0x2 0x3 0xff 0xff 0xff 0xff 0xff 0xff'/>
<PP Name='PIRLightMovement' Value='0x1'/><PP Name='PIRDarkMovement' Value='0x1'/><PP Name='SecondApplicationBlocks' Value='0x0'/>
<PP Name='LightLevelBroadcast' Value='0x2'/>
</Unit></Network></Project></Installation>"""


def test_parse_legacy_cbz(tmp_path: Path) -> None:
    archive = tmp_path / "TEST.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("TEST.xml", XML)
    project = parse_project_path(archive)
    assert project["project_name"] == "TEST"
    assert project["source_format"] == "xml"
    assert project["networks"][0]["interface"]["host"] == "10.0.0.2"
    unit = project["networks"][0]["units"][0]
    assert unit["motion_groups"][0]["group"] == 2
    assert unit["light_level_broadcast_groups"][0]["group"] == 3
    broadcast_group = project["networks"][0]["applications"][0]["groups"][2]
    assert broadcast_group["light_level_broadcast"] is True
    assert broadcast_group["suggested_platform"] == "sensor"
    assert broadcast_group["lux_per_level"] == 10


def test_parse_modern_cbz(tmp_path: Path) -> None:
    database = tmp_path / "TEST.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE tagged_entity(id INTEGER PRIMARY KEY, tag_name TEXT, address TEXT, description TEXT, display_id INTEGER);
        CREATE TABLE project(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER);
        CREATE TABLE installation(id INTEGER PRIMARY KEY, oid TEXT, db_version TEXT, version TEXT, modified INTEGER, project_id INTEGER, installation_detail_id INTEGER);
        CREATE TABLE network(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, project_id INTEGER, network_number TEXT, network_signature TEXT, last_verified INTEGER, _external TEXT);
        CREATE TABLE interface(id INTEGER PRIMARY KEY, oid TEXT, interface_type TEXT, interface_address TEXT, network_id INTEGER);
        CREATE TABLE application(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, network_id INTEGER);
        CREATE TABLE _group(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, application_id INTEGER, area INTEGER, phantom INTEGER, snapshot_id INTEGER, tags_dlt_list_id INTEGER, nac_dali_duration_test_timeout TEXT);
        CREATE TABLE unit(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, network_id INTEGER, unit_type TEXT, unit_name TEXT, serial_number TEXT, firmware_version TEXT, firmware_version2 TEXT, last_modified INTEGER, firmware_checksum TEXT, parameter_checksum TEXT, catalog_number TEXT, burden_enabled INTEGER, switchable_power_supply_enabled INTEGER, patch_version TEXT, snapshot_id INTEGER, device_name TEXT, group_number TEXT);
        CREATE TABLE property(id INTEGER PRIMARY KEY, oid TEXT, name TEXT, value TEXT);
        CREATE TABLE pp_properties(id INTEGER PRIMARY KEY, property_id INTEGER, unit_id INTEGER);
        CREATE TABLE device(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, application_id INTEGER);
        CREATE TABLE channel(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, device_id INTEGER, time_out_period TEXT, units_type TEXT);
        INSERT INTO tagged_entity VALUES
            (1,'TEST',NULL,NULL,NULL),
            (2,'Main','254',NULL,NULL),
            (3,'Lighting','56',NULL,NULL),
            (4,'Hall Light','1',NULL,NULL),
            (5,'Hall PIR','20',NULL,NULL),
            (6,'Broadcast Reading','3',NULL,NULL);
        INSERT INTO project VALUES(1,'project-oid',1);
        INSERT INTO installation VALUES(1,NULL,'2.3',NULL,0,1,1);
        INSERT INTO network VALUES(1,NULL,2,1,'254',NULL,NULL,NULL);
        INSERT INTO interface VALUES(1,NULL,'CNI','10.0.0.2:10001',1);
        INSERT INTO application VALUES(1,NULL,3,1);
        INSERT INTO _group VALUES
            (1,NULL,4,1,NULL,0,NULL,NULL,NULL),
            (2,NULL,6,1,NULL,0,NULL,NULL,NULL);
        INSERT INTO unit VALUES(1,NULL,5,1,'SENPIRIB','PIR',NULL,'2.4.00',NULL,NULL,NULL,NULL,'5753L',NULL,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO property VALUES
            (1,NULL,'Application','0x38 0xff'),
            (2,NULL,'GroupAddress','0x1 0x3 0xff 0xff 0xff 0xff 0xff 0xff'),
            (3,NULL,'BroadcastActive','0x4'),
            (4,NULL,'BroadcastBlock','0x1'),
            (5,NULL,'SecondApplicationBlocks','0x0');
        INSERT INTO pp_properties VALUES
            (1,1,1),
            (2,2,1),
            (3,3,1),
            (4,4,1),
            (5,5,1);
        """
    )
    connection.commit()
    connection.close()
    archive = tmp_path / "TEST.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(database, "TEST.db")
    project = parse_project_path(archive)
    assert project["source_format"] == "sqlite"
    assert project["db_version"] == "2.3"
    application = project["networks"][0]["applications"][0]
    assert application["groups"][0]["name"] == "Hall Light"
    assert application["groups"][1]["light_level_broadcast"] is True
    assert application["groups"][1]["sensor_kind"] == "illuminance"
    assert application["groups"][1]["native_unit"] == "lx"
    assert application["groups"][1]["suggested_platform"] == "sensor"


def test_parse_project_archive_bytes_preserves_sqlite_broadcast_metadata(
    tmp_path: Path,
) -> None:
    database = tmp_path / "TEST.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE tagged_entity(id INTEGER PRIMARY KEY, tag_name TEXT, address TEXT, description TEXT, display_id INTEGER);
        CREATE TABLE project(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER);
        CREATE TABLE installation(id INTEGER PRIMARY KEY, oid TEXT, db_version TEXT, version TEXT, modified INTEGER, project_id INTEGER, installation_detail_id INTEGER);
        CREATE TABLE network(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, project_id INTEGER, network_number TEXT, network_signature TEXT, last_verified INTEGER, _external TEXT);
        CREATE TABLE interface(id INTEGER PRIMARY KEY, oid TEXT, interface_type TEXT, interface_address TEXT, network_id INTEGER);
        CREATE TABLE application(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, network_id INTEGER);
        CREATE TABLE _group(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, application_id INTEGER, area INTEGER, phantom INTEGER, snapshot_id INTEGER, tags_dlt_list_id INTEGER, nac_dali_duration_test_timeout TEXT);
        CREATE TABLE unit(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, network_id INTEGER, unit_type TEXT, unit_name TEXT, serial_number TEXT, firmware_version TEXT, firmware_version2 TEXT, last_modified INTEGER, firmware_checksum TEXT, parameter_checksum TEXT, catalog_number TEXT, burden_enabled INTEGER, switchable_power_supply_enabled INTEGER, patch_version TEXT, snapshot_id INTEGER, device_name TEXT, group_number TEXT);
        CREATE TABLE property(id INTEGER PRIMARY KEY, oid TEXT, name TEXT, value TEXT);
        CREATE TABLE pp_properties(id INTEGER PRIMARY KEY, property_id INTEGER, unit_id INTEGER);
        CREATE TABLE device(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, application_id INTEGER);
        CREATE TABLE channel(id INTEGER PRIMARY KEY, oid TEXT, tagged_entity_id INTEGER, device_id INTEGER, time_out_period TEXT, units_type TEXT);
        INSERT INTO tagged_entity VALUES
            (1,'TEST',NULL,NULL,NULL),
            (2,'Main','254',NULL,NULL),
            (3,'Lighting','56',NULL,NULL),
            (4,'Broadcast Reading','3',NULL,NULL),
            (5,'Hall PIR','20',NULL,NULL);
        INSERT INTO project VALUES(1,'project-oid',1);
        INSERT INTO installation VALUES(1,NULL,'2.3',NULL,0,1,1);
        INSERT INTO network VALUES(1,NULL,2,1,'254',NULL,NULL,NULL);
        INSERT INTO interface VALUES(1,NULL,'CNI','10.0.0.2:10001',1);
        INSERT INTO application VALUES(1,NULL,3,1);
        INSERT INTO _group VALUES(1,NULL,4,1,NULL,0,NULL,NULL,NULL);
        INSERT INTO unit VALUES(1,NULL,5,1,'SENPIRIB','PIR',NULL,'2.4.00',NULL,NULL,NULL,NULL,'5753L',NULL,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO property VALUES
            (1,NULL,'Application','0x38 0xff'),
            (2,NULL,'GroupAddress','0x3 0xff 0xff 0xff 0xff 0xff 0xff 0xff'),
            (3,NULL,'BroadcastActive','0x4'),
            (4,NULL,'BroadcastBlock','0x0'),
            (5,NULL,'SecondApplicationBlocks','0x0');
        INSERT INTO pp_properties VALUES
            (1,1,1),
            (2,2,1),
            (3,3,1),
            (4,4,1),
            (5,5,1);
        """
    )
    connection.commit()
    connection.close()
    archive = tmp_path / "TEST.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(database, "TEST.db")

    project = parse_project_archive_bytes(archive.read_bytes(), "C-Gate Server current backup.cbz")
    group = project["networks"][0]["applications"][0]["groups"][0]
    assert project["source_format"] == "sqlite"
    assert group["sensor_kind"] == "illuminance"
    assert group["native_unit"] == "lx"
    assert group["lux_per_level"] == 10


def test_group_classification_uses_groups_for_sensor_values() -> None:
    assert classify_group("Hall Motion", relay=False, output_assigned=False) == "binary_sensor"
    assert classify_group("Hall Light Level", relay=False, output_assigned=False) == "sensor"
    assert classify_group("Hall Light Level", relay=False, output_assigned=True) == "sensor"


def test_light_level_broadcast_overrides_application_mapping() -> None:
    group = {
        "name": "Broadcast Reading",
        "relay": False,
        "output_assigned": True,
        "light_level_broadcast": True,
    }
    assert effective_group_platform(group, "light") == "sensor"
    assert effective_group_platform(group, "switch") == "sensor"
    assert effective_group_platform(group, "ignore") == "ignore"
    assert effective_group_platform(group, "ignore", "auto") == "sensor"


def test_group_auto_override_runs_inference() -> None:
    group = {
        "name": "Hall Light Level",
        "relay": False,
        "output_assigned": True,
    }
    assert effective_group_platform(group, "light", "auto") == "sensor"


def test_explicit_group_override_remains_authoritative() -> None:
    group = {
        "name": "Broadcast Reading",
        "relay": False,
        "output_assigned": False,
        "light_level_broadcast": True,
    }
    assert effective_group_platform(group, "light", "ignore") == "ignore"


def test_parse_project_bytes_from_cgate_xml() -> None:
    project = parse_project_bytes(XML, "TEST.xml (fetched from C-Gate)", "xml")
    assert project["project_name"] == "TEST"
    assert project["source_name"] == "TEST.xml (fetched from C-Gate)"
    assert project["source_format"] == "xml"


def test_light_level_broadcast_level_converts_to_lux() -> None:
    assert light_level_broadcast_to_lux(0) == 0
    assert light_level_broadcast_to_lux(49) == 490
    assert light_level_broadcast_to_lux(255) == 2550


def test_light_level_broadcast_direct_group_detects_unknown_unit_type() -> None:
    xml = XML.replace(
        b"<UnitType>SENPIRIB</UnitType><CatalogNumber>5753L</CatalogNumber>",
        b"<UnitType>CUSTOM_SENSOR</UnitType><CatalogNumber>UNKNOWN</CatalogNumber>",
    ).replace(
        b"<PP Name='LightLevelBroadcast' Value='0x2'/>",
        b"<PP Name='AmbientLightBroadcastGroupAddress' Value='0x3'/>",
    )
    project = parse_project_bytes(xml, "TEST.xml", "xml")

    unit = project["networks"][0]["units"][0]
    assert unit["supports_illuminance"] is False
    assert unit["light_level_broadcast_groups"][0]["group"] == 3
    group = project["networks"][0]["applications"][0]["groups"][2]
    assert group["light_level_broadcast"] is True
    assert group["suggested_platform"] == "sensor"


def test_light_level_broadcast_selected_block_property() -> None:
    xml = XML.replace(
        b"<PP Name='LightLevelBroadcast' Value='0x2'/>",
        b"<PP Name='IlluminanceBroadcastBlock' Value='0x1'/>",
    )
    project = parse_project_bytes(xml, "TEST.xml", "xml")

    unit = project["networks"][0]["units"][0]
    assert unit["light_level_broadcast_groups"][0]["block"] == 1
    assert unit["light_level_broadcast_groups"][0]["group"] == 3


def test_toolkit_broadcastactive_block_property() -> None:
    xml = XML.replace(
        b"<PP Name='LightLevelBroadcast' Value='0x2'/>",
        b"<PP Name='BroadcastActive' Value='0x4'/><PP Name='BroadcastBlock' Value='0x1'/>",
    )
    project = parse_project_bytes(xml, "TEST.xml", "xml")

    unit = project["networks"][0]["units"][0]
    assert unit["light_level_broadcast_groups"][0]["block"] == 1
    assert unit["light_level_broadcast_groups"][0]["group"] == 3
    group = project["networks"][0]["applications"][0]["groups"][2]
    assert group["light_level_broadcast"] is True
    assert group["suggested_platform"] == "sensor"


def test_toolkit_broadcastblock_is_ignored_when_light_level_broadcast_is_inactive() -> None:
    xml = XML.replace(
        b"<PP Name='LightLevelBroadcast' Value='0x2'/>",
        b"<PP Name='BroadcastActive' Value='0x0'/><PP Name='BroadcastBlock' Value='0x1'/>",
    )
    project = parse_project_bytes(xml, "TEST.xml", "xml")

    unit = project["networks"][0]["units"][0]
    assert unit["light_level_broadcast_groups"] == []
    group = project["networks"][0]["applications"][0]["groups"][2]
    assert "light_level_broadcast" not in group
