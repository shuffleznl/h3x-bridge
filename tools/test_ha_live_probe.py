"""Live-validation tooling tests do not contact a battery or real HA instance."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from aiohttp import web

TOOLS = Path(__file__).parent
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("ha_live_probe", TOOLS / "ha_live_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_small_trickle_is_not_force_charge_confirmation():
    states = {
        "sensor.pylontech_h3x_bridge_battery_power": {"state": "-63"},
        "sensor.pylontech_h3x_bridge_battery_status": {"state": "Charging"},
        "sensor.pylontech_h3x_bridge_charge_control_status": {
            "state": "manual force charge",
            "attributes": {"permitted_charge_w": 13800, "telemetry_available": True},
        },
    }
    assert probe.assess(states)["substantial_charging"] is False
    states["sensor.pylontech_h3x_bridge_battery_power"]["state"] = "-3000"
    assert probe.assess(states)["substantial_charging"] is True
    states["sensor.pylontech_h3x_bridge_battery_power"]["state"] = "unavailable"
    assert probe.assess(states)["measured_charge_w"] is None


def test_filter_excludes_private_states_and_plans():
    states = [
        {"entity_id": "person.private", "state": "home"},
        {"entity_id": "sensor.pylontech_h3x_bridge_charge_control_status", "state": "idle",
         "attributes": {"grid_power_entity": "sensor.grid", "permitted_charge_w": 750}},
        {"entity_id": "sensor.grid", "state": "2.5", "attributes": {"unit_of_measurement": "kW"}},
        {"entity_id": "sensor.pylontech_h3x_predictive_dispatch_decision", "state": "idle",
         "attributes": {"plan": list(range(1000)), "reason": "hold"}},
    ]
    selected = probe.select_states(states)
    assert "person.private" not in selected
    assert "sensor.grid" in selected
    assert selected["sensor.pylontech_h3x_predictive_dispatch_decision"]["attributes"] == {"reason": "hold"}


@pytest.mark.asyncio
async def test_probe_only_reads_and_does_not_follow_redirects():
    requests = []

    async def handler(request):
        requests.append((request.method, request.path))
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.path == "/api/states":
            return web.json_response([])
        if request.path == "/redirect":
            raise web.HTTPFound("/should-not-be-called")
        return web.Response(status=401, text="test-token")

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with probe.HAClient(f"http://127.0.0.1:{port}", "test-token") as client:
            assert (await client.snapshot())["states"] == {}
            with pytest.raises(RuntimeError, match="HTTP 302"):
                await client.request("/redirect")
            with pytest.raises(RuntimeError) as error:
                await client.request("/unauthorized")
            assert "test-token" not in str(error.value)
        assert requests == [("GET", "/api/states"), ("GET", "/redirect"), ("GET", "/unauthorized")]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_read_only_soak_recovers_after_transient_read_failure(monkeypatch, tmp_path):
    calls = 0

    class FlakyHA:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def snapshot(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("restart in progress")
            return {"captured_at": probe.utc_now(), "assessment": {}, "states": {}}

        async def request(self, path):
            assert path == "/api/error_log"
            return ""

    monkeypatch.setattr(probe, "HAClient", FlakyHA)
    credential = tmp_path / "credential"
    credential.write_text("test-token")
    await probe.run(SimpleNamespace(
        token_file=credential, output=tmp_path / "report", duration_minutes=0.002,
        interval_seconds=0.05, url="http://not-used",
    ))
    assert calls > 1
    assert "restart in progress" in (tmp_path / "report/read-errors.jsonl").read_text()
    assert (tmp_path / "report/samples.jsonl").read_text()
