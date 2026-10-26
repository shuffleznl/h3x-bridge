"""Record live H3X evidence through HA only; never connect to the inverter."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

import aiohttp

BRIDGE = "pylontech_h3x_bridge_"
IMPORTANT_ATTRIBUTES = {
    "unit_of_measurement", "options", "register", "raw_reference",
    "register_word_hex", "requested_reference_raw", "readback_raw",
    "readback_mode", "readback_matches", "write_acknowledged", "command_error",
    "charge_headroom_w", "grid_without_battery_w", "grid_limit_w",
    "grid_margin_w", "battery_power_cap_w", "requested_charge_rate_percent",
    "requested_charge_w", "permitted_charge_w", "target_reference_raw",
    "requested_power_percent", "readback_reference_percent", "power_control_path",
    "charging_response", "charging_observations", "telemetry_available",
    "measured_battery_status", "measured_battery_power_w", "last_control_error",
    "last_power_command_error", "last_rejected_command", "grid_power_entity",
    "charge_method", "manual_charge_expires", "reason", "applied", "apply_error",
    "entity", "raw_state", "raw_unit", "input_status", "source_age_seconds",
    "battery_circuit_rating", "battery_circuit_limit_w", "connection_rating",
    "slot_clock_basis", "slot_device_clock", "slot_requested_clock",
    "slot_clock_offset_seconds", "slot_start", "slot_end", "slot_readback_matches",
    "slot_readback_values", "slot_readback_ems_mode", "slot_error",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def select_states(states):
    """Exclude unrelated home states and large optimizer plans from reports."""
    sources = set()
    for state in states:
        if state["entity_id"] == f"sensor.{BRIDGE}charge_control_status":
            source = state.get("attributes", {}).get("grid_power_entity")
            if source:
                sources.add(source)
    result = {}
    for state in states:
        entity = state["entity_id"]
        relevant = BRIDGE in entity or entity in sources
        relevant |= bool(re.search(
            r"h3x_(?:predictive_dispatch|energy_arbitrage)_"
            r"(?:automatic_control|decision|target_power|grid_diagnostics_status)$", entity
        ))
        relevant |= "shellypro3em" in entity and any(
            part in entity for part in ("current", "power", "voltage")
        )
        if relevant:
            result[entity] = {
                "state": state["state"],
                "last_updated": state.get("last_updated"),
                "last_reported": state.get("last_reported"),
                "attributes": {
                    key: value for key, value in state.get("attributes", {}).items()
                    if key in IMPORTANT_ATTRIBUTES
                },
            }
    return result


def assess(states):
    def value(key):
        return states.get(f"sensor.{BRIDGE}{key}", {}).get("state")

    status = states.get(f"sensor.{BRIDGE}charge_control_status", {})
    attrs = status.get("attributes", {})
    power = numeric(value("battery_power"))
    requested = numeric(attrs.get("permitted_charge_w"))
    measured = max(0, -power) if power is not None else None
    # A status bit plus idle-sized trickle is not proof of useful forced charging.
    tracking_threshold = max(200, requested * 0.2) if requested else 200
    return {
        "battery_status": value("battery_status"),
        "battery_power_w": power,
        "battery_current_a": numeric(value("battery_current")),
        "soc_percent": numeric(value("battery_soc")),
        "temperature_c": numeric(value("bms_temperature")),
        "requested_charge_w": requested,
        "measured_charge_w": measured,
        "substantial_charging": (
            value("battery_status") == "Charging" and measured is not None
            and measured >= tracking_threshold and attrs.get("telemetry_available") is True
        ),
        "control_status": status.get("state"),
        "reported_confirmation": attrs.get("charging_response"),
        "reference_readback_percent": attrs.get("readback_reference_percent"),
        "control_error": attrs.get("last_control_error"),
    }


class HAClient:
    """Authenticated HTTP with no redirects, automatic command retries or token output."""

    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=aiohttp.ClientTimeout(total=90, connect=10),
        )
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def request(self, path, data=None):
        method = "GET" if data is None else "POST"
        async with self.session.request(
            method, self.url + path, json=data, allow_redirects=False
        ) as response:
            body = await response.text()
            if response.status >= 300:
                # Limit HA's error body and defensively redact the credential.
                detail = body.replace(self.token, "[redacted]")[:600]
                error = RuntimeError(f"HA {method} {path}: HTTP {response.status}: {detail}")
                error.status = response.status
                raise error
            if response.content_type == "application/json":
                return json.loads(body)
            return body

    async def snapshot(self):
        states = select_states(await self.request("/api/states"))
        return {"captured_at": utc_now(), "assessment": assess(states), "states": states}


def append_json(path, record):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")


async def run(args):
    token = args.token_file.read_text(encoding="utf-8-sig").strip()
    if not token:
        raise ValueError("Token file is empty")
    args.output.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    end = loop.time() + args.duration_minutes * 60
    collected = 0
    async with HAClient(args.url, token) as client:
        while True:
            try:
                snapshot = await client.snapshot()
                append_json(args.output / "samples.jsonl", snapshot)
                collected += 1
                print(json.dumps({"at": snapshot["captured_at"], **snapshot["assessment"]}), flush=True)
            except (aiohttp.ClientError, TimeoutError, RuntimeError) as err:
                if getattr(err, "status", None) in {400, 401, 403}:
                    raise
                failure = {"at": utc_now(), "event": "read_error", "detail": str(err).replace(token, "[redacted]")}
                append_json(args.output / "read-errors.jsonl", failure)
                print(json.dumps(failure), flush=True)
            if loop.time() >= end:
                break
            await asyncio.sleep(min(args.interval_seconds, max(0, end - loop.time())))
        try:
            log = await client.request("/api/error_log")
            relevant = []
            for block in re.split(r"(?=^\d{4}-\d{2}-\d{2} )", log, flags=re.M):
                if re.search(r"h3x_bridge|Modbus|modbus", block):
                    relevant.append(block.replace(token, "[redacted]"))
            (args.output / "bridge-errors.txt").write_text("".join(relevant), encoding="utf-8")
        except (RuntimeError, aiohttp.ClientError, TimeoutError) as err:
            print(f"Log read unavailable: {err}", flush=True)
    if not collected:
        raise RuntimeError("No Home Assistant telemetry samples collected")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--url", required=True)
    result.add_argument("--token-file", type=Path, required=True)
    result.add_argument("--duration-minutes", type=float, default=0)
    result.add_argument("--interval-seconds", type=float, default=20)
    result.add_argument("--output", type=Path, default=Path("live-validation") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    if arguments.duration_minutes < 0 or arguments.interval_seconds < 5:
        raise SystemExit("Duration must be nonnegative and interval at least 5 seconds")
    asyncio.run(run(arguments))
