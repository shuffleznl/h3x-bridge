"""Exercise real HA entities/control logic against a local Modbus TCP emulator."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import Context, HomeAssistant
from custom_components.pylontech_h3x_bridge.config_flow import (
    CannotConnect,
    connection_schema,
    validate_input,
)
from custom_components.pylontech_h3x_bridge.coordinator import PylontechCoordinator
from custom_components.pylontech_h3x_bridge.endpoint import normalize_host
from custom_components.pylontech_h3x_bridge.number import NUMBER_TYPES, PylontechNumber
from custom_components.pylontech_h3x_bridge.override import (
    ChargeOverride,
    warning_level,
)
from custom_components.pylontech_h3x_bridge.transport import ModbusTransportError
from h3x_modbus_emulator import build_registers, handle_client


@pytest_asyncio.fixture
async def system(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    registers = build_registers()
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, registers, True, 0.01),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    entry = SimpleNamespace(
        entry_id="test",
        title="Pylontech H3X Bridge",
        data={"host": "localhost", "port": port},
        options={},
    )
    c = PylontechCoordinator(hass, "localhost", port)
    c.data = {
        "battery_soc": 55,
        "bms_temperature": 25,
        "charge_limit_soc": 95,
        "grid_total_power": 1200,
        "battery_power": 0,
        "ac_total_power": 0,
        "battery_status": "Idle",
    }
    control = c.override = ChargeOverride(hass, c, entry)
    control.store = SimpleNamespace(
        async_save=AsyncMock(), async_load=AsyncMock(return_value=None)
    )
    try:
        yield hass, c, control, entry, registers
    finally:
        await c.async_close()
        server.close()
        await server.wait_closed()
        await hass.async_stop()


@pytest.mark.parametrize(
    "value,expected",
    [
        (" battery.lan ", "battery.lan"),
        ("BATTERY.LOCAL.", "battery.local"),
        ("localhost", "localhost"),
        ("[::1]", "::1"),
        ("192.168.195.53", "192.168.195.53"),
    ],
)
def test_endpoint_normalization(value, expected):
    assert normalize_host(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "http://battery.lan", "battery:502", "bad host", "bad..lan", "-bad.lan"],
)
def test_bad_endpoint_rejected(value):
    with pytest.raises(ValueError):
        normalize_host(value)


@pytest.mark.asyncio
async def test_hostname_connection_and_signed_commands(system):
    hass, c, control, entry, registers = system
    data = dict(entry.data)
    assert await validate_input(hass, data) == {"title": "Pylontech H3X Bridge"}
    assert await c.async_set_charge_discharge_power(-100)
    result = await c.client.read_holding_registers(30161, 3, 2)
    assert result[0] == 1  # Emulator's measured charging state.
    assert registers[(2, 40901)] == 65436
    assert registers[(2, 40907)] == 4
    assert await c.async_set_charge_discharge_power(100)
    assert (await c.client.read_holding_registers(30161, 1, 2))[0] == 2
    assert not await c.async_set_charge_discharge_power(-1001)
    control.settings["charge_method"] = "time_slot_pn_customer"
    assert await c.async_set_charge_discharge_power(-100)
    assert registers[(2, 40901)] == 65436
    assert registers[(2, 40926)] == 0  # Automation reference semantics stay direct.


@pytest.mark.asyncio
async def test_dns_failure_is_contained(system, monkeypatch):
    hass, c, control, entry, registers = system
    monkeypatch.setattr(
        asyncio,
        "open_connection",
        AsyncMock(side_effect=socket.gaierror("no DNS record")),
    )
    with pytest.raises(CannotConnect):
        await validate_input(hass, {"host": "does-not-exist.invalid", "port": 502})
    assert not c.client.connected


@pytest.mark.asyncio
async def test_options_probe_releases_existing_connection(system):
    hass, c, control, entry, registers = system
    assert await c.client.connect()
    old = c.client
    await validate_input(hass, dict(entry.data), c)
    assert not old.connected
    assert c.client is not old
    assert await c.async_set_charge_discharge_power(-100)


@pytest.mark.asyncio
async def test_poll_stops_on_first_transport_failure(system):
    hass, c, control, entry, registers = system
    c.client.read_holding_registers = AsyncMock(
        side_effect=ModbusTransportError("connection lost")
    )
    with pytest.raises(ModbusTransportError):
        await c.safe_read(30100, 2, 2)
    assert c.client.read_holding_registers.await_count == 1


def test_optional_entity_defaults_are_omitted():
    result = connection_schema()({"host": "localhost", "port": 502})
    assert "weather_alert_entity" not in result
    assert "grid_power_entity" not in result


@pytest.mark.asyncio
async def test_manual_override_blocks_optimizer_and_stops(system):
    hass, c, control, entry, registers = system
    control.manual_until = datetime.now(timezone.utc) + timedelta(hours=1)
    await control.async_tick(c.data)
    assert registers[(2, 40901)] > 32767
    assert not await c.async_set_charge_discharge_power(500)
    assert not await c.async_write_register(40907, 0)
    assert not (await c.async_clear_time_slot(slot=4))["success"]
    await control.async_set("force_charge", False)
    assert registers[(2, 40901)] == 0
    assert registers[(2, 40907)] == 0


@pytest.mark.asyncio
async def test_negative_dashboard_value_latches_manual_override(system):
    hass, c, control, entry, registers = system
    c.async_refresh = AsyncMock()
    description = next(
        item for item in NUMBER_TYPES if item.key == "charge_discharge_power"
    )
    number = PylontechNumber(c, description, entry)
    number.async_set_context(Context(user_id="test-user"))
    await number.async_set_native_value(-10)
    assert control.manual_until is not None
    assert control.settings["force_charge_rate"] == 10
    assert c.async_refresh.await_count == 1
    await control.async_tick(c.data)
    assert registers[(2, 40901)] == 65436


@pytest.mark.asyncio
async def test_grid_headroom_and_unavailable_meter(system):
    hass, c, control, entry, registers = system
    control.options["grid_power_entity"] = "sensor.shelly_grid"
    control.manual_until = datetime.now(timezone.utc) + timedelta(hours=1)
    control.settings["force_charge_rate"] = 100
    hass.states.async_set("sensor.shelly_grid", "16.5", {"unit_of_measurement": "kW"})
    await control.async_tick(c.data)
    raw = registers[(2, 40901)] - 65536
    assert -500 <= raw * 13800 / 1000 < 0
    hass.states.async_set("sensor.shelly_grid", "unknown")
    await control.async_tick(c.data)
    assert registers[(2, 40901)] == 0
    assert "grid power unavailable" in control.reason


@pytest.mark.asyncio
async def test_override_respects_half_c_limit(system):
    hass, c, control, entry, registers = system
    control.manual_until = datetime.now(timezone.utc) + timedelta(hours=1)
    control.settings["force_charge_rate"] = 100
    c.data["battery_system_capacity"] = 10.24
    await control.async_tick(c.data)
    watts = (65536 - registers[(2, 40901)]) * 13800 / 1000
    assert 0 < watts <= 5120


@pytest.mark.asyncio
async def test_conflicting_schedules_are_suspended_and_restored(system):
    hass, c, control, entry, registers = system
    original = [1, 0x0800, 0x0B00, 1, 200, 127]
    for offset, value in enumerate(original):
        registers[(2, 40908 + offset)] = value
    control.manual_until = datetime.now(timezone.utc) + timedelta(hours=1)
    await control.async_tick(c.data)
    assert registers[(2, 40908)] == 0
    control.manual_until = None
    await control.async_tick(c.data)
    assert [registers[(2, 40908 + offset)] for offset in range(6)] == original


@pytest.mark.asyncio
async def test_weather_charge_hold_siren_and_restore(system):
    hass, c, control, entry, registers = system
    alerts, beeps = [], []

    async def notification(call):
        alerts.append(call.data)

    async def siren(call):
        beeps.append(call.data)

    hass.services.async_register("persistent_notification", "create", notification)
    hass.services.async_register("siren", "turn_on", siren)
    control.options.update(
        weather_alert_entity="sensor.knmi", alert_siren_entity="siren.chime"
    )
    control.settings["weather_reserve"] = True
    hass.states.async_set("sensor.knmi", "Code oranje")
    await control.async_tick(c.data)
    assert control.weather_active
    assert registers[(2, 40902)] == 100
    assert registers[(2, 40901)] > 32767
    assert len(alerts) == len(beeps) == 1
    c.data["battery_soc"] = 100
    await control.async_tick(c.data)
    assert 0 <= registers[(2, 40901)] <= 50
    assert len(beeps) == 1
    c.data["battery_soc"] = 90
    await control.async_tick(c.data)
    assert registers[(2, 40901)] == 0
    hass.states.async_set("sensor.knmi", "unavailable")
    await control.async_tick(c.data)
    assert control.weather_active
    hass.states.async_set("sensor.knmi", "Code groen")
    await control.async_tick(c.data)
    assert not control.weather_active
    assert registers[(2, 40902)] == 95
    assert registers[(2, 40907)] == 0


@pytest.mark.asyncio
async def test_soc_and_temperature_stops(system):
    hass, c, control, entry, registers = system
    control.manual_until = datetime.now(timezone.utc) + timedelta(hours=1)
    await control.async_tick(c.data)
    c.data["bms_temperature"] = 0
    await control.async_tick(c.data)
    assert registers[(2, 40901)] == 0
    c.data["bms_temperature"] = 25
    c.data["battery_soc"] = 95
    await control.async_tick(c.data)
    assert not control.active
    assert registers[(2, 40907)] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,mode", [("time_slot_pn_customer", 5), ("time_slot_user", 4)]
)
async def test_slot_modes_and_stop(system, method, mode):
    hass, c, control, entry, registers = system
    control.settings["charge_method"] = method
    control.settings["force_charge_rate"] = 10
    control.manual_until = datetime.now(timezone.utc) + timedelta(hours=1)
    await control.async_tick(c.data)
    assert registers[(2, 40907)] == mode
    assert registers[(2, 40926)] == 1
    assert registers[(2, 40929)] == 0
    assert registers[(2, 40930)] == 100
    control.manual_until = None
    await control.async_tick(c.data)
    assert registers[(2, 40926)] == 0
    assert await c.async_set_charge_discharge_power(100)
    assert registers[(2, 40907)] == 4


def test_warning_expiry_and_unknown():
    now = datetime.now(timezone.utc)
    state = SimpleNamespace(
        state="alerts",
        attributes={
            "alerts": [
                {"color": "red", "expires": (now - timedelta(minutes=1)).isoformat()},
                {"color": "yellow", "expires": (now + timedelta(hours=1)).isoformat()},
            ]
        },
    )
    assert warning_level(state, now) == 1
    assert warning_level(SimpleNamespace(state="unknown", attributes={}), now) is None


@pytest.mark.asyncio
async def test_expired_manual_session_stops_after_reload(system):
    hass, c, control, entry, registers = system
    assert await c.async_set_charge_discharge_power(-100)
    control.store.async_load.return_value = {
        "owns_command": True,
        "previous_charge_soc": 90,
    }
    await control.async_load()
    await control.async_tick(c.data)
    assert registers[(2, 40901)] == 0
    assert registers[(2, 40902)] == 90
    assert registers[(2, 40907)] == 0


@pytest.mark.parametrize(
    "text,level",
    [("Code geel", 1), ("orange", 2), ("Code rood", 3), ("green", 0), ("2", None)],
)
def test_warning_codes(text, level):
    assert (
        warning_level(
            SimpleNamespace(state=text, attributes={}), datetime.now(timezone.utc)
        )
        == level
    )
