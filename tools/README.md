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
