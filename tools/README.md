# Force H3X Modbus Emulator

The runtime suite in `test_runtime_controls.py` runs the actual Home Assistant 2026.10.4 entity, configuration and coordinator code against this emulator. It covers DNS/IP normalization and failures, signed commands, manual priority, weather reserve, optional notification/siren, power headroom, SOC/temperature stops, reload recovery and both slot EMS modes. The emulator models documented direction/status transitions, not proprietary BMS firmware or real battery performance.

```powershell
uv run --python 3.14 --with homeassistant==2026.10.4 --with pytest --with pytest-asyncio python -m pytest tools/test_runtime_controls.py -q
```

`h3x_modbus_emulator.py` is a dependency-free Modbus TCP emulator for local Pylontech H3X Bridge validation. It implements the holding-register reads and writes used by the integration and can reproduce the duplicate write response pattern seen in live Modbus logs.

Run it with Python 3.11 or newer:

```bash
python tools/h3x_modbus_emulator.py --host 0.0.0.0 --port 1502 --duplicate-write-response
```

Then configure Pylontech H3X Bridge to use the emulator host and port `1502`.

Supported function codes:

```text
3   Read holding registers
6   Write single register
16  Write multiple registers
```

The emulator is not a battery simulator. It is a protocol harness for validating register encoding, read/write order, reconnect behavior, and transaction-ID resilience before pushing a HACS release.

Run the write-sequence validation locally:

```bash
python tools/validate_modbus_write_sequence.py
```

## Live Home Assistant Validation

Use `ha_live_probe.py` for read-only recordings through Home Assistant's existing
bridge, not a competing Modbus TCP connection. It records filtered energy states,
requested/effective reference, measured battery power/SOC, and relevant Modbus
errors. Unavailable measurements remain missing, never zero. An idle-sized trickle
does not count as delivery of a large charge request even if Battery Status says
Charging. Reports and credential files belong outside version control.

```powershell
python tools/ha_live_probe.py --url http://HOME_ASSISTANT:8123 --token-file C:\PRIVATE\ha-token.txt --duration-minutes 120
```

Create a temporary HA access token for the test and revoke it afterwards. Never
commit it, put it in command-line arguments, or include it in a report. HTTP is
unencrypted: use the trusted LAN or HTTPS. `.secrets/` and `live-validation/` are
ignored by this repository. Unchanged REST state JSON can contain a cached
`last_reported`; do not use an old unchanged SOC timestamp alone to diagnose a
stalled poll. The active test reads the actual in-memory timestamps through a
read-only HA template before checking freshness.

`ha_charge_validation.py` is an explicitly enabled, supervised charge-only matrix:
5%, 10% and 20% in direct User reference, PN-Customer slot and User slot modes. It
requires both EMS optimizers and weather reserve off, an independently measured
fresh grid source, fresh battery telemetry, safe SOC/temperature and an already
verified Force Charge off/zero-reference baseline. It enforces conservative
16.5 kW grid and 11 kW battery test ceilings without changing installed limits.
It does not retry uncertain commands or test full power.

```powershell
python tools/ha_charge_validation.py --url http://HOME_ASSISTANT:8123 --token-file C:\PRIVATE\ha-token.txt --allow-charge-tests --hold-seconds 120
```

On normal or exceptional exit it verifies Force Charge off and reference zero,
then restores the initial local rate/method preferences. It deliberately does not
resume a preexisting high-rate force charge. Keep it supervised: process/machine
failure or loss of the HA/network path can prevent cleanup. A direct reference
has no independent device-side test deadline. The existing bridge's SOC,
temperature, grid and session protections remain enabled; BMS and electrical
protection remain authoritative. Stop if communication becomes uncertain.

`ha_charge_readback.py` is an exceptional read-only interlock inspection. It first
requires all controllers off and a zero reference, disables the specified bridge
entry through HA, verifies it is unloaded, reads the documented PCS/BMS limits,
closes its socket, and re-enables the bridge in a `finally` block. It has no
Modbus write path. Use only with the exact verified inverter endpoint and explicit
`--pause-bridge-for-read-only-inspection`; never run it during the charge matrix.

The live tooling uses `aiohttp`, already present in the HA validation runtime.
Unit tests use fake HTTP/HA responses and never contact a live inverter:

```powershell
python -m pytest tools/test_ha_live_probe.py tools/test_ha_charge_validation.py -q
```

`ha_isolated_charge_validation.py` is for supervised validation of a changed
bridge before installing it. It verifies the installed bridge is unloaded,
starts the actual local Home Assistant coordinator with the same HA timezone,
uses fresh independent Shelly measurements, and tests short charge slots at
1-20% only. The default test uses zero direct reference so the short device
slot is an independent stop boundary. Every exit attempts zero-reference
stop, original schedule restoration and re-enabling the installed bridge.
No Home Assistant server restart or timezone change is performed.

The explicitly enabled `--combined-reference-slot` diagnostic additionally
tests a matching small negative reference and valid slot together. It first
lowers the actual device charge-SOC ceiling to no more than five percentage
points above current SOC, creating a conservative device-level backstop. The
original ceiling is restored only after a verified stop; restoration and bridge
reload are tested even when the independent meter fails. Do not run this tool
without supervision or on an unverified inverter/rating. Recovery settings are
journaled in the ignored output directory.

The isolated tool requires an explicit verified `--inverter-rated-power-w`,
module-derived pack capacity and a power request below 11 kW / 0.5C. The optional
`--reference-before-mode` and `--reference-write-function 16` comparisons use the
same conservative SOC backstop. These are commissioning diagnostics, not changes
to the integration's signed-reference contract or normal write order.

The emulator now evaluates slots against its own RTC registers. Regression tests
reproduce an inactive slot when HA local time is one hour ahead, then verify
device-clock-aligned operation in modes 4 and 5, invalid-clock refusal, midnight
handling and disabling a slot after mismatched read-back. Firmware behavior and
real delivered power still require hardware measurements.
