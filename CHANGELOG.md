# Changelog

## 0.2.0

- Changed the Home Assistant device hierarchy to create one child device per populated C-Bus application.
- Moved light, switch, cover, binary-sensor, numeric group, and Measurement Application entities onto their application device.
- Removed physical PIR/multisensor motion entities and their per-unit devices. Motion is now represented directly by the configured C-Bus group.
- Added automatic numeric-sensor inference for group names containing `Light Level`, `Ambient Light`, `Illuminance`, or `Lux`. These remain 0–100% group-level values unless the project supplies a Measurement Application channel with a real lux unit.
- Added upgrade cleanup for the old per-network Lights/Sensors devices and physical-unit motion entities.
- Preserved existing group and Measurement Application unique IDs.

## 0.1.0

- Initial HACS release.
- Added legacy XML and modern SQLite Toolkit project import.
- Added offline-capable setup and project reconfigure flows.
- Added per-hub C-Gate endpoints and application/group mapping options.
- Added lights, switches, covers, binary sensors, group sensors, and Measurement Application sensors.
- Added physical PIR device mapping from Toolkit programming.
- Added C-Gate command pooling, push status handling, reconnects, diagnostics, and hub maintenance buttons.
