"""Explicit, bounded live charge tests through Home Assistant's existing bridge.

Never retries an uncertain charge command. Stops the override on every exit and
restores local preferences, but deliberately does not resume a preexisting force
charge. This is operator tooling, not an unattended battery controller.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import time

from ha_live_probe import HAClient, append_json, numeric, parser, utc_now

PREFIX = "pylontech_h3x_bridge_"
FORCE = f"switch.{PREFIX}force_charge"
RATE = f"number.{PREFIX}force_charge_rate"
METHOD = f"select.{PREFIX}charge_method"
REFERENCE = f"number.{PREFIX}charge_discharge_power_ref"
WEATHER = f"switch.{PREFIX}weather_reserve"
METHODS = ("user_reference", "time_slot_pn_customer", "time_slot_user")


def state(snapshot, entity):
    return snapshot["states"].get(entity, {}).get("state")


def check_safety(snapshot, *, active=False):
    states = snapshot["states"]
    for entity, item in states.items():
        if entity.endswith("_automatic_control") and item["state"] != "off":
            raise RuntimeError(f"Competing control is not disabled: {entity}")
    if state(snapshot, WEATHER) != "off":
        raise RuntimeError("Weather reserve is enabled; do not override it")
    attrs = states.get(f"sensor.{PREFIX}charge_control_status", {}).get("attributes", {})
    if attrs.get("telemetry_available") is not True:
        raise RuntimeError(f"Bridge telemetry unavailable: {attrs.get('last_control_error', 'unknown cause')}")
    assessment = snapshot["assessment"]
    if assessment.get("battery_status") not in {"Charging", "Discharging", "Idle", "Standby", "Run"}:
        raise RuntimeError("Battery status missing, faulted, asleep or offline")
    soc, temp = assessment["soc_percent"], assessment["temperature_c"]
    charge_limit = numeric(state(snapshot, f"number.{PREFIX}charge_limit_soc"))
    if soc is None or charge_limit is None or not 10 <= soc < min(85, charge_limit - 2):
        raise RuntimeError("SOC missing or outside test headroom (10..85%, below charge limit)")
    if temp is None or not 5 <= temp <= 40:
        raise RuntimeError("BMS temperature missing or outside 5..40 C")
    now = time.time()
    for entity in (f"sensor.{PREFIX}battery_power", f"sensor.{PREFIX}battery_soc"):
        reported = states.get(entity, {}).get("last_reported")
        if not reported:
            raise RuntimeError(f"No freshness timestamp: {entity}")
        if not -5 <= now - datetime.fromisoformat(reported).timestamp() <= 65:
            raise RuntimeError(f"Stale telemetry: {entity}")
    grid_entity = attrs.get("grid_power_entity")
    grid_state = states.get(grid_entity, {})
    grid = numeric(grid_state.get("state"))
    unit = grid_state.get("attributes", {}).get("unit_of_measurement")
    if grid is None or unit not in {"W", "kW"}:
        raise RuntimeError("Independent grid measurement missing")
    grid *= 1000 if unit == "kW" else 1
    reported = grid_state.get("last_reported")
    if not reported or not -5 <= now - datetime.fromisoformat(reported).timestamp() <= 65:
        raise RuntimeError("Independent grid measurement stale")
    if grid > 16500:
        raise RuntimeError("Grid import exceeds conservative 16.5 kW test ceiling")
    power = assessment["battery_power_w"]
    if power is None or power < -11000 or power > 1500:
        raise RuntimeError("Battery power missing or outside charge-only test envelope")
    if active and state(snapshot, FORCE) != "on":
        raise RuntimeError("Force charge unexpectedly stopped or lost ownership")
    if attrs.get("last_control_error") not in (None, "none"):
        raise RuntimeError(f"Bridge control error: {attrs['last_control_error']}")


async def run_test(args):
    if not args.allow_charge_tests:
        raise ValueError("Active testing requires --allow-charge-tests")
    if not 60 <= args.hold_seconds <= 600:
        raise ValueError("Hold duration must be 60..600 seconds")
    token = args.token_file.read_text(encoding="utf-8-sig").strip()
    args.output.mkdir(parents=True, exist_ok=True)
    events = args.output / "events.jsonl"
    samples = args.output / "samples.jsonl"
    initial = None
    owns_test = False
    started = asyncio.get_running_loop().time()
    results = []

    def event(kind, **fields):
        item = {"at": utc_now(), "event": kind, **fields}
        append_json(events, item)
        print(json.dumps(item), flush=True)

    async with HAClient(args.url, token) as client:
        async def snapshot(label):
            item = await client.snapshot()
            attrs = item["states"].get(f"sensor.{PREFIX}charge_control_status", {}).get("attributes", {})
            entities = [f"sensor.{PREFIX}battery_soc", f"sensor.{PREFIX}battery_power"]
            if attrs.get("grid_power_entity"):
                entities.append(attrs["grid_power_entity"])
            # HA caches REST State JSON for unchanged values. Read the live
            # in-memory report times through a read-only template, not that cache.
            template = (
                "{ {% for entity in " + json.dumps(entities) + " %}"
                "{{ entity | to_json }}:{{ states[entity].last_reported.isoformat() | to_json }}"
                "{% if not loop.last %},{% endif %}{% endfor %} }"
            )
            reports = await client.request("/api/template", {"template": template})
            if isinstance(reports, str):
                reports = json.loads(reports)
            for entity, reported in reports.items():
                item["states"][entity]["last_reported"] = reported
            item["freshness_source"] = "HA in-memory last_reported via read-only template"
            item["stage"] = label
            append_json(samples, item)
            return item

        async def service(domain, name, entity, **values):
            event("service_start", domain=domain, service=name, entity=entity, values=values)
            # There is intentionally no retry: HTTP failure does not imply no write.
            await client.request(f"/api/services/{domain}/{name}", {"entity_id": entity, **values})
            event("service_returned", domain=domain, service=name, entity=entity)

        async def stop():
            await service("switch", "turn_off", FORCE)
            item = await snapshot("stopped")
            if state(item, FORCE) != "off" or numeric(state(item, REFERENCE)) != 0:
                raise RuntimeError("Stop not verified: override must be off and reference zero")
            if state(item, f"select.{PREFIX}ems_mode") != "Self-Consumption":
                raise RuntimeError("Stop not verified: EMS did not return to Self-Consumption")
            event("stop_verified", ems=state(item, f"select.{PREFIX}ems_mode"))

        try:
            initial = await snapshot("initial")
            check_safety(initial)
            if state(initial, FORCE) != "off":
                raise RuntimeError("Start from verified Force Charge off baseline")
            owns_test = True
            combined = getattr(args, "combined_reference_slot", False)
            methods = METHODS[1:] if combined else METHODS
            rates = (10,) if combined else (5, 10, 20)
            for method in methods:
                await stop()
                if combined:
                    await service("select", "select_option", METHOD, option="user_reference")
                    await service("number", "set_value", REFERENCE, value=-10)
                    check_safety(await snapshot("combined_direct_reference_prepared"), active=True)
                await service("select", "select_option", METHOD, option=method)
                for rate in rates:
                    if asyncio.get_running_loop().time() - started > 2700:
                        raise RuntimeError("45-minute absolute test deadline reached")
                    check_safety(await snapshot("pre_command"))
                    if method == "user_reference" and rate == 5:
                        await service("number", "set_value", REFERENCE, value=-rate)
                    else:
                        await service("number", "set_value", RATE, value=rate)
                        if state(await snapshot("rate_set"), FORCE) != "on":
                            await service("switch", "turn_on", FORCE)
                    label = f"{method}:{rate}%"
                    end = asyncio.get_running_loop().time() + args.hold_seconds
                    observed = []
                    while True:
                        item = await snapshot(label)
                        check_safety(item, active=True)
                        if combined and numeric(state(item, REFERENCE)) != -10:
                            raise RuntimeError("Combined test lost its verified -10% signed reference")
                        observed.append(item["assessment"])
                        event("sample", stage=label, **item["assessment"])
                        remaining = end - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            break
                        await asyncio.sleep(min(20, remaining))
                    stable = observed[2:] or observed
                    result = {
                        "stage": label, "samples": len(observed),
                        "min_charge_w": min(x["measured_charge_w"] for x in stable),
                        "max_charge_w": max(x["measured_charge_w"] for x in stable),
                        "mean_charge_w": sum(x["measured_charge_w"] for x in stable) / len(stable),
                        "useful_charge_samples": sum(x["substantial_charging"] for x in stable),
                        "soc_start": observed[0]["soc_percent"], "soc_end": observed[-1]["soc_percent"],
                    }
                    results.append(result)
                    event("stage_complete", **result)
                await stop()
        except BaseException as err:
            event("test_failed", error=type(err).__name__, detail=str(err).replace(token, "[redacted]"))
            raise
        finally:
            # Stop first, then restore non-actuating preferences. Never resume an
            # old 100% request after an inconclusive or failed test.
            try:
                if owns_test:
                    await stop()
                    old_rate, old_method = state(initial, RATE), state(initial, METHOD)
                    if old_rate is not None:
                        await service("number", "set_value", RATE, value=float(old_rate))
                    if old_method in METHODS:
                        await service("select", "select_option", METHOD, option=old_method)
                    await snapshot("final_restored_preferences_force_off")
                    event("cleanup_complete", force_charge="off", results=results)
            except BaseException as err:
                event("CLEANUP_FAILED_MANUAL_STOP_REQUIRED", detail=str(err).replace(token, "[redacted]"))
                raise


if __name__ == "__main__":
    arguments = parser()
    arguments.add_argument("--allow-charge-tests", action="store_true")
    arguments.add_argument("--hold-seconds", type=int, default=120)
    arguments.add_argument("--combined-reference-slot", action="store_true")
    asyncio.run(run_test(arguments.parse_args()))
