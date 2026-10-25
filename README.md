# Pylontech H3X Bridge for Home Assistant

This repository contains the HACS-installable **Pylontech H3X Bridge** Home Assistant custom integration for a Pylontech Force H3X system.

| Integration | Domain | Purpose |
| --- | --- | --- |
| Pylontech H3X Bridge | `pylontech_h3x_bridge` | Local Modbus TCP bridge for Force H3X sensors and controls. |

Pylontech H3X Bridge exposes the H3X sensors and writable Modbus controls needed by Home Assistant automations and energy-management systems. The register map below follows the integration implementation and the manufacturer document **ModBus Protocol Pylon FH3X V1.2 (2025-08-11)**.

## What It Controls

Default entity IDs are based on a clean Pylontech H3X Bridge install:

| Purpose | Default entity |
| --- | --- |
| EMS mode | `select.pylontech_h3x_bridge_ems_mode` |
| Charge/discharge power | `number.pylontech_h3x_bridge_charge_discharge_power_ref` |
| Battery SOC | `sensor.pylontech_h3x_bridge_battery_soc` |
| House load | `sensor.pylontech_h3x_bridge_load_power` |
| Battery module count | `sensor.pylontech_h3x_bridge_battery_module_count` |
| Battery system capacity | `sensor.pylontech_h3x_bridge_battery_system_capacity` |
| Battery usable capacity | `sensor.pylontech_h3x_bridge_battery_usable_capacity` |
| BMS temperature | `sensor.pylontech_h3x_bridge_bms_temperature` |
| Charge SOC limit | `number.pylontech_h3x_bridge_charge_limit_soc` |
| Discharge SOC limit | `number.pylontech_h3x_bridge_discharge_limit_soc_eps` |

The H3X integration writes Modbus register `40907` for EMS mode and `40901` for the signed charge/discharge power reference. Direct reference control requires `User mode`; the manual/weather time-slot methods can also use `PN-Customer mode`.

## Modbus Entity And Register Map

The entity IDs below are the defaults for a clean installation. Home Assistant may append a suffix when an entity ID already exists, and user-renamed entity IDs are not changed back by the integration. Register addresses are shown exactly as they are placed in the Modbus TCP request; do not prepend `4` or apply a one-register offset when comparing them with integration logs.

All values are read with function `0x03` (Read Holding Registers). Single-register writes use `0x06`; contiguous and 32-bit writes use `0x10`. Multi-register values use high word first (big-endian word and byte order).

### Inverter Telemetry (Slave 2)

| Default entity | Register(s), decimal (hex) | Raw type / scale | Notes |
| --- | --- | --- | --- |
| `sensor.pylontech_h3x_bridge_ac_total_power` | `30100-30101` (`0x7594-0x7595`) | `S32`, `1 W` | Total inverter AC power. |
| `sensor.pylontech_h3x_bridge_grid_total_power` | `30108-30109` (`0x759C-0x759D`) | `S32`, `1 W` | Signed grid power as reported by the inverter. |
| `sensor.pylontech_h3x_bridge_load_power` | Derived | `W` | Calculated as AC total power plus grid total power; it is not a separate register read. |
| `sensor.pylontech_h3x_bridge_inverter_status` | `30115` (`0x75A3`) | `U16` | Raw manufacturer status code. |
| `sensor.pylontech_h3x_bridge_pv1_voltage` | `30119` (`0x75A7`) | `U16 x 0.1 V` | PV input 1 voltage. |
| `sensor.pylontech_h3x_bridge_pv1_current` | `30120` (`0x75A8`) | `U16 x 0.1 A` | PV input 1 current. |
| `sensor.pylontech_h3x_bridge_pv2_voltage` | `30121` (`0x75A9`) | `U16 x 0.1 V` | PV input 2 voltage. |
| `sensor.pylontech_h3x_bridge_pv2_current` | `30122` (`0x75AA`) | `U16 x 0.1 A` | PV input 2 current. |
| `sensor.pylontech_h3x_bridge_pv3_voltage` | `30123` (`0x75AB`) | `U16 x 0.1 V` | PV input 3 voltage. |
| `sensor.pylontech_h3x_bridge_pv3_current` | `30124` (`0x75AC`) | `U16 x 0.1 A` | PV input 3 current. |
| `sensor.pylontech_h3x_bridge_pv_total_power` | `30127-30128` (`0x75AF-0x75B0`) | `S32`, `1 W` | Total PV power. |
| `sensor.pylontech_h3x_bridge_pv_total_energy` | `30129-30130` (`0x75B1-0x75B2`) | IEEE-754 `Float32`, `kWh` | Cumulative PV energy. |
| `sensor.pylontech_h3x_bridge_grid_voltage_r` | `30131` (`0x75B3`) | `U16 x 0.1 V` | Grid phase R voltage. |
| `sensor.pylontech_h3x_bridge_grid_voltage_s` | `30133` (`0x75B5`) | `U16 x 0.1 V` | Grid phase S voltage; `30132` is skipped by this integration. |
| `sensor.pylontech_h3x_bridge_grid_voltage_t` | `30135` (`0x75B7`) | `U16 x 0.1 V` | Grid phase T voltage; `30134` is skipped by this integration. |
| `sensor.pylontech_h3x_bridge_ac_frequency` | `30140` (`0x75BC`) | `U16 x 0.01 Hz` | AC frequency. |
| `sensor.pylontech_h3x_bridge_inverter_temperature` | `30146` (`0x75C2`) | `S16 x 0.1 C` | Inverter temperature. |
| `sensor.pylontech_h3x_bridge_heatsink_temperature` | `30147` (`0x75C3`) | `S16 x 0.1 C` | Heatsink temperature. |
| `sensor.pylontech_h3x_bridge_total_grid_import` | `30156-30157` (`0x75CC-0x75CD`) | IEEE-754 `Float32`, `kWh` | Cumulative grid import. |
| `sensor.pylontech_h3x_bridge_total_grid_export` | `30158-30159` (`0x75CE-0x75CF`) | IEEE-754 `Float32`, `kWh` | Cumulative grid export. |
| `sensor.pylontech_h3x_bridge_battery_status` | `30161` (`0x75D1`) | `U16` enum | `0` Sleep, `1` Charging, `2` Discharging, `3` Idle, `4` Standby, `5` Run, `6` Fault, `7` Offline. |
| `sensor.pylontech_h3x_bridge_battery_power` | `30162-30163` (`0x75D2-0x75D3`) | `S32`, `1 W` | Signed battery power as reported by the inverter. |
| `sensor.pylontech_h3x_bridge_battery_voltage` | `30164` (`0x75D4`) | `U16 x 0.1 V` | Battery voltage. |
| `sensor.pylontech_h3x_bridge_battery_current` | `30165` (`0x75D5`) | `S16 x 0.1 A` | Signed battery current. |
| `sensor.pylontech_h3x_bridge_total_battery_charge` | `30174-30175` (`0x75DE-0x75DF`) | IEEE-754 `Float32`, `kWh` | Cumulative charged energy. |
| `sensor.pylontech_h3x_bridge_total_battery_discharge` | `30176-30177` (`0x75E0-0x75E1`) | IEEE-754 `Float32`, `kWh` | Cumulative discharged energy. |
| `sensor.pylontech_h3x_bridge_battery_soc` | `30182` (`0x75E6`) | `U16`, `%` | Inverter-side battery state of charge. |

### ESS/BMS Telemetry (Slave 1)

| Default entity | Register(s), decimal (hex) | Raw type / scale | Notes |
| --- | --- | --- | --- |
| `sensor.pylontech_h3x_bridge_bms_voltage` | `5123` (`0x1403`) | `U16 x 0.1 V` | BMS-reported pack voltage. |
| `sensor.pylontech_h3x_bridge_bms_temperature` | `5126` (`0x1406`) | `S16 x 0.1 C` | BMS temperature. |
| `sensor.pylontech_h3x_bridge_bms_soc` | `5127` (`0x1407`) | `U16`, `%` | BMS state of charge. |
| `sensor.pylontech_h3x_bridge_bms_cycles` | `5128` (`0x1408`) | `U16`, cycles | BMS cycle counter. |
| `sensor.pylontech_h3x_bridge_bms_cell_voltage_max` | `5136` (`0x1410`) | `U16 x 0.001 V` | Optional; some firmware returns Modbus exception `2` (illegal address). |
| `sensor.pylontech_h3x_bridge_bms_cell_voltage_min` | `5137` (`0x1411`) | `U16 x 0.001 V` | Optional; unavailable together with the maximum-cell register on affected firmware. |
| `sensor.pylontech_h3x_bridge_bms_soh` | `5152` (`0x1420`) | `U16`, `%` | BMS state of health. |
| `sensor.pylontech_h3x_bridge_battery_module_count` | `5174` (`0x1436`) | `U16`, modules | Optional manufacturer field "Module number in series"; ESS base `0x1400` plus offset `0x0036`. |
| `sensor.pylontech_h3x_bridge_battery_system_capacity` | Derived from `5174` | `kWh` | Datasheet system capacity for 2-7 Force H3 modules: `10.24`, `15.36`, `20.48`, `25.60`, `30.72`, or `35.84`. |
| `sensor.pylontech_h3x_bridge_battery_usable_capacity` | Derived from `5174` | `kWh` | Datasheet usable capacity: `9.69`, `14.73`, `19.48`, `24.32`, `29.17`, or `34.01`. |
| `sensor.pylontech_h3x_bridge_battery_usable_capacity_theoretical` | Derived from `5174` | `kWh` | System capacity multiplied by the datasheet `95%` depth of discharge. |
| `sensor.pylontech_h3x_bridge_battery_usable_capacity_deviation` | Derived from `5174` | `%` | Absolute deviation between datasheet usable capacity and the theoretical 95%-DoD value. |

### Writable Controls (Slave 2)

| Default entity | Register(s), decimal (hex) | Raw type / scale | Allowed UI values and behavior |
| --- | --- | --- | --- |
| `number.pylontech_h3x_bridge_charge_discharge_power_ref` | `40901` (`0x9FC5`) | `S16`, `0.1Pn%` | Whole percentages `-100..100`. Negative charges; positive discharges; zero stops the forced reference. A nonzero write first sets `40907 = 4`. |
| `number.pylontech_h3x_bridge_charge_limit_soc` | `40902` (`0x9FC6`) | `U16`, `%` | `50..100%`. |
| `number.pylontech_h3x_bridge_discharge_limit_soc_eps` | `40903` (`0x9FC7`) | `U16`, `%` | `5..100%`; manufacturer EPS discharge limit. |
| `select.pylontech_h3x_bridge_ems_mode` | `40907` (`0x9FCB`) | `U16` enum | `0` Self-Consumption, `1` Back up, `2` Off-Grid, `3` Feed in priority, `4` User, `5` PN-Customer. |
| `number.pylontech_h3x_bridge_meter_max_power_export` | `40401-40402` (`0x9DD1-0x9DD2`) | `S32`, `1 W` | UI range `-20000..-1 W`, written high word first. Verify the sign and applicable grid-code setting on the specific inverter before changing it. |
| `switch.pylontech_h3x_bridge_heat_pump` | `40848` (`0x9F90`) | `U16` boolean | Writes `0` or `1`. This is the inverter's heat-pump control flag, not a generic external heat-pump relay. |
| `switch.pylontech_h3x_bridge_period_1` | `40908` (`0x9FCC`) | `U16` boolean | Enables/disables time slot 1. |
| `switch.pylontech_h3x_bridge_period_2` | `40914` (`0x9FD2`) | `U16` boolean | Enables/disables time slot 2. |
| `switch.pylontech_h3x_bridge_period_3` | `40920` (`0x9FD8`) | `U16` boolean | Enables/disables time slot 3. |
| `switch.pylontech_h3x_bridge_period_4` | `40926` (`0x9FDE`) | `U16` boolean | Enables/disables time slot 4. |

Register `40400` (`0x9DD0`, `U16`) is read with the export limit to preserve the contiguous source block, but the current integration does not expose it as a Home Assistant entity.

### Time-Slot And Clock Registers (Slave 2)

Each of the four time slots occupies six contiguous holding registers:

| Slot | Enable | Start | End | Mode | Power | Weekday mask |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `40908` | `40909` | `40910` | `40911` | `40912` | `40913` |
| 2 | `40914` | `40915` | `40916` | `40917` | `40918` | `40919` |
| 3 | `40920` | `40921` | `40922` | `40923` | `40924` | `40925` |
| 4 | `40926` | `40927` | `40928` | `40929` | `40930` | `40931` |

- Enable is `0` (disabled) or `1` (enabled).
- Start and end are packed as `(hour << 8) | minute`.
- Slot mode is `0` for charge or `1` for discharge.
- Slot power is `U16` in `0.1Pn%`; for example, `10%` is written as `100`.
- The services currently use weekday mask `0x7F` for every day.
- A slot is programmed atomically at integration level: disable, write registers, set EMS mode, then enable. Do not run a second Modbus client concurrently.

When clock synchronization is enabled, the integration writes:

| Register | Packed value |
| --- | --- |
| `40932` (`0x9FE4`) | Year |
| `40933` (`0x9FE5`) | `(month << 8) | day` |
| `40934` (`0x9FE6`) | `(hour << 8) | minute` |
| `40935` (`0x9FE7`) | `(second << 8) | weekday`, where Sunday is `0` |

## Release 0.4.2

- Replace the ambiguous "Command failed or control is owned" service error. A conflicting entity/register is now identified as an ownership rejection; genuine power-write, mode-write and read-back failures report their actual stage.
- Preserve the force-charge owner's direct command path. A signed-in user's negative power-reference action starts or updates manual charging; the dedicated rate control also remains usable during charging. Competing EMS/SOC/reference actions remain blocked intentionally.
- Keep control intent separate from physical response. `charge_control_status` retains "manual force charge" while the `charging_response` attribute reports `awaiting_measured_charging`, `charging_confirmed`, `not_requested`, or `command_failed`. Confirmation requires two successive observations with battery status Charging and battery power at or below -50 W. A non-charging observation clears confirmation immediately; raw measured status/power are never smoothed or fabricated. This confirms charge direction, not delivery of the requested wattage.
- Add `last_rejected_command`, `last_rejected_entity_id`, `last_control_error` and `last_power_command_error` diagnostics. Recovery while restoring saved settings is explicitly identified as `restoring` control ownership.
- Keep the eight local override/settings/diagnostic entities available after polling or command failures. They read local intent rather than stale coordinator data, so Force Charge can still be turned off and its failure attributes remain visible. Physical telemetry and direct hardware controls remain unavailable until communication recovers; local availability does not imply a device write succeeded.
- Exercise real `switch.turn_on`, `number.set_value`, authentication contexts and mid-dispatch takeover against Home Assistant and the Modbus emulator. Controller updates check ownership before and after each awaited service call and yield cleanly when preempted.

A WebSocket API log mentioning `number.set_value` came from a browser-issued number action; it does not establish that the Force Charge switch rejected itself. Do not remove the ownership guard to suppress it. Set manual SOC/EMS parameters before starting Force Charge, or stop the override first. A negative user reference and the dedicated charge-rate control are the supported ways to adjust manual charging while it is active.

Update the controller dashboard alongside the bridge to display measured charging response, the last competing entity and the actual Modbus failure separately. A correct negative register reference still does not prove the inverter is physically charging. Inverter/BMS interlocks remain authoritative; this release does not reverse signs or automatically probe other EMS modes.

## Release 0.4.1

- Fix missing override/weather dashboard controls when the saved bridge/device name differs from "Pylontech H3X Bridge". New controls use stable IDs; automatically generated 0.4.0 IDs migrate on setup. Legacy telemetry/control IDs, explicit custom names/IDs and IDs belonging to another device are preserved.
- Avoid a mandatory 24-register schedule read before manual charging. Each schedule is read as its own six-register block, and failures now raise an actionable Home Assistant service error.
- Confirm the actual 40901 reference and 40907 mode after direct power writes. An acknowledged Modbus packet and a matching register value are separate from measured battery charging.
- Expose grid headroom, requested/permitted power, raw signed reference and read-back verification in the charge-control diagnostics. A zero-watt charge request caused by limits is reported as paused, not "manual force charge".

The sign and scale are unchanged and verified against the manufacturer protocol and literal Modbus TCP bytes:

| Home Assistant input | Raw S16 at 40901 | Wire word | Meaning |
| --- | ---: | --- | --- |
| `-10%` | `-100` | `0xFF9C` | Charge |
| `0%` | `0` | `0x0000` | Zero reference |
| `+10%` | `100` | `0x0064` | Discharge |

For direct reference control, mode 40907 is set to `4` first. Time-slot methods instead use a positive U16 power magnitude with slot mode `0` for charging. Do not invert the sign to try to correct a non-charging inverter.

After updating and restarting, the previous dashboard's eight default control IDs should resolve without reimporting it. Updated dashboard packages additionally show headroom and read-back diagnostics. User-renamed controls need the matching custom IDs in the dashboard.

## Release 0.4.0

Requires Home Assistant 2025.3 or newer; runtime tests use Home Assistant 2026.10.4.

- Hostnames and IPv4/IPv6 endpoints are validated with a real Modbus read before saving. DNS/connect failures abort one poll instead of reconnecting for every register. DNS resolution remains asynchronous; IPv6/IPv4 connection attempts use Happy Eyeballs. A name must resolve from Home Assistant's own container/network, not just from a browser or desktop.
- A negative value entered by a signed-in user starts a bounded manual override, so an optimizer cannot immediately replace it. API/automation power commands retain their normal behavior. Failed writes now report an error rather than appearing successful.
- Force-charge and optional KNMI weather-reserve controls own conflicting EMS/SOC/power writes while active. Their status reports whether the inverter has actually entered Charging.

## Manual Charge And Weather Reserve

| Default entity | Purpose |
| --- | --- |
| `switch.pylontech_h3x_bridge_force_charge` | Start/stop manual charging; ends at the charge SOC limit or after two hours. |
| `number.pylontech_h3x_bridge_force_charge_rate` | Requested rate, 1-100% of configured rated inverter power. |
| `select.pylontech_h3x_bridge_charge_method` | Manual/weather command method: `user_reference` writes signed 40901 in User mode; `time_slot_pn_customer` uses charge slot 4 in EMS mode 5; `time_slot_user` uses the same slot in mode 4. Automation/API reference writes retain their direct signed-register contract. |
| `switch.pylontech_h3x_bridge_weather_reserve` | Enable the optional natural-disaster / severe-weather reserve policy. Disabled by default. |
| `select.pylontech_h3x_bridge_weather_alert_threshold` | Minimum KNMI colour: yellow (geel), orange (oranje, default), or red (rood). Higher colours also trigger. |
| `number.pylontech_h3x_bridge_weather_discharge_rate` | Post-full-charge discharge cap, 0-20% of rated power; default 5%. |
| `sensor.pylontech_h3x_bridge_control_owner` | `automatic`, `force_charge`, `weather_reserve`, or `restoring`. |
| `sensor.pylontech_h3x_bridge_charge_control_status` | Local control intent/pause/failure reason. Attributes separately expose measured charging response, telemetry availability, sources, expiry and actual command failures. |

In **Configure**, select a signed Shelly Pro 3EM grid power sensor (positive import, negative export, W or kW), the local KNMI warning-code/alerts sensor, and optionally a siren/chime. Set the actual inverter **Pn** rating, battery-circuit power cap and total grid connection cap. Defaults are 13.8 kW rated power, 11 kW battery power cap and 17.5 kW grid cap. The override subtracts current battery power from measured import, reserves 500 W headroom, and rounds charge commands down. A selected meter older than 120 seconds, invalid units, missing SOC/temperature, or BMS temperature outside 5-45 C pauses the override. The inverter/BMS protections remain authoritative. These are total three-phase power limits; per-phase fuse protection still requires the installation's own protection or a phase-aware EMS.

For weather, install/configure a local [KNMI integration](https://github.com/golles/ha-knmi) warning-code entity or [NL Weather](https://github.com/PaulVanSchayck/ha-nl-weather) alerts entity, then select it in the bridge. Use a source configured for your location; the bridge does not infer warning colour from ordinary rain/cloud forecasts. It accepts Dutch/English colour codes and structured `alerts` with `color`/`code`, ignoring expired entries. [KNMI's colour definitions](https://www.knmi.nl/kennis-en-datacentrum/uitleg/knmi-waarschuwingen) indicate weather risk, not a guaranteed power outage. KNMI is moving from provinces to flexible warning areas in November 2026, so the location-aware weather integration handles the warning-area feed.

At or above the selected threshold the bridge charges to 100% as fast as the configured caps permit, raises a persistent notification and activates the optional [Home Assistant siren](https://www.home-assistant.io/integrations/siren/) for two seconds. No undocumented inverter buzzer register is written. After reaching 100%, it allows only capped discharge toward measured household demand and stops that discharge at 90% SOC. If an active warning source becomes unavailable, reserve control stays active until a valid lower warning or the user disables it. Clearing the warning restores the previous charge SOC limit and Self-Consumption mode. Weather reserve takes priority over manual charging, which takes priority over optimizer writes.

During manual/weather control, the four built-in inverter schedules are saved and temporarily suspended to prevent competing commands. Settings are restored when the override ends; recovery information is stored across restarts. Time-slot methods temporarily use **slot 4**. Short five-minute slots are refreshed during active charging and disabled on stop or discharge. Midnight-spanning slots are avoided (charging pauses for the last minute of the day). The low-level diagnostic services remain available, but conflicting writes are rejected during an override. Select a time-slot method if your firmware accepts a negative reference without actually charging; successful Modbus read-back alone does not establish physical charging. Test at a low rate while watching Battery Status and Battery Power.

Where module-derived system capacity is available, the override also caps battery power at **0.5C** (500 W per kWh). On a six-module 30.72 kWh pack, the configured 11 kW limit is lower and therefore applies. Full-charge status and saved charge limits survive reloads; a manual session is stopped on reload rather than silently restarting its two-hour timer.

The controller needs Home Assistant and working Modbus communication to enforce software timeouts and power limits. In User-reference mode, a command may remain on the inverter if Home Assistant or the network fails; the device's configured SOC and hardware limits still apply. Time-slot mode provides a device-side time window. This feature does not configure EPS islanding or make the house outage-capable.

## HACS Installation

1. In HACS, add `https://github.com/shuffleznl/h3x-bridge` as a custom repository of type **Integration**.
2. Install **Pylontech H3X Bridge**.
3. Restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration**.
5. Add **Pylontech H3X Bridge** and enter the Modbus TCP IP/port.

## Manual Installation

1. Copy `custom_components/pylontech_h3x_bridge` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration**.
4. Add **Pylontech H3X Bridge** and enter the Modbus TCP IP/port.

## Operational Notes

- The manufacturer protocol lists Modbus TCP endpoint `172.22.184.210:502` as the factory default. Configure the address actually assigned to the inverter; the host and port can be updated from the integration options.
- Register `40901` is written as signed `S16`: negative values charge, positive values discharge.
- Home Assistant exposes `40901` as whole percent values from `-100` to `100`; the integration converts that to the raw Modbus `0.1Pn%` signed integer.
- `Pn%` is a percentage of the inverter's rated power, not a kW command. Confirm the applicable inverter rating and start with a small magnitude while observing physical battery and grid power.
- The integration always sets EMS mode `40907` to `4` (`User mode`) before nonzero charge/discharge power writes.
- The integration uses a small raw Modbus TCP transport instead of PyModbus. Requests stay serialized on one socket, and late duplicate ACK frames are discarded until the matching transaction id is received.
- The integration performs its own locked write retries. Direct power commands verify the signed reference and EMS mode under the same lock before reporting success; normal polling separately checks the physical battery response.
- The BMS module count is read from ESS register `0x1436` / decimal `5174`, calculated as ESS base `0x1400` plus offset `0x0036` ("Module number in series").
- Total and usable capacity are derived from the Force H3 datasheet table for the detected module count. The derived usable capacity is checked against the 95% depth-of-discharge theoretical value and exposed with a deviation percentage.
- IP and port can be changed later from **Settings > Devices & services > Pylontech H3X Bridge > Configure**.
- Keep only one Modbus client connected to the inverter. Disable the original `pylon_fh3x` integration and other polling tools for the same H3X while using Pylontech H3X Bridge; concurrent TCP sessions can desynchronize Modbus transaction IDs.
- Firmware, grid-code, BMS, SOC, temperature, and protection interlocks can reject or suppress an otherwise valid command. Verify control writes at low power on the actual installation; a successful register read-back does not by itself prove physical charge or discharge.

## Time-Slot Services

Version `0.3.0` keeps the service-based control using the manufacturer time-slot registers and renames the Home Assistant domain to `pylontech_h3x_bridge`. These services are intended for controlled testing:

| Service | Purpose |
| --- | --- |
| `pylontech_h3x_bridge.force_charge_now` | Program a temporary charge slot, default slot `4`, default EMS mode `pn_customer` (`40907 = 5`). |
| `pylontech_h3x_bridge.test_force_charge_modes` | Program the same temporary charge slot first with EMS mode `5`, then with EMS mode `4`, and return measured snapshots. |
| `pylontech_h3x_bridge.clear_time_slot` | Disable one time slot. |

The slot command writes `40908-40931` style registers: disable slot, optionally sync inverter clock (`40932-40935`), write start/end/mode/power/weekday, set EMS mode, then enable the slot.

## Validation

Run `python -m pytest tools/test_runtime_controls.py tools/test_bridge_registry.py tools/test_service_control.py -q` in an environment with Home Assistant 2026.10.4, pytest, pytest-asyncio and PyYAML. These tests use actual entity platforms/registries, real entity services and a local Modbus TCP emulator. They cover signed charge/discharge wire frames, failed preparation, power limits, control ownership and the eight override/weather IDs. They also exercise authenticated negative reference changes and force-charge takeover before, during and after optimizer service calls. Dashboard attributes are checked in idle and after a charge request; when sibling controller checkouts are available, both dashboard YAML files are parsed and every bridge attribute row is checked against actual Home Assistant states.

GitHub CI checks out the controller dashboards for these runtime contract tests. Controller repositories also run the entity/attribute suite against the current bridge, so a dashboard change is validated against actual Home Assistant states rather than only expected name strings.

The emulator verifies software behavior, not inverter firmware or physical response. Confirm measured battery power at a low rate on the installation before relying on automatic control.

## Files

```text
custom_components/
  pylontech_h3x_bridge/
    __init__.py
    config_flow.py
    const.py
    coordinator.py
    manifest.json
    number.py
    select.py
    sensor.py
    switch.py
```
