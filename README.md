# C-Bus C-Gate for Home Assistant

A native Home Assistant integration for Clipsal/Schneider Electric C-Bus installations using **C-Gate** as the runtime backend.

It is designed to work with the companion C-Gate Server Home Assistant app or an existing C-Gate installation. It does not use MQTT and it never opens a CNI directly.

## v0.1.0 features

- Imports legacy Toolkit `.cbz`/`.xml` projects and modern C-Gate 3 `.cbz`/`.db` projects during setup.
- Setup validates C-Gate but can continue while C-Gate is offline.
- One Home Assistant hub device per imported C-Bus network/CNI.
- A lights device and sensor devices beneath the corresponding hub.
- Physical PIR/multisensor devices beneath their hub where Toolkit programming identifies them.
- Per-hub C-Gate host and command/event/status/config port overrides.
- Per-application entity mapping and per-group overrides.
- Direct C-Gate command and Status Change interfaces; no MQTT bridge.
- Up to eight persistent command sessions for fast parallel actions.
- Status Change Port push updates with an automatic command-port event fallback when port 20025 is disabled.
- Optimistic UI state followed by authoritative C-Gate status reconciliation.
- Project replacement through **Reconfigure**, preserving address-based unique IDs.
- Lights, switches, binary sensors, numeric group sensors, covers, and Measurement Application sensors.
- Reopen-network and resynchronise buttons per hub.
- Diagnostics and automatic reconnects.

## Device hierarchy

```text
C-Gate THEBEND
├── ESS2 Race Control                      (hub / CNI network)
│   ├── ESS2 Race Control Lights           (light, switch and cover entities)
│   ├── ESS2 Race Control Sensors          (generic sensor entities)
│   ├── physical multisensor               (motion entity)
│   └── C-Gate connection / maintenance
├── DB-L1-1 Function Rooms                 (hub)
│   └── ...
└── ...
```

C-Bus groups are logical objects, so all controllable groups are collected into one logical lights device per hub instead of pretending every group is a separate physical device.

## Installation with HACS

1. In HACS, open **Integrations**.
2. Add `https://github.com/bfulham/ha-cbus-cgate` as a custom repository of type **Integration**.
3. Install **C-Bus C-Gate**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → C-Bus C-Gate**.
6. Upload the Toolkit project backup.

Manual installation is also possible by copying `custom_components/cbus_cgate` into `/config/custom_components`.

## Initial setup

1. Upload a Toolkit project:
   - Toolkit 1.16 and earlier: CBZ containing XML.
   - Toolkit 1.17 and later/C-Gate 3: CBZ containing a SQLite DB.
   - Raw `.xml` and `.db` files are also accepted.
2. Enter the C-Gate server address and ports.
3. The integration attempts `NOOP`, `PROJECT USE`, and a project-state read.
4. If validation fails, choose **Continue without a working connection**. The integration will load and retry in the background.
5. Finish setup, then use **Configure → Application mappings** to change imported applications.

The default endpoint is applied to every hub. Configure an individual hub to point it at another C-Gate server or use different ports.

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

- **Automatic**: motion-named groups without an output become binary sensors, relay groups become switches, and other groups become lights.
- **Light**
- **Switch**
- **Binary sensor**
- **Numeric sensor**
- **Cover**
- **Ignore**

Mappings may be changed per network/application. Any individual group can then be overridden separately.

## C-Gate requirements

The C-Gate project must exist on the selected C-Gate server and its project name must match the uploaded Toolkit project.

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

Open the integration menu and select **Reconfigure**, then upload the updated CBZ/DB/XML project. The integration previews additions, removals, renames, and connection-definition changes.

Entity unique IDs are based on a generated installation ID plus numeric C-Bus addresses, never group names. Renaming a group therefore preserves automations and history.

## Current limitations

- v0.1.0 maps common group-oriented applications and Measurement Application events. It does not yet implement native HVAC, Trigger Control, Enable Control selectors, scenes, or every specialised C-Bus application.
- Covers are represented as one 0–255 position group. Paired up/down relay covers need a future composite-device mapping.
- Unit Parameter light-level polling is not yet implemented through C-Gate. Dedicated Measurement Application illuminance channels are supported.
- A C-Gate project can contain networks that are offline or use serial interfaces unavailable to the C-Gate host; those hubs remain unavailable without blocking other hubs.
- This release has been parser/protocol tested against the supplied THEBEND Toolkit projects and C-Gate 3.7.1 locally, but live field testing is still required.

## Debug logging

```yaml
logger:
  logs:
    custom_components.cbus_cgate: debug
```

## License

MIT. Schneider Electric C-Gate is proprietary software and is not included with this repository.
