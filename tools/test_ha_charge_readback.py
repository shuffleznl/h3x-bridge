"""Never open an inspection socket until HA confirms the bridge unloaded."""

from types import SimpleNamespace

import pytest

import ha_charge_readback as inspection
from test_ha_charge_validation import healthy_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("read_fails,unload_fails", [(False, False), (True, False), (False, True)])
async def test_exclusive_read_only_socket_and_restore(monkeypatch, tmp_path, read_fails, unload_fails):
    events = []
    paused = False

    class DeviceException(Exception):
        pass

    class FakeModbus:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self):
            assert paused
            events.append("connect")
            return True

        async def read_holding_registers(self, address, count, slave):
            assert paused
            events.append("read")
            if read_fails:
                raise TimeoutError("read timed out")
            return [0] * count

        def close(self):
            events.append("close")

    class FakeHA:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def snapshot(self):
            return healthy_snapshot()

        async def request(self, path):
            assert path == "/api/config/config_entries/entry"
            return [{"entry_id": "test", "domain": "pylontech_h3x_bridge",
                     "state": "not_loaded" if paused else "loaded",
                     "disabled_by": "user" if paused else None}]

    async def command(client, data):
        nonlocal paused
        if data["disabled_by"]:
            events.append("disable")
            paused = not unload_fails
            return {"require_restart": unload_fails}
        events.append("enable")
        paused = False
        return {"require_restart": False}

    async def no_sleep(*args):
        pass

    fake_module = SimpleNamespace(H3XModbusTcpClient=FakeModbus, ModbusDeviceException=DeviceException)
    monkeypatch.setattr(inspection, "HAClient", FakeHA)
    monkeypatch.setattr(inspection, "websocket_command", command)
    monkeypatch.setattr(inspection.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(inspection.importlib.util, "spec_from_file_location", lambda *args: SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None)))
    monkeypatch.setattr(inspection.importlib.util, "module_from_spec", lambda spec: fake_module)
    token_file = tmp_path / "credential"
    token_file.write_text("test-token", encoding="utf-8")
    args = SimpleNamespace(
        pause_bridge_for_read_only_inspection=True, modbus_host="not-used",
        modbus_port=502, bridge_entry_id="test", token_file=token_file,
        output=tmp_path / "report", url="http://not-used",
    )
    if read_fails or unload_fails:
        with pytest.raises((RuntimeError, TimeoutError)):
            await inspection.run(args)
    else:
        await inspection.run(args)
    assert events[0] == "disable"
    assert events[-2:] == ["close", "enable"]
    assert not paused
    if unload_fails:
        assert "connect" not in events
    else:
        assert events.index("disable") < events.index("connect") < events.index("read")
