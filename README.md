# C-Bus C-Gate for Home Assistant

A native Home Assistant integration for Clipsal/Schneider Electric C-Bus installations using **C-Gate** as the runtime backend.

It is designed to work with the companion C-Gate Server Home Assistant app or an existing C-Gate installation. It does not use MQTT and it never opens a CNI directly.

## v0.3.0 features

- Fetches the project currently loaded by C-Gate directly over the command port using `DBGETXML`; no Toolkit backup upload is required.
- Manual import of legacy Toolkit `.cbz`/`.xml` projects and modern C-Gate 3 `.cbz`/`.db` projects remains available as a fallback.
- Setup validates C-Gate but can continue while C-Gate is offline.
- One Home Assistant hub device per imported C-Bus network/CNI.
- One Home Assistant child device per populated C-Bus application beneath its network hub.
- Motion and light-level values are represented by C-Bus group entities on the application device; physical multisensors are not created as separate devices.
- Per-hub C-Gate host and command/event/status/config port overrides.
- Per-application entity mapping and per-group overrides.
- Direct C-Gate command and Status Change interfaces; no MQTT bridge.
- Up to eight persistent command sessions for fast parallel actions.
- Status Change Port push updates with an automatic command-port event fallback when port 20025 is disabled.
- Optimistic UI state followed by authoritative C-Gate status reconciliation.
- Project replacement through **Reconfigure**, either by fetching the latest project from C-Gate or uploading a file, while preserving address-based unique IDs.
- Lights, switches, binary sensors, numeric group sensors, covers, and Measurement Application sensors.
- Reopen-network and resynchronise buttons per hub.
- Diagnostics and automatic reconnects.

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

When upgrading from v0.1.0, the obsolete physical-unit motion entities and the old per-network Lights/Sensors devices are removed automatically. Automations that referenced a physical-unit motion entity must be changed to the corresponding motion group binary sensor.

## Installation with HACS

1. In HACS, open **Integrations**.
2. Add `https://github.com/bfulham/ha-c-gate` as a custom repository of type **Integration**.
3. Install **C-Bus C-Gate**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → C-Bus C-Gate**.
6. Choose **Fetch from C-Gate**, then enter the project name and the Home Assistant host/IP running the C-Gate Server add-on.

Manual installation is also possible by copying `custom_components/cbus_cgate` into `/config/custom_components`.

## Initial setup

### Fetch from the C-Gate Server add-on

1. Make sure the Toolkit project has already been uploaded to and loaded by the C-Gate Server add-on.
2. Add the integration and choose **Fetch from C-Gate**.
3. Enter:
   - **C-Gate project name**: the exact project name shown by the add-on, such as `THEBEND`.
   - **C-Gate host**: the host name or IP address of the Home Assistant system running the add-on. `homeassistant.local` is the default.
   - **Command port**: `20023` unless the add-on port mapping was changed.
4. The integration runs `NOOP`, `PROJECT USE <name>`, and `DBGETXML //<name>/`, then parses the returned XML locally.
5. Confirm the imported project and finish setup.

The fetched endpoint is applied to every imported hub initially. Configure an individual hub later to point it at another C-Gate server or use different ports.

### Manual upload fallback

Choose **Upload a project file** when C-Gate is offline or not reachable during setup. Supported files are:

- Toolkit 1.16 and earlier: CBZ containing XML.
- Toolkit 1.17 and later/C-Gate 3: CBZ containing a SQLite DB.
- Raw `.xml` and `.db` files.

After upload, enter the C-Gate endpoint. If validation fails, setup can continue offline and the integration will reconnect in the background.

## Application mapping

Default mappings are intentionally conservative:

| Application | Default |
|---|---|
| 48–127 | Automatic lighting-family inference |
| 200 | Numeric sensor |
| 203 | Switch |
| 228 | Measurement sensor |
| Other applications | Ignored |

Available mappings:

- **Automatic**: motion-named groups without an output become binary sensors; light-level, illuminance, or lux-named groups without an output become numeric sensors; relay groups become switches; and other groups become lights.
- **Light**
- **Switch**
- **Binary sensor**
- **Numeric sensor**
- **Cover**
- **Ignore**

Mappings may be changed per network/application. Any individual group can then be overridden separately.

## C-Gate requirements

The C-Gate project must exist on the selected C-Gate server. Direct fetch requires **Program** access because it uses `DBGETXML`; normal runtime control requires **Operate** or **Program** access.

The integration uses:

- command port `20023`
- event port `20024` (reserved for future/diagnostic use)
- Status Change port `20025`
- config-change port `20026` (reserved for future project-change detection)

If Status Change port `20025` is unavailable, the integration automatically opens a separate command session and enables `EVENT e0s1c0` for push updates.

The C-Gate `access.txt` file must allow the Home Assistant host/container address at **Operate** or **Program** level.

## Fast control design

Home Assistant does not serialise platform actions (`PARALLEL_UPDATES = 0`). A bounded pool of persistent C-Gate command sessions executes simultaneous service calls concurrently. Status traffic uses a separate connection, so command responses cannot block physical-switch updates.

The default pool contains four command sessions. Increase it carefully under **Configure → Performance**. It is capped at eight.

## Updating the Toolkit project

Open the integration menu and select **Reconfigure**. Choose **Fetch latest project from C-Gate** to import the currently loaded database without handling a backup file, or choose manual upload. The integration previews additions, removals, renames, and connection-definition changes.

Entity unique IDs are based on a generated installation ID plus numeric C-Bus addresses, never group names. Renaming a group therefore preserves automations and history.

## Current limitations

- v0.3.0 maps common group-oriented applications and Measurement Application events. It does not yet implement native HVAC, Trigger Control, Enable Control selectors, scenes, or every specialised C-Bus application.
- Covers are represented as one 0–255 position group. Paired up/down relay covers need a future composite-device mapping.
- Physical Unit Parameter polling is intentionally not used. Group-based light-level values are exposed as percentages, while dedicated Measurement Application illuminance channels retain their real lux units.
- A C-Gate project can contain networks that are offline or use serial interfaces unavailable to the C-Gate host; those hubs remain unavailable without blocking other hubs.
- Direct `DBGETXML` fetch currently imports XML applications, groups, units, and programming properties. Measurement Application device/channel definitions from a modern SQLite Toolkit database still require the manual CBZ/DB import path.
- The included parser and mocked C-Gate protocol tests pass; live validation against the add-on and a real C-Gate project is still required.

## Debug logging

```yaml
logger:
  logs:
    custom_components.cbus_cgate: debug
```

## License

MIT. Schneider Electric C-Gate is proprietary software and is not included with this repository.
