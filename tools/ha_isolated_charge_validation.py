"""Run the actual local HA coordinator on hardware with the installed bridge off.

Slot-only testing is the default. The explicit combined diagnostic also sends a
small signed reference, but first lowers the device SOC ceiling to at most five
percentage points above current SOC. That conservative device ceiling is restored
only after a zero-reference stop is verified.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1]))

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.pylontech_h3x_bridge.coordinator import PylontechCoordinator
from custom_components.pylontech_h3x_bridge.override import ChargeOverride, OVERRIDE_OWNER
from ha_charge_readback import websocket_command
from ha_live_probe import HAClient, append_json, numeric, parser, utc_now


async def run(args):
    if not args.allow_isolated_slot_tests or not 60 <= args.hold_seconds <= 600:
        raise ValueError("Explicit isolated test permission and a 60..600-second hold required")
    if any(not 1 <= rate <= 20 for rate in args.rates):
        raise ValueError("Only low rates 1..20% are allowed")
    rated_power = args.inverter_rated_power_w
    if not 1000 <= rated_power <= 30000:
        raise ValueError("An explicitly verified inverter Pn rating is required")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    token = args.token_file.read_text(encoding="utf-8-sig").strip()

    def record(event, **values):
        item = {"at": utc_now(), "event": event, **values}
        append_json(args.output / "events.jsonl", item)
        print(json.dumps(item), flush=True)

    async with HAClient(args.url, token) as remote:
        initial = await remote.snapshot()
        for entity, value in initial["states"].items():
            if entity.endswith(("_force_charge", "_weather_reserve", "_automatic_control")) and value["state"] != "off":
                raise RuntimeError(f"Control still active: {entity}")
        if initial["states"].get("number.pylontech_h3x_bridge_charge_discharge_power_ref", {}).get("state") != "0":
            raise RuntimeError("Installed bridge must have a verified zero reference")
        grid_entity = initial["states"]["sensor.pylontech_h3x_bridge_charge_control_status"]["attributes"]["grid_power_entity"]
        config = await remote.request("/api/config")
        # This changes only this isolated Python process, never the user's HA.
        old_zone = dt_util.DEFAULT_TIME_ZONE
        dt_util.set_default_time_zone(ZoneInfo(config["time_zone"]))
        hass = HomeAssistant(str(args.output / "isolated-ha"))
        coordinator = PylontechCoordinator(hass, args.modbus_host, args.modbus_port)
        coordinator.slot_clock_basis = "inverter_clock"
        control = None
        disabled = False
        original_soc_limit = None

        async def update_grid():
            state = await remote.request(f"/api/states/{grid_entity}")
            template = "{{ states[" + json.dumps(grid_entity) + "].last_reported.isoformat() }}"
            reported = await remote.request("/api/template", {"template": template})
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(reported)).total_seconds()
            unit = state.get("attributes", {}).get("unit_of_measurement")
            power = numeric(state.get("state"))
            if power is None or unit not in {"W", "kW"} or not -5 <= age <= 60:
                raise RuntimeError("Independent Shelly measurement invalid or stale")
            watts = power * (1000 if unit == "kW" else 1)
            if watts > 16500:
                raise RuntimeError("Grid import above conservative 16.5 kW test ceiling")
            hass.states.async_set(grid_entity, state["state"], state["attributes"])
            return watts

        async def stop_local():
            if control is None:
                return
            owner = OVERRIDE_OWNER.set(True)
            try:
                control.manual_until = None
                await control._command(0)
                await control._restore_schedules()
                if not await coordinator.async_write_register(40907, 0):
                    raise RuntimeError("Failed to restore Self-Consumption")
                words = await coordinator.client.read_holding_registers(40901, 7, 2)
                if words[0] != 0 or words[6] != 0:
                    raise RuntimeError("Stop read-back failed")
                control._owns_command = False
                control._last_command = None
                control._slot_until = None
                record("stop_verified", reference=0, ems_mode=0)
            finally:
                OVERRIDE_OWNER.reset(owner)

        class JournalStore:
            async def async_save(self, data):
                append_json(args.output / "recovery.jsonl", {"at": utc_now(), **data})

        try:
            entries = await remote.request("/api/config/config_entries/entry")
            entry = next(e for e in entries if e["entry_id"] == args.bridge_entry_id)
            if entry["domain"] != "pylontech_h3x_bridge" or entry["state"] != "loaded" or entry["disabled_by"]:
                raise RuntimeError("Expected an enabled loaded bridge")
            disabled = True  # Restore even if the disable response is uncertain.
            result = await websocket_command(remote, {
                "type": "config_entries/disable", "entry_id": args.bridge_entry_id, "disabled_by": "user",
            })
            entries = await remote.request("/api/config/config_entries/entry")
            entry = next(e for e in entries if e["entry_id"] == args.bridge_entry_id)
            if result.get("require_restart") or entry["state"] != "not_loaded":
                raise RuntimeError("Bridge unload not verified; refusing Modbus connection")
            record("installed_bridge_unloaded", local_timezone=config["time_zone"])
            await asyncio.sleep(2)
            await update_grid()
            await coordinator.async_refresh()
            if not coordinator.last_update_success:
                raise RuntimeError(f"Preflight poll failed: {coordinator.last_exception}")
            capacity = coordinator.data.get("battery_system_capacity")
            if not capacity or max(args.rates) * rated_power / 100 > min(11000, capacity * 500):
                raise RuntimeError("Unknown pack capacity or requested power exceeds 11 kW / 0.5C test limit")
            forbidden = await coordinator.client.read_holding_registers(0x1438, 1, 1)
            if forbidden[0]:
                raise RuntimeError("BMS forbids charging")
            combined = getattr(args, "combined_reference_slot", False)
            reference_first = getattr(args, "reference_before_mode", False)
            if combined and reference_first:
                raise RuntimeError("Choose one diagnostic variant at a time")
            if combined or reference_first:
                original_soc_limit = coordinator.data["charge_limit_soc"]
                temporary_limit = min(original_soc_limit, max(50, min(80, coordinator.data["battery_soc"] + 5)))
                record("device_soc_backstop", original=original_soc_limit, temporary=temporary_limit)
                if not await coordinator.async_write_register(40902, temporary_limit):
                    raise RuntimeError("Failed to establish conservative SOC backstop")
                if (await coordinator.client.read_holding_registers(40902, 1, 2))[0] != temporary_limit:
                    raise RuntimeError("SOC backstop read-back failed")
            entry = SimpleNamespace(entry_id="isolated-test", data={
                "host": args.modbus_host, "port": args.modbus_port,
                "inverter_rated_power_w": rated_power, "battery_power_limit_w": 11000,
                "grid_connection_limit_w": 17500, "grid_power_entity": grid_entity,
            }, options={})
            control = coordinator.override = ChargeOverride(hass, coordinator, entry)
            control.store = JournalStore()
            deadline = asyncio.get_running_loop().time() + 1800
            for method in (["user_reference"] if reference_first else args.methods):
                await stop_local()
                control.settings["charge_method"] = method
                for rate in args.rates:
                    if asyncio.get_running_loop().time() > deadline:
                        raise RuntimeError("30-minute isolated test deadline reached")
                    data = coordinator.data
                    if not 10 <= data.get("battery_soc", -1) < 80 or not 5 <= data.get("bms_temperature", -1) <= 40:
                        raise RuntimeError("Pre-command SOC/temperature outside test envelope")
                    control.settings["force_charge_rate"] = rate
                    control._last_command = None
                    if combined:
                        await stop_local()
                        control.settings["charge_method"] = "user_reference"
                        await update_grid()
                        await control.async_set("force_charge", True, refresh=True)
                        await control.async_set("charge_method", method, refresh=False)
                    await control.async_set("force_charge", True, refresh=False)
                    if reference_first:
                        owner = OVERRIDE_OWNER.set(True)
                        try:
                            grid = await update_grid()
                            base_grid = grid + coordinator.data.get("battery_power", 0)
                            if 17000 - base_grid < rate * rated_power / 100:
                                raise RuntimeError("Insufficient grid headroom for reference-order comparison")
                            await control._prepare_schedules()
                            raw = -rate * 10
                            write_function = getattr(args, "reference_write_function", 6)
                            if write_function == 16:
                                async with coordinator._modbus_lock:
                                    written = await coordinator._write_registers_locked(40901, [raw & 0xFFFF], 2)
                            else:
                                written = await coordinator.async_write_register(40901, raw, signed=True)
                            if not written:
                                raise RuntimeError("Reference-first diagnostic write failed")
                            if not await coordinator.async_write_register(40907, 4):
                                raise RuntimeError("Reference-first User-mode activation failed")
                            words = await coordinator.client.read_holding_registers(40901, 7, 2)
                            if words[0] != (raw & 0xFFFF) or words[6] != 4:
                                raise RuntimeError("Reference-first diagnostic read-back failed")
                            control._last_command = raw
                            record("reference_before_mode_verified", raw=raw, mode=4, function_code=write_function)
                        finally:
                            OVERRIDE_OWNER.reset(owner)
                    stage = f"{method}:{rate}%"
                    end = asyncio.get_running_loop().time() + args.hold_seconds
                    while True:
                        grid = await update_grid()
                        await coordinator.async_refresh()
                        if not coordinator.last_update_success:
                            raise RuntimeError(f"Live poll/control failed: {coordinator.last_exception}")
                        data = coordinator.data
                        soc, temp, power = (data.get(key) for key in ("battery_soc", "bms_temperature", "battery_power"))
                        if soc is None or not 10 <= soc < 80 or temp is None or not 5 <= temp <= 40:
                            raise RuntimeError("SOC/temperature outside test envelope")
                        if power is None or not -11000 <= power <= 1500:
                            raise RuntimeError("Battery power outside charge-only test envelope")
                        reference = data.get("charge_discharge_power")
                        if not combined and not reference_first and reference != 0:
                            raise RuntimeError("Unexpected direct reference: isolated test permits slots only")
                        if (combined or reference_first) and reference != control.power_budget.get("target_reference_raw"):
                            raise RuntimeError("Combined reference did not match the safety-capped target")
                        async with coordinator._modbus_lock:
                            bms_flags = (await coordinator.client.read_holding_registers(0x140F, 1, 1))[0]
                        if (bms_flags & 7) == 4:
                            raise RuntimeError("BMS basic status reports fault")
                        record("sample", stage=stage, battery_status=data.get("battery_status"),
                               battery_power_w=power, battery_current_a=data.get("battery_current"),
                               soc=soc, temperature_c=temp, grid_power_w=grid,
                               signed_reference_raw=reference,
                               bms_switching_flags=bms_flags, bms_basic_status=bms_flags & 7,
                               power_budget=control.power_budget, slot=coordinator.last_slot_command)
                        remaining = end - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            break
                        await asyncio.sleep(min(15, remaining))
                await stop_local()
            record("test_completed")
        except BaseException as err:
            record("test_failed", detail=str(err).replace(token, "[redacted]"),
                   slot=coordinator.last_slot_command, power=coordinator.last_power_command)
            raise
        finally:
            cleanup_error = None
            try:
                await stop_local()
                if original_soc_limit is not None:
                    if not await coordinator.async_write_register(40902, original_soc_limit):
                        raise RuntimeError("Could not restore original charge SOC ceiling")
                    if (await coordinator.client.read_holding_registers(40902, 1, 2))[0] != original_soc_limit:
                        raise RuntimeError("Original charge SOC ceiling restoration not verified")
                    record("device_soc_limit_restored", value=original_soc_limit)
            except BaseException as err:
                cleanup_error = err
                record("STOP_FAILED_SLOT_EXPIRY_IS_BACKSTOP", detail=str(err).replace(token, "[redacted]"))
            await coordinator.async_close()
            await hass.async_stop()
            dt_util.set_default_time_zone(old_zone)
            if disabled:
                await asyncio.sleep(2)
                result = await websocket_command(remote, {
                    "type": "config_entries/disable", "entry_id": args.bridge_entry_id, "disabled_by": None,
                })
                entries = await remote.request("/api/config/config_entries/entry")
                restored = next(e for e in entries if e["entry_id"] == args.bridge_entry_id)
                record("installed_bridge_restored", state=restored["state"], require_restart=result.get("require_restart"))
                if restored["state"] != "loaded" or result.get("require_restart"):
                    raise RuntimeError("Installed bridge reload needs attention")
            if cleanup_error:
                raise cleanup_error


if __name__ == "__main__":
    arguments = parser()
    arguments.add_argument("--allow-isolated-slot-tests", action="store_true")
    arguments.add_argument("--bridge-entry-id", required=True)
    arguments.add_argument("--modbus-host", required=True)
    arguments.add_argument("--modbus-port", type=int, default=502)
    arguments.add_argument("--inverter-rated-power-w", type=int, required=True)
    arguments.add_argument("--hold-seconds", type=int, default=120)
    arguments.add_argument("--combined-reference-slot", action="store_true")
    arguments.add_argument("--reference-before-mode", action="store_true")
    arguments.add_argument("--reference-write-function", type=int, choices=[6, 16], default=6)
    arguments.add_argument("--rates", type=int, nargs="+", default=[10])
    arguments.add_argument("--methods", nargs="+", choices=["time_slot_user", "time_slot_pn_customer"], default=["time_slot_user", "time_slot_pn_customer"])
    asyncio.run(run(arguments.parse_args()))
