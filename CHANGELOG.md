# Changelog

## 0.3.1

- Added the missing `options.step.init.menu_options` translations for the four-item **Configure** menu: Hub connections, Application mappings, Group overrides, and Performance and discovery.
- Regenerated the runtime `translations/en.json` file from the corrected translation source so setup, reconfigure, and options-flow labels remain synchronized.

## 0.3.0

- Added direct Toolkit project import from a running C-Gate server using the `DBGETXML` command.
- Added a setup menu with **Fetch from C-Gate** as the primary path and manual CBZ/DB/XML upload as a fallback.
- Added **Reconfigure → Fetch latest project from C-Gate** so project changes can be imported without downloading and uploading a Toolkit backup.
- Reused the fetched C-Gate endpoint as the initial runtime connection for every imported network.
- Added strict project-name validation, a 64 MiB XML safety limit, and complete-snippet validation.
- Prevented reconfigure from accidentally replacing an entry with a different C-Bus project.

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
