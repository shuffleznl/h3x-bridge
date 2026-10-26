"""Force H3X Modbus protocol helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import struct


EMS_MODE_USER = 4
EMS_MODE_PN_CUSTOMER = 5

SLOT_MODE_CHARGE = 0
SLOT_MODE_DISCHARGE = 1

WEEKDAY_ALL = 0x7F

REGISTER_CHARGE_DISCHARGE_POWER = 40901
REGISTER_EMS_MODE = 40907
REGISTER_REALTIME_YEAR = 40932


@dataclass(frozen=True)
class TimeSlotRegisters:
    """Contiguous register addresses for one H3X time slot."""

    enable: int
    start_time: int
    end_time: int
    mode: int
    power: int
    weekday: int


def time_slot_registers(slot: int) -> TimeSlotRegisters:
    """Return register addresses for a 1-based time slot number."""
    if slot < 1 or slot > 4:
        raise ValueError(f"slot must be 1..4, got {slot}")

    base = 40908 + ((slot - 1) * 6)
    return TimeSlotRegisters(
        enable=base,
        start_time=base + 1,
        end_time=base + 2,
        mode=base + 3,
        power=base + 4,
        weekday=base + 5,
    )


def encode_percent_tenths(percent: int) -> int:
    """Encode a user-facing percent as the H3X 0.1%Pn register value."""
    if percent < 0 or percent > 100:
        raise ValueError(f"percent must be 0..100, got {percent}")
    return percent * 10


def encode_16bit_uint(value: int) -> int:
    """Encode an unsigned 16-bit register value."""
    if value < 0 or value > 0xFFFF:
        raise ValueError(f"U16 value out of range: {value}")
    return value


def encode_16bit_int(value: int) -> int:
    """Encode a signed 16-bit value as the Modbus register word."""
    if value < -0x8000 or value > 0x7FFF:
        raise ValueError(f"S16 value out of range: {value}")
    return struct.unpack(">H", struct.pack(">h", value))[0]


def encode_hhmm(value: datetime) -> int:
    """Encode time as high-byte hour and low-byte minute."""
    return (value.hour << 8) | value.minute


def encode_weekday_for_realtime(value: datetime) -> int:
    """Encode weekday for register 40935.

    The manufacturer doc says Sunday is the first day. Its Wednesday example
    uses low byte 3, so this maps Sunday=0, Monday=1, ... Saturday=6.
    """
    return (value.weekday() + 1) % 7


def encode_realtime_registers(value: datetime) -> list[int]:
    """Encode registers 40932-40935 from a timezone-aware datetime."""
    return [
        value.year,
        (value.month << 8) | value.day,
        encode_hhmm(value),
        (value.second << 8) | encode_weekday_for_realtime(value),
    ]


def decode_realtime_registers(registers: list[int]) -> datetime:
    """Decode the inverter's wall clock without assuming its timezone."""
    if len(registers) != 4 or not 2000 <= registers[0] <= 2099:
        raise ValueError("Invalid inverter clock registers")
    year, month_day, hour_minute, second_weekday = registers
    return datetime(
        year, month_day >> 8, month_day & 0xFF,
        hour_minute >> 8, hour_minute & 0xFF, second_weekday >> 8,
    )


def shift_elapsed(value: datetime, delta: timedelta) -> datetime:
    """Add elapsed time rather than wall-clock time across a DST change."""
    if value.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware")
    return (value.astimezone(timezone.utc) + delta).astimezone(value.tzinfo)


def align_charge_window(
    start: datetime, end: datetime, requested_now: datetime, device_now: datetime,
) -> tuple[datetime, datetime]:
    """Preserve relative timing using the device clock, including UTC/local skew."""
    if any(value.tzinfo is None for value in (start, end, requested_now)):
        raise ValueError("Charge-window timestamps must be timezone-aware")
    reference = requested_now.astimezone(timezone.utc)
    start_offset = start.astimezone(timezone.utc) - reference
    end_offset = end.astimezone(timezone.utc) - reference
    if not timedelta(0) < end_offset <= timedelta(hours=4):
        raise ValueError("Charge window must end within the next four hours")
    midnight = device_now.replace(hour=0, minute=0, second=0, microsecond=0)
    actual_start = max(device_now + start_offset, midnight)
    actual_end = min(device_now + end_offset, midnight + timedelta(days=1, minutes=-1))
    if actual_end <= device_now or encode_hhmm(actual_start) >= encode_hhmm(actual_end):
        raise ValueError("No safe charge window before inverter midnight")
    return actual_start, actual_end


def encode_time_slot_values(
    start: datetime,
    end: datetime,
    *,
    mode: int,
    power_percent: int,
    weekday_mask: int = WEEKDAY_ALL,
) -> list[int]:
    """Encode the five contiguous slot config registers after enable."""
    if mode not in (SLOT_MODE_CHARGE, SLOT_MODE_DISCHARGE):
        raise ValueError(f"slot mode must be 0 charge or 1 discharge, got {mode}")
    if weekday_mask < 0 or weekday_mask > 0x7F:
        raise ValueError(f"weekday mask must be 0..127, got {weekday_mask}")

    return [
        encode_hhmm(start),
        encode_hhmm(end),
        mode,
        encode_percent_tenths(power_percent),
        weekday_mask,
    ]
