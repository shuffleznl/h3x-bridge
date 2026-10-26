"""Safety/cleanup tests for the operator's explicitly enabled live test tool."""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import ha_charge_validation as validation


def healthy_snapshot():
    now = datetime.now(timezone.utc).isoformat()
    states = {}
    for entity, value in {
        validation.FORCE: "off", validation.WEATHER: "off", validation.RATE: "100",
        validation.REFERENCE: "0", validation.METHOD: "user_reference",
        "select.pylontech_h3x_bridge_ems_mode": "Self-Consumption",
        "switch.pylontech_h3x_predictive_dispatch_automatic_control": "off",
        "sensor.pylontech_h3x_bridge_battery_soc": "54",
        "sensor.pylontech_h3x_bridge_battery_power": "0",
        "number.pylontech_h3x_bridge_charge_limit_soc": "100",
        "sensor.grid": "1500",
    }.items():
        states[entity] = {"state": value, "last_reported": now, "attributes": {}}
    states["sensor.grid"]["attributes"] = {"unit_of_measurement": "W"}
    states["sensor.pylontech_h3x_bridge_charge_control_status"] = {
        "state": "automatic control", "attributes": {
            "grid_power_entity": "sensor.grid", "telemetry_available": True,
            "last_control_error": "none",
        },
    }
    return {"states": states, "assessment": {
        "soc_percent": 54, "temperature_c": 28, "battery_power_w": 0, "battery_status": "Idle",
    }}


def test_preflight_requires_no_competing_control_and_fresh_sensors():
    snapshot = healthy_snapshot()
    validation.check_safety(snapshot)
    snapshot["states"][validation.WEATHER]["state"] = "on"
    with pytest.raises(RuntimeError, match="Weather reserve"):
        validation.check_safety(snapshot)
    snapshot = healthy_snapshot()
    snapshot["states"]["sensor.grid"]["last_reported"] = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat()
    with pytest.raises(RuntimeError, match="measurement stale"):
        validation.check_safety(snapshot)


@pytest.mark.parametrize("field,value", [("soc_percent", 85), ("temperature_c", 0), ("battery_power_w", -12000)])
def test_preflight_rejects_unsafe_envelope(field, value):
    snapshot = healthy_snapshot()
    snapshot["assessment"][field] = value
    with pytest.raises(RuntimeError):
        validation.check_safety(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("combined", [False, True])
async def test_uncertain_command_is_not_retried_and_cleanup_leaves_force_off(monkeypatch, tmp_path, combined):
    calls = []

    class FakeClient:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def snapshot(self):
            return copy.deepcopy(healthy_snapshot())

        async def request(self, path, data=None):
            if path == "/api/template":
                return {}
            calls.append((path, data))
            if path == "/api/services/number/set_value" and data["entity_id"] == validation.REFERENCE:
                raise TimeoutError("uncertain write")
            return []

    monkeypatch.setattr(validation, "HAClient", FakeClient)
    token_file = tmp_path / "credential"
    token_file.write_text("test-token", encoding="utf-8")
    with pytest.raises(TimeoutError, match="uncertain write"):
        await validation.run_test(SimpleNamespace(
            allow_charge_tests=True, hold_seconds=60, token_file=token_file,
            output=tmp_path / "report", url="http://not-used", combined_reference_slot=combined,
        ))
    negative = [data for path, data in calls if data["entity_id"] == validation.REFERENCE]
    assert negative == [{"entity_id": validation.REFERENCE, "value": -10 if combined else -5}]
    assert sum(path == "/api/services/switch/turn_off" for path, _ in calls) == 2
    assert not any(path == "/api/services/switch/turn_on" for path, _ in calls)
    assert "cleanup_complete" in (tmp_path / "report/events.jsonl").read_text()
    assert "test-token" not in (tmp_path / "report/events.jsonl").read_text()
