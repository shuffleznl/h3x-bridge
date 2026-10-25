# H3X Bridge 0.4.0

- Validate hostnames/IP addresses and perform a Modbus read before saving an endpoint. Use asynchronous DNS and IPv4/IPv6 connection racing; stop a failed polling cycle at its first transport error.
- Safely test replacement endpoints without holding a competing connection open. Use Home Assistant's standard reload lifecycle.
- Keep negative power values entered by a signed-in user active through a bounded manual override. Add a Force Charge switch, charge-rate control and measured charging diagnostics.
- Offer User-reference, PN-Customer charge-slot and User charge-slot methods. Save, suspend and restore competing inverter schedules.
- Add optional KNMI yellow/orange/red reserve control: charge to 100%, notify/chime once, then cap discharge while retaining 90% SOC. Select the local warning sensor, signed Shelly grid sensor and optional siren in Configure.
- Respect configured battery/grid power caps, live grid headroom, valid SOC/temperature and a 0.5C limit when module-derived capacity is available. Save restoration data across reloads.
- Update entity/register documentation and include 32 runtime tests against Home Assistant 2026.10.4 with a Modbus TCP emulator. Existing protocol validators also pass.

Requires Home Assistant 2025.3 or newer. Update the dashboard/controller integration alongside this bridge to expose the new controls and pause optimizer writes during overrides. Imported storage-mode dashboards require a fresh YAML import; YAML-mode dashboards referencing the packaged file follow package updates.

No live inverter or Home Assistant installation was connected during validation. A successful register read-back cannot establish physical charging; use the status/power diagnostics and test your selected charging method at a low rate.
