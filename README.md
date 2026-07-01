# C-Bus C-Gate for Home Assistant

A native Home Assistant integration for Clipsal/Schneider Electric C-Bus installations using **C-Gate** as the runtime backend.

It is designed to work with the companion C-Gate Server Home Assistant add-on or an existing C-Gate installation. It does not use MQTT and it never opens a CNI directly.

## v0.4.6 features

- Automatically detects a running **C-Gate Server** add-on on Home Assistant installations with Supervisor.
- Uses the detected add-on's internal hostname, standard ports, and configured Toolkit project name, avoiding manual connection details.
- Downloads the detected add-on's current CBZ backup directly over the internal Supervisor network, preserving modern SQLite Toolkit programming without a manual upload.
- Manual connection to another C-Gate server and manual import of `.cbz`, `.xml`, and `.db` projects remain available.
- Creates one Home Assistant hub device per imported C-Bus network/CNI and one child device per populated C-Bus application.
- Represents motion and light-level values through C-Bus groups on the application device rather than separate physical-sensor devices.
- Detects groups assigned to a sensor's **Light Level Broadcast** block and exposes them as illuminance sensors in lux.
- Applies per-hub C-Gate host and command/event/status/config port overrides.
- Supports per-application entity mapping and per-group overrides.
- Adds a **Hide individual fixtures and show groups only** option that recognises C-Bus addresses mapped by a 5502DAL/PC_DAL2C gateway to individual DALI fittings while retaining DALI group and broadcast addresses.
- Uses direct C-Gate command and Status Change interfaces; no MQTT bridge.
- Fetches the current level of every configured group during startup, so groups that have not changed since C-Gate or Home Assistant boot do not remain `unknown`.
- Uses up to eight persistent command sessions for fast parallel actions.
- Uses Status Change Port push updates with an automatic command-port event fallback when port 20025 is unavailable.
- Provides optimistic UI state followed by authoritative C-Gate status reconciliation.
- Supports project replacement through **Reconfigure** while preserving address-based unique IDs.
- Uses concise entity IDs based only on the entity name, such as `light.green_room`, while keeping address-based unique IDs internally.
- Provides lights, switches, binary sensors, numeric group sensors, covers, and Measurement Application sensors.
- Provides reopen-network and resynchronise buttons per hub, diagnostics, and automatic reconnects.

## Device hierarchy

```text
C-Gate THEBEND
├── ESS2 Race Control                      (hub / CNI network)
│   ├── ESS2 Race Control — Lighting       (application 56 groups)
│   ├── ESS2 Race Control — Measurement    (application 228 channels)
│   └── C-Gate connection / maintenance
├── DB-L1-1 Function Rooms                 (hub)
│   └── ...
└── ...
```

C-Bus applications are used as logical device boundaries. Every imported group or Measurement Application channel is attached to the child device for its network/application address. Physical PIR and multisensor units are not exposed as separate Home Assistant devices.

When upgrading from v0.1.0, the obsolete physical-unit motion entities and the old per-network Lights/Sensors devices are removed automatically. Automations that referenced a physical-unit motion entity must be changed to the corresponding motion-group binary sensor.

## Installation with HACS

1. In HACS, open **Integrations**.
2. Add `https://github.com/bfulham/ha-c-gate` as a custom repository of type **Integration**.
3. Install **C-Bus C-Gate**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → C-Bus C-Gate**.
6. Choose **Use detected C-Gate add-on** when it is offered.

Manual installation is also possible by copying `custom_components/cbus_cgate` into `/config/custom_components`.

## Initial setup

### Detected C-Gate Server add-on

1. Install and start the companion C-Gate Server add-on.
2. Upload/load the Toolkit project in the add-on and configure its `project_name` option.
3. Add this integration and choose **Use detected C-Gate add-on**.
4. Confirm the detected add-on and project name.
5. The integration uses the add-on's Supervisor-network hostname and standard C-Gate ports automatically, downloads its current CBZ backup, and presents the import summary.

Only running add-ons are offered. Discovery first uses Home Assistant's add-on cache and then queries Supervisor directly when that cache is not ready. If Supervisor is not available, the detected-add-on option is omitted and the manual paths remain available. The expected project-name field is optional because the backup identifies its own project.

### Another C-Gate server

Choose **Fetch from another C-Gate server**, then enter:

- the exact C-Gate project name;
- the server hostname or IP address;
- command port `20023` unless changed;
- event, status, and config-change ports if different from the defaults.

The integration runs `PROJECT USE <name>` and `DBGETXML //<name>/`, then parses the returned XML locally.

### Manual upload fallback

Choose **Upload a project file** when C-Gate is offline or unreachable during setup. Supported files are:

- Toolkit 1.16 and earlier: CBZ containing XML;
- Toolkit 1.17 and later/C-Gate 3: CBZ containing a SQLite DB;
- raw `.xml` and `.db` files.

After upload, enter the C-Gate endpoint. If validation fails, setup can continue offline and the integration reconnects in the background.

## Entity type detection

Default application mappings are intentionally conservative:

| Application | Default |
|---|---|
| 48–127 | Automatic lighting-family inference |
| 200 | Numeric sensor |
| 203 | Switch |
| 228 | Measurement sensor |
| Other applications | Ignored |

Under **Automatic**, the integration applies these rules:

- a group assigned to a unit's **Light Level Broadcast** block becomes an illuminance sensor;
- motion/occupancy/PIR-named groups without an output become motion binary sensors;
- light-level/ambient-light/illuminance/lux-named groups without an output become numeric percentage sensors when no broadcast programming was found;
- relay and enable/disable control groups become switches;
- other groups become lights.

Light Level Broadcast detection takes precedence over the broad network/application mapping, so a broadcast group remains a lux sensor even when its application is mapped as lights. Any individual group can still be overridden separately; an explicit concrete group override takes precedence. Selecting `auto` for a group runs the normal inference rules.

### Light Level Broadcast values

Toolkit programming is inspected to identify the virtual key/block or group used for Light Level Broadcast. The importer accepts descriptive bitmask, selected-block/key, and direct-group properties, plus the modern 5753L/SENPIRIB `BroadcastActive = 0x4` and `BroadcastBlock` layout. `BroadcastBlock` is resolved through the unit's `GroupAddress` array, so the group name does not need to contain “lux” or “light level”.

A detected broadcast group is exposed with:

- Home Assistant device class: **Illuminance**;
- native unit: `lx`;
- value: C-Bus group level × 10;
- attributes: raw C-Bus level and the conversion scale.

For example, raw level `49` is represented as `490 lx`. Groups whose names contain “light level”, “ambient light”, “illuminance”, or “lux” remain percentage sensors unless Toolkit programming marks them as the broadcast destination. They are never exposed as controllable lights merely because Toolkit also references the address from an output-capable unit.

## C-Gate requirements

The C-Gate project must exist on the selected server. The detected-add-on path downloads the add-on's built-in current backup on internal port `8099`, then uses the standard C-Gate ports for runtime control. Manual fetching from another C-Gate server still uses `DBGETXML` and requires **Program** access. Normal runtime control requires **Operate** or **Program** access.

The integration uses:

- command port `20023`;
- event port `20024`;
- Status Change port `20025`;
- config-change port `20026`.

If Status Change port `20025` is unavailable, the integration opens a separate command session and enables `EVENT e0s1c0` for push updates.

The C-Gate `access.txt` file must allow the Home Assistant host/container address at **Operate** or **Program** level.

## Fast control design

Home Assistant does not serialise platform actions (`PARALLEL_UPDATES = 0`). A bounded pool of persistent C-Gate command sessions executes simultaneous service calls concurrently. Status traffic uses a separate connection, so command responses cannot block physical-switch updates.

The default pool contains four command sessions. Increase it carefully under **Configure → Performance and discovery**. It is capped at eight.

The same page includes **Hide individual fixtures and show groups only**. When enabled, the integration reads each DALI gateway's `CBusToDali` map and omits addresses targeting individual fittings on either DALI line. Addresses targeting DALI groups or line broadcasts remain available, as do relays, motion groups, illuminance sensors, and non-DALI C-Bus groups. Existing fixture entity-registry entries are removed when the integration reloads.

## Initial state synchronisation

C-Gate push ports report changes as they occur, but they do not replay every existing group level when Home Assistant connects. The integration therefore performs an authoritative state fetch each time its C-Gate connection starts:

1. it requests a network state refresh from C-Gate;
2. it waits until each network's C-Gate object model is ready;
3. it reads all group levels by application using a wildcard query where supported;
4. it falls back to individual group reads on C-Gate versions that do not support wildcard reads;
5. it retries unresolved groups while C-Gate completes its startup scan and again during the normal health cycle.

This means a light, switch, cover, motion group, or numeric group should have its real state shortly after startup even when it has not produced a C-Bus event since boot.

## Updating the Toolkit project

Open the integration menu and select **Reconfigure**. The available choices are:

- **Fetch from detected C-Gate add-on**;
- **Fetch latest project from another C-Gate server**;
- **Upload a project file**.

Entity unique IDs use a generated installation ID plus numeric C-Bus addresses, never group names. Entity IDs are suggested from only the entity name, so a group named `Green Room` becomes `light.green_room` instead of including the network and application names. When two entities in the same domain have the same name, Home Assistant appends `_2`, `_3`, and so on.

When upgrading to v0.4.6, the integration performs a one-time migration of its existing automatically generated entity IDs. IDs that appear to have been manually customised are left unchanged. Because the visible entity IDs change, review YAML automations, external integrations, and dashboards that contain hard-coded references.

When upgrading from v0.4.2 or earlier, run **Reconfigure → Fetch from detected C-Gate add-on** once. This path downloads the real CBZ/SQLite backup instead of importing `DBGETXML` output, so the `BroadcastActive`, `BroadcastBlock`, and `GroupAddress` properties required for lux detection are retained. On reload, the existing sensor entities update from `%` to `lx`; stale `light.*` registry entries are also removed automatically when a group previously used the light domain.

When upgrading to v0.4.5 and enabling the groups-only option, run **Reconfigure → Fetch from detected C-Gate add-on** once before enabling the toggle. Older stored project models do not contain the `CBusToDali` mapping needed to distinguish individual fittings from DALI groups.

## Current limitations

- The integration maps common group-oriented applications and Measurement Application events. It does not yet implement native HVAC, Trigger Control, Enable Control selectors, scenes, or every specialised C-Bus application.
- Covers are represented as one 0–255 position group. Paired up/down relay covers need a future composite-device mapping.
- Light Level Broadcast conversion uses the C-Bus broadcast scale of 10 lux per group level. It does not poll physical Unit Parameters directly.
- A project can contain networks that are offline or use interfaces unavailable to the C-Gate host; those hubs remain unavailable without blocking other hubs.
- Fetching from another, non-add-on C-Gate server still uses `DBGETXML`. Modern SQLite-only metadata, including some Measurement Application definitions and device programming, may require a CBZ/DB import.
- The detected-add-on backup path and manual CBZ/DB path retain the complete modern SQLite project. The parser has been validated against the supplied THEBEND backup and detects all 43 configured Light Level Broadcast groups as illuminance sensors.

## Debug logging

```yaml
logger:
  logs:
    custom_components.cbus_cgate: debug
```

## License

MIT. Schneider Electric C-Gate is proprietary software and is not included with this repository.
