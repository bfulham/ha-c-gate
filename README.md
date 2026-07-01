# C-Bus C-Gate for Home Assistant

A native Home Assistant integration for Clipsal/Schneider Electric C-Bus installations using **C-Gate** as the runtime backend.

It is designed to work with the companion C-Gate Server Home Assistant add-on or an existing C-Gate installation. It does not use MQTT and it never opens a CNI directly.

## v0.4.2 features

- Automatically detects a running **C-Gate Server** add-on on Home Assistant installations with Supervisor.
- Uses the detected add-on's internal hostname, standard ports, and configured Toolkit project name, avoiding manual connection details.
- Fetches the project loaded by C-Gate directly over the command port using `DBGETXML`; no Toolkit backup upload is required.
- Manual connection to another C-Gate server and manual import of `.cbz`, `.xml`, and `.db` projects remain available.
- Creates one Home Assistant hub device per imported C-Bus network/CNI and one child device per populated C-Bus application.
- Represents motion and light-level values through C-Bus groups on the application device rather than separate physical-sensor devices.
- Detects groups assigned to a sensor's **Light Level Broadcast** block and exposes them as illuminance sensors in lux.
- Applies per-hub C-Gate host and command/event/status/config port overrides.
- Supports per-application entity mapping and per-group overrides.
- Uses direct C-Gate command and Status Change interfaces; no MQTT bridge.
- Uses up to eight persistent command sessions for fast parallel actions.
- Uses Status Change Port push updates with an automatic command-port event fallback when port 20025 is unavailable.
- Provides optimistic UI state followed by authoritative C-Gate status reconciliation.
- Supports project replacement through **Reconfigure** while preserving address-based unique IDs.
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
5. The integration uses the add-on's Supervisor-network hostname and standard C-Gate ports automatically, fetches the project, and presents the import summary.

Only running add-ons are offered. If Supervisor is not available, the detected-add-on option is omitted and the manual paths remain available.

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

The C-Gate project must exist on the selected server. Direct project fetch requires **Program** access because it uses `DBGETXML`; normal runtime control requires **Operate** or **Program** access.

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

## Updating the Toolkit project

Open the integration menu and select **Reconfigure**. The available choices are:

- **Fetch from detected C-Gate add-on**;
- **Fetch latest project from another C-Gate server**;
- **Upload a project file**.

Entity unique IDs use a generated installation ID plus numeric C-Bus addresses, never group names. Renaming a group therefore preserves automations and history.

After installing v0.4.2, run **Reconfigure → Fetch from detected C-Gate add-on** or upload the latest project once. Earlier versions saved a normalised project that did not retain the raw `BroadcastActive` and `BroadcastBlock` properties, so a fresh project parse is required. On the following reload, stale `light.*` registry entries are removed automatically and the replacement `sensor.*` illuminance entities are created.

## Current limitations

- The integration maps common group-oriented applications and Measurement Application events. It does not yet implement native HVAC, Trigger Control, Enable Control selectors, scenes, or every specialised C-Bus application.
- Covers are represented as one 0–255 position group. Paired up/down relay covers need a future composite-device mapping.
- Light Level Broadcast conversion uses the C-Bus broadcast scale of 10 lux per group level. It does not poll physical Unit Parameters directly.
- A project can contain networks that are offline or use interfaces unavailable to the C-Gate host; those hubs remain unavailable without blocking other hubs.
- Direct `DBGETXML` fetch imports XML applications, groups, units, and programming properties. Measurement Application device/channel definitions from a modern SQLite Toolkit database still require the manual CBZ/DB import path.
- The parser has been validated against a real modern Toolkit CBZ and detects all 43 configured Light Level Broadcast groups. Direct fetching still depends on C-Gate returning the corresponding unit programming properties in `DBGETXML`.

## Debug logging

```yaml
logger:
  logs:
    custom_components.cbus_cgate: debug
```

## License

MIT. Schneider Electric C-Gate is proprietary software and is not included with this repository.
