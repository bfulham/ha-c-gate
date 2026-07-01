# Changelog

## 0.4.5

- Added **Hide individual fixtures and show groups only** to **Configure → Performance and discovery**.
- Parses the 5502DAL/PC_DAL2C `CBusToDali` lookup from both modern SQLite CBZ projects and legacy/fetched XML projects.
- Marks C-Bus addresses targeting individual DALI fittings on line A (`0x00`–`0x3f`) or line B (`0x80`–`0xbf`) as individual fixtures, while retaining DALI group and broadcast targets.
- Removes existing fixture entities from Home Assistant's entity registry when the option is enabled and recreates them normally if it is disabled later.
- Validated against the supplied THEBEND project: the Balcony 2 individual Row 1–5 fittings are filtered while `Balcony 2 Front row`, `Balcony 2 Row 2+3`, and `Balcony 2 Row 4+5` remain visible.

## 0.4.4

- Fixed group entities remaining `unknown` after Home Assistant or C-Gate starts when their C-Bus level had not changed since boot.
- Added an initial C-Gate state synchronisation that requests a network state refresh, waits for the C-Gate object model to become ready, and reads the current level of every configured group.
- Uses application-wide wildcard level reads where supported, with automatic fallback to individual group reads for C-Gate versions that reject wildcard queries.
- Retries unresolved groups during the initial C-Gate network scan and continues retrying them during the normal connection health cycle instead of relying solely on future push events.
- Starts the push status listener before initial level retrieval so physical changes that occur during startup are not missed.

## 0.4.3

- Fixed detected add-on imports producing percentage sensors instead of lux sensors. The add-on path now downloads the add-on's real current CBZ backup from its built-in `/project/backup` endpoint instead of relying on `DBGETXML`, preserving the modern SQLite `BroadcastActive`, `BroadcastBlock`, and `GroupAddress` programming used by Light Level Broadcast.
- Added a byte-based CBZ import path with the same archive size, path-safety, and expanded-file validation as manual uploads.
- Added durable `sensor_kind: illuminance` and `native_unit: lx` metadata to detected Light Level Broadcast groups; group sensors accept these markers as well as the legacy boolean flag.
- Fixed add-on autodetection during setup and reconfigure by querying Supervisor directly when Home Assistant's cached add-on details are not ready, while retaining the cached-data fallback.
- Made the expected add-on project name optional because the downloaded backup identifies its own project.
- Validated the add-on-backup parser against the supplied THEBEND project: 43 Light Level Broadcast groups are retained as illuminance sensors in `lx`.

## 0.4.2

- Fixed modern Toolkit 5753L/SENPIRIB Light Level Broadcast detection. These projects store the feature as `BroadcastActive = 0x4` plus a zero-based `BroadcastBlock`, rather than using property names containing “light level”.
- Resolves `BroadcastBlock` through the unit's `GroupAddress` array and application selection, then marks that exact group as an illuminance sensor.
- Validated against a real modern Toolkit CBZ containing 43 programmed Light Level Broadcast groups; all 43 are detected as `sensor` entities rather than `light` entities.
- Ignores `BroadcastBlock` when the Light Level Broadcast active flag is not set.
- Added regression coverage for both modern SQLite Toolkit projects and fetched/legacy XML project data.

## 0.4.1

- Fixed Light Level Broadcast groups still appearing as lights when their application was mapped to `light` or another broad entity type.
- Light Level Broadcast metadata now takes precedence over application-level mappings while explicit per-group overrides remain authoritative.
- Light-level/ambient-light/illuminance/lux-named groups are now treated as sensor values even when Toolkit also references the address from an output-capable unit.
- Added automatic entity-registry cleanup when a group changes domain, removing the obsolete `light.*` entry before creating its `sensor.*` replacement.
- Fixed a stored per-group `auto` override so it runs automatic inference instead of being treated as a literal entity platform.

## 0.4.0

- Added Home Assistant Supervisor discovery for running **C-Gate Server** add-ons.
- Added setup and reconfigure paths that automatically select the add-on's internal hostname, standard C-Gate ports, and configured project name.
- Kept manual C-Gate connection and Toolkit file upload paths for non-Supervisor installations and remote C-Gate servers.
- Added Toolkit programming detection for groups assigned to a sensor's **Light Level Broadcast** block.
- Light Level Broadcast groups are now automatically created as illuminance sensors with unit `lx` and Home Assistant's illuminance device class.
- Added conversion of the C-Bus broadcast group level to lux at 10 lux per C-Bus level, while retaining the raw group level as an entity attribute.
- Added support for bitmask, selected-block/key, and direct-group Toolkit property encodings, including explicitly programmed sensor types not present in the built-in catalogue list.

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
- Added automatic numeric-sensor inference for group names containing `Light Level`, `Ambient Light`, `Illuminance`, or `Lux`. These remain 0–100% group-level values unless the project identifies the group as Light Level Broadcast or supplies a Measurement Application channel with a real lux unit.
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
