"""Exercise real HA entities/control logic against a local Modbus TCP emulator."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
from custom_components.pylontech_h3x_bridge.transport import (
    ModbusTransportError,
    ModbusDeviceException,
)
from custom_components.pylontech_h3x_bridge.protocol import encode_realtime_registers, align_charge_window, shift_elapsed
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
    assert c.data["ems_mode"] == "0"


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
async def test_manual_negative_does_not_require_a_24_register_read(system):
    hass, c, control, entry, registers = system
    read = c.client.read_holding_registers

    async def limited_read(address, count, slave):
        if address == 40908 and count > 6:
            raise ModbusDeviceException(3, 2)
        return await read(address, count, slave)

    c.client.read_holding_registers = limited_read
    number = PylontechNumber(
        c, next(d for d in NUMBER_TYPES if d.key == "charge_discharge_power"), entry
    )
    number.async_set_context(Context(user_id="test-user"))
    await number.async_set_native_value(-10)
    assert registers[(2, 40901)] == 0xFF9C


@pytest.mark.asyncio
async def test_signed_register_wire_bytes_match_manufacturer(system, monkeypatch):
    hass, c, control, entry, registers = system
    await c._ensure_connected()
    frames = []
    write = c.client._writer.write

    def capture(frame):
        frames.append(frame)
        return write(frame)

    monkeypatch.setattr(c.client._writer, "write", capture)
    assert await c.async_set_charge_discharge_power(-100)
    assert bytes.fromhex("02069fc5ff9c") in [frame[6:] for frame in frames]
    assert c.last_power_command["readback_raw"] == -100
    assert c.last_power_command["readback_matches"] is True
    frames.clear()
    assert await c.async_set_charge_discharge_power(100)
    assert bytes.fromhex("02069fc50064") in [frame[6:] for frame in frames]


@pytest.mark.asyncio
@pytest.mark.parametrize("reference,mode", [(0, 4), (65436, 0)])
async def test_acknowledged_but_unapplied_command_is_not_success(
    system, reference, mode
):
    hass, c, control, entry, registers = system
    read = c.client.read_holding_registers

    async def unapplied_read(address, count, slave):
        result = await read(address, count, slave)
        if address == 40901 and count == 7:
            result[0], result[6] = reference, mode
        return result

    c.client.read_holding_registers = unapplied_read
    assert not await c.async_set_charge_discharge_power(-100)
    assert c.last_power_command["write_acknowledged"] is True
    assert c.last_power_command["readback_matches"] is False
    assert c._power_command_sequence == 0


@pytest.mark.asyncio
async def test_status_charging_without_measured_power_is_not_confirmed(system):
    hass, c, control, entry, registers = system
    control.manual_until = datetime.now(timezone.utc) + timedelta(hours=1)
    c.data["battery_status"] = "Charging"
    c.data["battery_power"] = 0
    await control.async_tick(c.data)
    assert control.reason == "manual force charge"
    assert control.charging_response == "awaiting_measured_charging"


@pytest.mark.asyncio
async def test_charging_confirmation_needs_two_agreeing_observations(system):
    hass, c, control, entry, registers = system
    control.manual_until = datetime.now(timezone.utc) + timedelta(hours=1)
    for status, power in [("Idle", -100), ("Charging", -20), ("Charging", -1000)]:
        c.data.update(battery_status=status, battery_power=power)
        await control.async_tick(c.data)
        assert control.charging_response == "awaiting_measured_charging"
        assert control.reason == "manual force charge"
    await control.async_tick(c.data)
    assert control.charging_response == "charging_confirmed"
    c.data["battery_power"] = 0
    await control.async_tick(c.data)
    assert control.charging_response == "awaiting_measured_charging"
    assert control.reason == "manual force charge"


@pytest.mark.asyncio
async def test_force_charge_reports_zero_headroom_instead_of_success(system):
    hass, c, control, entry, registers = system
    control.options["grid_power_entity"] = "sensor.shelly_grid"
    hass.states.async_set("sensor.shelly_grid", "17500", {"unit_of_measurement": "W"})
    with pytest.raises(HomeAssistantError, match="below minimum"):
        await control.async_set("force_charge", True)
    assert control.power_budget["charge_headroom_w"] == 0
    assert control.power_budget["target_reference_raw"] == 0
    assert registers[(2, 40901)] == 0


@pytest.mark.asyncio
async def test_failed_preparation_is_not_reported_as_success(system):
    hass, c, control, entry, registers = system
    read = c.client.read_holding_registers

    async def failing_read(address, count, slave):
        if address == 40908:
            raise ModbusDeviceException(3, 2)
        return await read(address, count, slave)

    c.client.read_holding_registers = failing_read
    with pytest.raises(HomeAssistantError, match="Charge control failed"):
        await control.async_set("force_charge", True)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [4, 5])
async def test_slot_uses_inverter_rtc_when_ha_is_one_hour_ahead(system, mode):
    hass, c, control, entry, registers = system
    c.slot_clock_basis = "inverter_clock"
    ha_now = datetime(2026, 10, 26, 2, 10, tzinfo=ZoneInfo("Europe/Amsterdam"))
    device_now = datetime(2026, 10, 26, 1, 10)
    for address, value in zip(range(40932, 40936), encode_realtime_registers(device_now)):
        registers[(2, address)] = value
    registers[(2, 40907)] = mode
    for address, value in zip(range(40926, 40932), [1, 0x0209, 0x020F, 0, 100, 0x7F]):
        registers[(2, address)] = value
    assert (await c.client.read_holding_registers(30161, 1, 2))[0] == 3
    result = await c.async_program_charge_slot(
        slot=4, power_percent=10, start=ha_now - timedelta(minutes=1),
        end=ha_now + timedelta(minutes=5), ems_mode=mode, clock_time=ha_now,
    )
    assert result["success"] and result["slot_readback_matches"]
    assert result["slot_clock_offset_seconds"] == -3600
    assert result["start_register"] == 0x0109
    assert result["end_register"] == 0x010F
    assert registers[(2, 40934)] == 0x010A  # No unrequested clock write.
    assert (await c.client.read_holding_registers(30161, 1, 2))[0] == 1


def test_slot_clock_alignment_crosses_dates_and_handles_midnight():
    ha_now = datetime(2026, 10, 26, 1, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    start, end = align_charge_window(
        ha_now - timedelta(minutes=1), ha_now + timedelta(minutes=5),
        ha_now, datetime(2026, 10, 26, 0, 0, 10),
    )
    assert start == datetime(2026, 10, 26)
    assert end == datetime(2026, 10, 26, 0, 5, 10)
    with pytest.raises(ValueError, match="midnight"):
        align_charge_window(ha_now, ha_now + timedelta(minutes=5), ha_now, datetime(2026, 10, 25, 23, 59, 10))


def test_elapsed_slot_duration_across_dst_fallback():
    now = datetime(2026, 10, 25, 2, 58, tzinfo=ZoneInfo("Europe/Amsterdam"))
    end = shift_elapsed(now, timedelta(minutes=5))
    assert end.astimezone(timezone.utc) - now.astimezone(timezone.utc) == timedelta(minutes=5)
    start_device, end_device = align_charge_window(
        shift_elapsed(now, -timedelta(minutes=1)), end, now,
        now.astimezone(timezone.utc).replace(tzinfo=None),
    )
    assert end_device - start_device == timedelta(minutes=6)


@pytest.mark.asyncio
async def test_slot_invalid_clock_does_not_write_or_enable(system):
    hass, c, control, entry, registers = system
    registers[(2, 40932)] = 0
    now = datetime.now(timezone.utc)
    result = await c.async_program_charge_slot(
        slot=4, power_percent=10, start=now - timedelta(minutes=1),
        end=now + timedelta(minutes=5), ems_mode=5, clock_time=now,
    )
    assert not result["success"]
    assert registers[(2, 40926)] == 0
    assert registers[(2, 40907)] == 0


@pytest.mark.asyncio
async def test_owned_slot_reports_verified_mode_in_same_refresh(system):
    hass, c, control, entry, registers = system
    control.settings["charge_method"] = "time_slot_pn_customer"
    control.manual_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    await c.async_refresh()
    assert c.last_update_success
    assert c.data["ems_mode"] == "5"
    assert c.data["period_4"] == 1
    assert c.last_slot_command["slot_readback_matches"]


@pytest.mark.asyncio
async def test_rtc_midnight_pause_recovers_without_telemetry_failure(system):
    hass, c, control, entry, registers = system
    c.slot_clock_basis = "inverter_clock"
    control.settings["charge_method"] = "time_slot_user"
    control.manual_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    for address, value in zip(range(40932, 40936), encode_realtime_registers(datetime(2026, 10, 26, 23, 59, 20))):
        registers[(2, address)] = value
    await c.async_refresh()
    assert c.last_update_success
    assert "midnight" in control.reason
    assert control.charging_response == "not_requested"
    assert registers[(2, 40926)] == 0
    assert registers[(2, 40901)] == 0
    for address, value in zip(range(40932, 40936), encode_realtime_registers(datetime(2026, 10, 27, 0, 0, 20))):
        registers[(2, address)] = value
    await c.async_refresh()
    assert c.last_update_success
    assert registers[(2, 40926)] == 1


@pytest.mark.asyncio
async def test_default_slot_clock_basis_preserves_ha_local_time(system):
    hass, c, control, entry, registers = system
    now = datetime(2026, 10, 26, 2, 10, tzinfo=ZoneInfo("Europe/Amsterdam"))
    for address, value in zip(range(40932, 40936), encode_realtime_registers(datetime(2026, 10, 26, 1, 10))):
        registers[(2, address)] = value
    result = await c.async_program_charge_slot(
        slot=4, power_percent=10, start=now - timedelta(minutes=1), end=now + timedelta(minutes=5),
        ems_mode=4, clock_time=now,
    )
    assert result["success"]
    assert result["slot_clock_basis"] == "home_assistant_local"
    assert result["start_register"] == 0x0209
    assert result["end_register"] == 0x020F


@pytest.mark.asyncio
async def test_slot_readback_mismatch_disables_schedule(system):
    hass, c, control, entry, registers = system
    read = c.client.read_holding_registers

    async def wrong_readback(address, count, slave):
        result = await read(address, count, slave)
        if address == 40926:
            result[4] += 1
        return result

    c.client.read_holding_registers = wrong_readback
    now = datetime.now(timezone.utc)
    result = await c.async_program_charge_slot(
        slot=4, power_percent=10, start=now - timedelta(minutes=1),
        end=now + timedelta(minutes=5), ems_mode=5, clock_time=now,
    )
    assert not result["success"]
    assert registers[(2, 40926)] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("combined,reference_first,function", [(False, False, 6), (True, False, 6), (False, True, 6), (False, True, 16)])
async def test_isolated_live_tool_stops_and_restores_bridge_on_meter_failure(system, monkeypatch, tmp_path, combined, reference_first, function):
    import ha_isolated_charge_validation as isolated
    from test_ha_charge_validation import healthy_snapshot

    hass, c, control, entry, registers = system
    registers[(1, 0x1436)] = 6
    paused = False
    grid_reads = 0

    class FakeRemote:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def snapshot(self):
            return healthy_snapshot()

        async def request(self, path, data=None):
            nonlocal grid_reads
            if path == "/api/config":
                return {"time_zone": "Europe/Amsterdam"}
            if path == "/api/config/config_entries/entry":
                return [{"entry_id": "remote", "domain": "pylontech_h3x_bridge",
                         "state": "not_loaded" if paused else "loaded", "disabled_by": "user" if paused else None}]
            if path.startswith("/api/states/"):
                grid_reads += 1
                if grid_reads > (2 if reference_first else 1):
                    raise RuntimeError("meter failed")
                return {"state": "1500", "attributes": {"unit_of_measurement": "W"}}
            if path == "/api/template":
                return datetime.now(timezone.utc).isoformat()
            raise AssertionError(path)

    async def command(client, data):
        nonlocal paused
        paused = bool(data["disabled_by"])
        return {"require_restart": False}

    monkeypatch.setattr(isolated, "HAClient", FakeRemote)
    monkeypatch.setattr(isolated, "websocket_command", command)
    credential = tmp_path / "credential"
    credential.write_text("test-token")
    with pytest.raises(RuntimeError, match="meter failed"):
        await isolated.run(SimpleNamespace(
            allow_isolated_slot_tests=True, hold_seconds=60, rates=[10],
            methods=["time_slot_user"], token_file=credential, output=tmp_path / "live",
            url="http://unused", modbus_host="localhost", modbus_port=c.port, bridge_entry_id="remote",
            combined_reference_slot=combined,
            reference_before_mode=reference_first,
            reference_write_function=function,
            inverter_rated_power_w=15000,
        ))
    assert not paused
    assert registers[(2, 40901)] == 0
    assert registers[(2, 40926)] == 0
    assert registers[(2, 40907)] == 0
    assert registers[(2, 40902)] == 95
    if reference_first:
        assert "reference_before_mode_verified" in (tmp_path / "live/events.jsonl").read_text()


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
