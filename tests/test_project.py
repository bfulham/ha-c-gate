"""Tests for Toolkit project import."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import zipfile

from project import parse_project_path


XML = b"""<?xml version='1.0'?>
<Installation><DBVersion>1.15.7</DBVersion><Project>
<OID>project-oid</OID><TagName>TEST</TagName>
<Network><TagName>Main</TagName><Address>254</Address>
<Interface><InterfaceType>CNI</InterfaceType><InterfaceAddress>10.0.0.2:10001</InterfaceAddress></Interface>
<Application><TagName>Lighting</TagName><Address>56</Address>
<Group><TagName>Hall Light</TagName><Address>1</Address></Group>
<Group><TagName>Hall Motion</TagName><Address>2</Address></Group>
</Application>
<Unit><TagName>Hall PIR</TagName><Address>20</Address><UnitType>SENPIRIB</UnitType><CatalogNumber>5753L</CatalogNumber><FirmwareVersion>2.4.00</FirmwareVersion>
<PP Name='Application' Value='0x38 0xff'/><PP Name='GroupAddress' Value='0x2 0xff 0xff 0xff 0xff 0xff 0xff 0xff'/>
<PP Name='PIRLightMovement' Value='0x1'/><PP Name='PIRDarkMovement' Value='0x1'/><PP Name='SecondApplicationBlocks' Value='0x0'/>
</Unit></Network></Project></Installation>"""


def test_parse_legacy_cbz(tmp_path: Path) -> None:
    archive = tmp_path / "TEST.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("TEST.xml", XML)
    project = parse_project_path(archive)
    assert project["project_name"] == "TEST"
    assert project["source_format"] == "xml"
    assert project["networks"][0]["interface"]["host"] == "10.0.0.2"
    assert project["networks"][0]["units"][0]["motion_groups"][0]["group"] == 2


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
        INSERT INTO tagged_entity VALUES(1,'TEST',NULL,NULL,NULL),(2,'Main','254',NULL,NULL),(3,'Lighting','56',NULL,NULL),(4,'Hall Light','1',NULL,NULL),(5,'Hall PIR','20',NULL,NULL);
        INSERT INTO project VALUES(1,'project-oid',1);
        INSERT INTO installation VALUES(1,NULL,'2.3',NULL,0,1,1);
        INSERT INTO network VALUES(1,NULL,2,1,'254',NULL,NULL,NULL);
        INSERT INTO interface VALUES(1,NULL,'CNI','10.0.0.2:10001',1);
        INSERT INTO application VALUES(1,NULL,3,1);
        INSERT INTO _group VALUES(1,NULL,4,1,NULL,0,NULL,NULL,NULL);
        INSERT INTO unit VALUES(1,NULL,5,1,'SENPIRIB','PIR',NULL,'2.4.00',NULL,NULL,NULL,NULL,'5753L',NULL,NULL,NULL,NULL,NULL,NULL);
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
    assert project["networks"][0]["applications"][0]["groups"][0]["name"] == "Hall Light"
