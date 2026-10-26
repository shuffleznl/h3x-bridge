"""Read documented interlocks only while HA's bridge is verified unloaded.

No Modbus write operations exist in this tool. The bridge is re-enabled after
closing the inspection socket, including after a failed read.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

from ha_live_probe import HAClient, append_json, parser, utc_now


async def websocket_command(client, command):
    async with client.session.ws_connect(client.url + "/api/websocket", timeout=90) as ws:
        greeting = await ws.receive_json(timeout=10)
        if greeting.get("type") != "auth_required":
            raise RuntimeError("Unexpected HA WebSocket greeting")
        await ws.send_json({"type": "auth", "access_token": client.token})
        if (await ws.receive_json(timeout=10)).get("type") != "auth_ok":
            raise RuntimeError("HA WebSocket authentication failed")
        await ws.send_json({"id": 1, **command})
        response = await ws.receive_json(timeout=90)
        if response.get("id") != 1 or not response.get("success"):
            raise RuntimeError("HA entry operation failed")
        return response["result"]


async def run(args):
    if not args.pause_bridge_for_read_only_inspection:
        raise ValueError("Explicit --pause-bridge-for-read-only-inspection required")
    transport_path = Path(__file__).parents[1] / "custom_components/pylontech_h3x_bridge/transport.py"
    spec = importlib.util.spec_from_file_location("inspection_transport", transport_path)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    modbus = transport.H3XModbusTcpClient(args.modbus_host, args.modbus_port, timeout=5)
    token = args.token_file.read_text(encoding="utf-8-sig").strip()
    args.output.mkdir(parents=True, exist_ok=True)

    def record(event, **fields):
        data = {"at": utc_now(), "event": event, **fields}
        append_json(args.output / "readback.jsonl", data)
        print(json.dumps(data), flush=True)

    async with HAClient(args.url, token) as client:
        entries = await client.request("/api/config/config_entries/entry")
        entry = next(e for e in entries if e["entry_id"] == args.bridge_entry_id)
        if entry["domain"] != "pylontech_h3x_bridge" or entry["state"] != "loaded" or entry["disabled_by"]:
            raise RuntimeError("Expected one enabled and loaded Pylontech H3X Bridge entry")
        snapshot = await client.snapshot()
        for entity, state in snapshot["states"].items():
            if entity.endswith(("_force_charge", "_weather_reserve", "_automatic_control")) and state["state"] != "off":
                raise RuntimeError(f"Control still active: {entity}")
        reference = snapshot["states"].get("number.pylontech_h3x_bridge_charge_discharge_power_ref", {}).get("state")
        if reference != "0":
            raise RuntimeError("Zero signed reference must be verified before pausing the bridge")
        try:
            result = await websocket_command(client, {
                "type": "config_entries/disable", "entry_id": args.bridge_entry_id,
                "disabled_by": "user",
            })
            if result.get("require_restart"):
                raise RuntimeError("Bridge could not be unloaded; refusing direct connection")
            entries = await client.request("/api/config/config_entries/entry")
            paused = next(e for e in entries if e["entry_id"] == args.bridge_entry_id)
            if paused["state"] != "not_loaded" or paused["disabled_by"] != "user":
                raise RuntimeError("Bridge unload not verified; refusing direct connection")
            record("bridge_unloaded")
            await asyncio.sleep(2)
            if not await modbus.connect():
                raise RuntimeError("Read-only inspection endpoint unreachable")
            for name, slave, address, count in (
                ("pcs_model", 2, 30018, 8),
                ("pcs_firmware", 2, 30039, 2),
                ("grid_connection_state", 2, 30160, 1),
                ("battery_telemetry", 2, 30161, 5),
                ("inverter_charge_interlocks", 2, 30166, 3),
                ("ems_and_reference", 2, 40901, 7),
                ("device_grid_import_limit", 2, 40937, 2),
                ("export_control", 2, 40400, 3),
                ("slot_1", 2, 40908, 6),
                ("slot_2", 2, 40914, 6),
                ("slot_3", 2, 40920, 6),
                ("slot_4", 2, 40926, 6),
                ("inverter_clock", 2, 40932, 4),
                ("bms_voltage", 1, 0x1403, 1),
                ("bms_charge_voltage_current_limits", 1, 0x1409, 3),
                ("bms_switching_flags", 1, 0x140F, 1),
                ("bms_charge_discharge_forbidden", 1, 0x1438, 2),
                ("bms_error_code", 1, 0x1432, 2),
            ):
                try:
                    registers = await modbus.read_holding_registers(address, count, slave)
                    record("registers", name=name, slave=slave, address=address, values=registers)
                except transport.ModbusDeviceException as err:
                    record("unsupported_register", name=name, address=address, detail=str(err))
                await asyncio.sleep(0.15)
        finally:
            modbus.close()
            await asyncio.sleep(1)
            result = await websocket_command(client, {
                "type": "config_entries/disable", "entry_id": args.bridge_entry_id,
                "disabled_by": None,
            })
            entries = await client.request("/api/config/config_entries/entry")
            restored = next(e for e in entries if e["entry_id"] == args.bridge_entry_id)
            record("bridge_restored", state=restored["state"], require_restart=result.get("require_restart"))
            if restored["state"] != "loaded" or result.get("require_restart"):
                raise RuntimeError("Bridge reload failed; manual attention required")


if __name__ == "__main__":
    arguments = parser()
    arguments.add_argument("--bridge-entry-id", required=True)
    arguments.add_argument("--modbus-host", required=True)
    arguments.add_argument("--modbus-port", type=int, default=502)
    arguments.add_argument("--pause-bridge-for-read-only-inspection", action="store_true")
    asyncio.run(run(arguments.parse_args()))
