"""Bounded manual charging and KNMI warning reserve control."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import logging
import math

from homeassistant.helpers.storage import Store
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .protocol import shift_elapsed

OVERRIDE_OWNER = ContextVar("h3x_override_owner", default=False)
LOGGER = logging.getLogger(__name__)
LEVELS = {
    "green": 0,
    "groen": 0,
    "yellow": 1,
    "geel": 1,
    "orange": 2,
    "oranje": 2,
    "red": 3,
    "rood": 3,
}
DEFAULTS = {
    "force_charge_rate": 20,
    "weather_reserve": False,
    "weather_threshold": "orange",
    "weather_discharge_rate": 5,
    "charge_method": "user_reference",
}


def warning_level(state, now: datetime) -> int | None:
    """Read a local warning code or NL Weather alert list; ignore expired alerts."""
    if state is None or state.state in {"unknown", "unavailable"}:
        return None

    def parse(value):
        return LEVELS.get(str(value).lower().replace("code ", "").strip())

    attributes = state.attributes
    alerts = attributes.get("alerts")
    if isinstance(alerts, list):
        levels = []
        for alert in alerts:
            if not isinstance(alert, dict):
                continue
            expires = alert.get("expires") or alert.get("end") or alert.get("end_time")
            if expires:
                try:
                    end = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                    if end.tzinfo is None:
                        return None
                    if end <= now:
                        continue
                except (ValueError, TypeError):
                    return None
            level = parse(alert.get("color", alert.get("code", alert.get("severity"))))
            if level is None:
                return None
            levels.append(level)
        return max(levels, default=0)
    for value in (state.state, attributes.get("code"), attributes.get("color")):
        level = parse(value)
        if level is not None:
            return level
    return None


class ChargeOverride:
    """Own control while charging or retaining an emergency weather reserve."""

    def __init__(self, hass, coordinator, entry):
        self.hass, self.coordinator, self.entry = hass, coordinator, entry
        self.options = {**entry.data, **entry.options}
        self.settings = dict(DEFAULTS)
        self.store = Store(hass, 1, f"pylontech_h3x_bridge.{entry.entry_id}.controls")
        self.manual_until = None
        self.weather_active = False
        self.weather_full = False
        self.reason = "automatic control"
        self._saved_charge_soc = None
        self._last_command = None
        self._slot_until = None
        self._slot_active = False
        self._beeped = False
        self._owns_command = False
        self._saved_slots = None
        self._schedules_prepared = False
        self.power_budget = {}
        self.charging_response = "not_requested"
        self.charging_observations = 0
        self.last_error = None
        self._lock = asyncio.Lock()

    @property
    def active(self):
        return (
            self.manual_until is not None or self.weather_active or self._owns_command
        )

    async def async_load(self):
        saved = await self.store.async_load()
        if saved:
            self.settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
            self._saved_charge_soc = saved.get("previous_charge_soc")
            self._owns_command = bool(saved.get("owns_command"))
            self.weather_active = (
                bool(saved.get("weather_active")) and self.settings["weather_reserve"]
            )
            self._slot_active = bool(saved.get("slot_active"))
            self.weather_full = bool(saved.get("weather_full"))
            self._saved_slots = saved.get("previous_slots")

    async def _save(self):
        await self.store.async_save(
            {
                **self.settings,
                "previous_charge_soc": self._saved_charge_soc,
                "owns_command": self._owns_command,
                "weather_active": self.weather_active,
                "slot_active": self._slot_active,
                "weather_full": self.weather_full,
                "previous_slots": self._saved_slots,
            }
        )

    async def _prepare_schedules(self):
        """Keep built-in schedules from overriding a manual/weather command."""
        if self._schedules_prepared:
            return
        c = self.coordinator
        if self._saved_slots is None:
            async with c._modbus_lock:
                await c._ensure_connected()
                slots = []
                for address in range(40908, 40932, 6):
                    slots.extend(await c.client.read_holding_registers(address, 6, 2))
                self._saved_slots = slots
            await self._save()
        for offset in range(0, 24, 6):
            if self._saved_slots[offset] and not await c.async_write_register(
                40908 + offset, 0
            ):
                raise RuntimeError("Failed to suspend conflicting inverter schedule")
        self._schedules_prepared = True

    async def _restore_schedules(self):
        if self._saved_slots is None:
            return
        c = self.coordinator
        async with c._modbus_lock:
            for offset in range(0, 24, 6):
                base = 40908 + offset
                values = self._saved_slots[offset : offset + 6]
                if not await c._write_registers_locked(base + 1, values[1:], 2):
                    raise RuntimeError("Failed to restore inverter schedule settings")
                if not await c._write_register_locked(
                    base, values[0], 2, raw_value=values[0], signed=False
                ):
                    raise RuntimeError("Failed to restore inverter schedule enable")
        self._saved_slots = None
        self._schedules_prepared = False

    async def async_set(self, key, value, *, refresh=True):
        async with self._lock:
            if (
                key == "charge_method"
                and value != self.settings["charge_method"]
                and self._slot_active
            ):
                token = OVERRIDE_OWNER.set(True)
                try:
                    await self._command(0)
                finally:
                    OVERRIDE_OWNER.reset(token)
            if key == "force_charge":
                self.manual_until = (
                    (datetime.now(timezone.utc) + timedelta(hours=2)) if value else None
                )
            else:
                self.settings[key] = value
            if self.active:
                self._owns_command = True
            self._last_command = None
            await self._save()
        if refresh:
            await self.coordinator.async_refresh()
            if not self.coordinator.last_update_success:
                raise HomeAssistantError(f"Charge control failed: {self.coordinator.last_exception}")
            if key == "force_charge" and value and self.reason.startswith("paused:"):
                raise HomeAssistantError(self.reason)

    def diagnostics(self):
        return {
            "force_charge": int(self.manual_until is not None),
            "weather_reserve": int(self.settings["weather_reserve"]),
            "control_owner": "weather_reserve"
            if self.weather_active
            else "force_charge"
            if self.manual_until
            else "restoring"
            if self._owns_command
            else "automatic",
            "charge_control_status": self.reason,
        }

    async def async_slot_charge(self, percent):
        """Refresh a short owned slot; charge direction is in the slot mode."""
        now = dt_util.now()
        if (
            self._slot_until
            and now < self._slot_until
            and self._last_command == -round(percent * 10)
        ):
            return True
        end = shift_elapsed(now, timedelta(minutes=5))
        result = await self.coordinator.async_program_charge_slot(
            slot=4,
            power_percent=max(1, round(percent)),
            start=shift_elapsed(now, -timedelta(minutes=1)),
            end=end,
            ems_mode=5
            if self.settings["charge_method"] == "time_slot_pn_customer"
            else 4,
            clock_time=now,
            sync_clock=False,
        )
        if result.get("deferred"):
            if not await self.coordinator.async_set_charge_discharge_power(0):
                return False
            self.reason = "paused: inverter clock midnight boundary"
            self.power_budget.update(target_reference_raw=0, permitted_charge_w=0)
            return True
        if not result["success"]:
            self.coordinator.last_slot_command["slot_error"] = result.get("error", "Charge-slot programming failed")
        if result["success"]:
            self._slot_until = now.astimezone(timezone.utc) + timedelta(seconds=min(180, max(0, result["remaining_seconds"] - 30)))
            self._slot_active = True
            self._last_command = -round(percent * 10)
            await self._save()
        return result["success"]

    async def _command(self, raw):
        c = self.coordinator
        if raw >= 0 and self._slot_active:
            result = await c.async_clear_time_slot(slot=4)
            if not result["success"]:
                raise RuntimeError("Failed to disable owned charge slot")
            self._slot_active = False
            self._slot_until = None
        slot_due = (
            raw < 0
            and self.settings["charge_method"] != "user_reference"
            and (
                not self._slot_until or dt_util.now() >= self._slot_until
            )
        )
        if raw != self._last_command or slot_due:
            if not await c.async_set_charge_discharge_power(raw):
                detail = (c.last_slot_command.get("slot_error", "Charge-slot programming failed; check bridge logs")
                          if raw < 0 and self.settings["charge_method"] != "user_reference"
                          else c.last_power_command.get("command_error"))
                raise RuntimeError(detail or "Charge/discharge command failed")
            self._last_command = raw

    async def async_tick(self, data):
        async with self._lock:
            token = OVERRIDE_OWNER.set(True)
            try:
                self.last_error = None
                await self._tick(data)
            except Exception as err:
                self.last_error = str(err)
                self.reason = "command failed"
                self.charging_response = "command_failed"
                self.charging_observations = 0
                raise
            finally:
                if self.charging_response == "not_requested":
                    self.charging_observations = 0
                OVERRIDE_OWNER.reset(token)

    def _observe_charging(self, raw, data):
        """Keep control intent separate from measured charging confirmation."""
        if raw is None or raw >= 0:
            self.charging_response = "not_requested"
            self.charging_observations = 0
            return
        power = data.get("battery_power")
        charging = (data.get("battery_status") == "Charging"
                    and isinstance(power, (int, float)) and math.isfinite(power)
                    and power <= -50)
        self.charging_observations = self.charging_observations + 1 if charging else 0
        self.charging_response = (
            "charging_confirmed" if self.charging_observations >= 2
            else "awaiting_measured_charging"
        )

    async def _tick(self, data):
        c = self.coordinator
        self.power_budget = {}
        self.charging_response = "not_requested"
        now = datetime.now(timezone.utc)
        source = self.hass.states.get(self.options.get("weather_alert_entity", ""))
        level = warning_level(source, now)
        threshold = LEVELS[self.settings["weather_threshold"]]
        enabled = self.settings["weather_reserve"]
        # Keep an existing reserve if the warning source fails; never invent green.
        weather = enabled and (
            level >= threshold if level is not None else self.weather_active
        )
        if not weather:
            self.weather_full = False
            self._beeped = False
        previous_weather = self.weather_active
        self.weather_active = weather
        if previous_weather and not weather and self._saved_charge_soc is not None:
            if not await c.async_write_register(40902, self._saved_charge_soc):
                raise RuntimeError("Failed to restore charge SOC limit")
            data["charge_limit_soc"] = self._saved_charge_soc
            self._saved_charge_soc = None
            await self._save()
        if self.manual_until and (
            now >= self.manual_until
            or data.get("battery_soc", 0) >= data.get("charge_limit_soc", 100)
        ):
            self.manual_until = None
        if self.manual_until is None and not self.weather_active:
            if self._owns_command:
                await self._command(0)
                data["charge_discharge_power"] = 0
                await self._restore_schedules()
                if self._saved_charge_soc is not None:
                    if not await c.async_write_register(40902, self._saved_charge_soc):
                        raise RuntimeError("Failed to restore charge SOC limit")
                    self._saved_charge_soc = None
                if not await c.async_write_register(40907, 0):
                    raise RuntimeError("Failed to restore self-consumption mode")
                data["ems_mode"] = "0"
                self._last_command = None
                self._owns_command = False
                await self._save()
            self.reason = (
                "warning source unavailable"
                if enabled and level is None
                else "automatic control"
            )
            self._observe_charging(data.get("charge_discharge_power", 0), data)
            return
        if weather and not self._beeped:
            try:
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "notification_id": f"h3x_weather_{self.entry.entry_id}",
                        "title": "H3X weather reserve",
                        "message": "KNMI warning threshold reached. Charging to 100% then limiting discharge.",
                    },
                    blocking=True,
                )
                siren = self.options.get("alert_siren_entity")
                self._beeped = True
                if siren:
                    await self.hass.services.async_call(
                        "siren",
                        "turn_on",
                        {"entity_id": siren, "duration": 2},
                        blocking=True,
                    )
                self._beeped = True
            except Exception:
                LOGGER.exception("Unable to deliver weather reserve notification")
        self._owns_command = True
        await self._prepare_schedules()
        soc, temp = data.get("battery_soc"), data.get("bms_temperature")
        if soc is None or temp is None or not 5 <= temp <= 45:
            await self._command(0)
            self.reason = (
                "paused: missing SOC/temperature or temperature outside 5..45 C"
            )
            return
        if weather and self._saved_charge_soc is None:
            self._saved_charge_soc = data.get("charge_limit_soc")
            if self._saved_charge_soc is None:
                await self._command(0)
                self.reason = "paused: charge SOC limit unavailable"
                return
            await self._save()
        if weather and data.get("charge_limit_soc") != 100:
            if not await c.async_write_register(40902, 100):
                raise RuntimeError("Failed to set full-charge SOC limit")
        if weather and soc >= 100 and not self.weather_full:
            self.weather_full = True
            await self._save()
        rated = self.options.get("inverter_rated_power_w", 13800)
        maximum = min(self.options.get("battery_power_limit_w", 11000), rated)
        capacity = data.get("battery_system_capacity")
        if capacity is not None and capacity > 0:
            maximum = min(maximum, capacity * 500)  # 0.5C upper bound.
        grid_entity = self.options.get("grid_power_entity")
        grid = data.get("grid_total_power")
        if grid_entity:
            state = self.hass.states.get(grid_entity)
            try:
                age = (
                    now - getattr(state, "last_reported", state.last_updated)
                ).total_seconds()
                unit = state.attributes.get("unit_of_measurement")
                grid = float(state.state) * (1000 if unit == "kW" else 1)
                if age > 120 or unit not in {"W", "kW"} or not math.isfinite(grid):
                    grid = None
            except (ValueError, TypeError, AttributeError):
                grid = None
        if grid is None:
            await self._command(0)
            self.reason = "paused: grid power unavailable or stale"
            return
        # Remove the battery's present contribution before calculating headroom.
        base_grid = grid + data.get("battery_power", 0)
        headroom = max(
            0, self.options.get("grid_connection_limit_w", 17500) - 500 - base_grid
        )
        self.power_budget = {
            "grid_power_w": grid, "battery_power_w": data.get("battery_power"),
            "grid_without_battery_w": base_grid,
            "grid_limit_w": self.options.get("grid_connection_limit_w", 17500),
            "grid_margin_w": 500, "charge_headroom_w": round(headroom),
            "battery_power_cap_w": maximum,
            "requested_charge_rate_percent": self.settings["force_charge_rate"],
        }
        if weather and self.weather_full:
            demand = max(0, grid + data.get("ac_total_power", 0))
            watts = min(
                maximum, rated * self.settings["weather_discharge_rate"] / 100, demand
            )
            raw = math.floor(watts / rated * 1000) if soc > 90 else 0
            self.reason = "weather reserve: slow discharge, 90% SOC floor"
        else:
            percent = 100 if weather else self.settings["force_charge_rate"]
            watts = min(maximum, rated * percent / 100, headroom)
            # Round down; never round a constrained command above headroom.
            raw = -math.floor(watts / rated * 100) * 10
            self.reason = (
                "weather reserve: charge to 100%" if weather else "manual force charge"
            )
            self.power_budget["requested_charge_w"] = rated * percent / 100
            self.power_budget["permitted_charge_w"] = watts
            if raw == 0:
                self.reason = (
                    f"paused: charging power below minimum {rated / 100:.0f} W; "
                    f"headroom {headroom:.0f} W, battery cap {maximum:.0f} W"
                )
        self.power_budget["target_reference_raw"] = raw
        if raw:
            expected_mode = (
                5
                if raw < 0 and self.settings["charge_method"] == "time_slot_pn_customer"
                else 4
            )
            if str(data.get("ems_mode")) != str(expected_mode) or (
                self.settings["charge_method"] == "user_reference"
                and data.get("charge_discharge_power") != raw
            ):
                self._last_command = None
        await self._command(raw)
        self._observe_charging(self.power_budget.get("target_reference_raw", raw), data)
