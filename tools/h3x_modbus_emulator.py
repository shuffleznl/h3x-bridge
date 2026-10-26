#!/usr/bin/env python3
"""Minimal Pylontech Force H3X Modbus TCP emulator.

This is intentionally small and dependency-free so it can be used before a
Home Assistant/HACS release. It implements enough holding-register behavior for
Pylontech H3X Bridge development:

- function 3: read holding registers
- function 6: write single register
- function 16: write multiple registers

Use --duplicate-write-response to reproduce the observed H3X behavior where a
write ACK can be echoed again before the next read response.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import struct
from datetime import datetime
from collections import defaultdict
from collections.abc import MutableMapping


RegisterMap = MutableMapping[tuple[int, int], int]

LOGGER = logging.getLogger("h3x_modbus_emulator")


def u16(value: int) -> int:
    """Return value constrained to one unsigned Modbus register."""
    return value & 0xFFFF


def set_u16(registers: RegisterMap, slave: int, address: int, value: int) -> None:
    registers[(slave, address)] = u16(value)


def set_s16(registers: RegisterMap, slave: int, address: int, value: int) -> None:
    registers[(slave, address)] = struct.unpack(">H", struct.pack(">h", value))[0]


def set_s32(registers: RegisterMap, slave: int, address: int, value: int) -> None:
    high, low = struct.unpack(">HH", struct.pack(">i", value))
    registers[(slave, address)] = high
    registers[(slave, address + 1)] = low


def set_f32(registers: RegisterMap, slave: int, address: int, value: float) -> None:
    high, low = struct.unpack(">HH", struct.pack(">f", value))
    registers[(slave, address)] = high
    registers[(slave, address + 1)] = low


def build_registers() -> RegisterMap:
    """Build a deterministic register set based on the H3X register map."""
    registers: RegisterMap = defaultdict(int)

    # Slave 2: inverter.
    set_s32(registers, 2, 30100, 0)      # AC total power
    set_s32(registers, 2, 30108, 0)      # Grid total power
    set_u16(registers, 2, 30115, 5)      # Inverter status: Run

    set_u16(registers, 2, 30119, 4200)   # PV1 voltage 420.0 V
    set_u16(registers, 2, 30120, 25)     # PV1 current 2.5 A
    set_u16(registers, 2, 30121, 4100)
    set_u16(registers, 2, 30122, 20)
    set_u16(registers, 2, 30123, 0)
    set_u16(registers, 2, 30124, 0)
    set_s32(registers, 2, 30127, 1800)   # PV total power
    set_f32(registers, 2, 30129, 1234.5)

    set_u16(registers, 2, 30131, 2300)   # Grid R voltage 230.0 V
    set_u16(registers, 2, 30133, 2300)
    set_u16(registers, 2, 30135, 2300)
    set_u16(registers, 2, 30140, 5000)   # 50.00 Hz
    set_s16(registers, 2, 30146, 245)    # Inverter temp 24.5 C
    set_s16(registers, 2, 30147, 260)

    set_f32(registers, 2, 30156, 1200.0)
    set_f32(registers, 2, 30158, 150.0)
    set_u16(registers, 2, 30161, 3)      # Battery idle
    set_s32(registers, 2, 30162, 0)
    set_u16(registers, 2, 30164, 5120)   # Battery voltage 512.0 V
    set_s16(registers, 2, 30165, 0)
    set_s32(registers, 2, 30172, 1200)
    set_f32(registers, 2, 30174, 500.0)
    set_f32(registers, 2, 30176, 450.0)
    set_u16(registers, 2, 30182, 55)

    set_u16(registers, 2, 40400, 0)
    set_s32(registers, 2, 40401, -10000)
    set_u16(registers, 2, 40848, 0)
    set_s16(registers, 2, 40901, 0)
    set_u16(registers, 2, 40902, 95)
    set_u16(registers, 2, 40903, 15)
    set_u16(registers, 2, 40907, 0)
    set_u16(registers, 2, 40908, 0)
    set_u16(registers, 2, 40914, 0)
    set_u16(registers, 2, 40920, 0)
    set_u16(registers, 2, 40926, 0)
    now = datetime.now().astimezone()
    for address, value in zip(range(40932, 40936), (
        now.year, (now.month << 8) | now.day,
        (now.hour << 8) | now.minute, (now.second << 8) | ((now.weekday() + 1) % 7),
    )):
        set_u16(registers, 2, address, value)

    # Slave 1: BMS.
    set_u16(registers, 1, 5123, 5120)
    set_s16(registers, 1, 5126, 250)
    set_u16(registers, 1, 5127, 55)
    set_u16(registers, 1, 5128, 27)
    set_u16(registers, 1, 5136, 3350)
    set_u16(registers, 1, 5137, 3335)
    set_u16(registers, 1, 5152, 100)

    return registers


def exception_response(function_code: int, code: int) -> bytes:
    return bytes([function_code | 0x80, code])


def simulate_battery(registers: RegisterMap) -> None:
    """Model documented command direction; this does not model real firmware."""
    mode = registers[(2, 40907)]
    raw = struct.unpack(">h", struct.pack(">H", registers[(2, 40901)]))[0] if mode == 4 else 0
    # Slot matching follows the device RTC, not the test host's timezone.
    hhmm = registers[(2, 40934)]
    if mode in (4, 5):
        for base in (40908, 40914, 40920, 40926):
            if registers[(2, base)] and registers[(2, base + 1)] <= hhmm < registers[(2, base + 2)]:
                raw = registers[(2, base + 4)] * (-1 if registers[(2, base + 3)] == 0 else 1)
    if raw < 0 and registers[(2, 30182)] >= registers[(2, 40902)]:
        raw = 0
    power = round(raw * 13800 / 1000)
    set_u16(registers, 2, 30161, 1 if power < 0 else 2 if power > 0 else 3)
    set_s32(registers, 2, 30162, power)
    set_s32(registers, 2, 30100, power)
    set_s32(registers, 2, 30108, 1200 - power)


def handle_pdu(registers: RegisterMap, unit_id: int, pdu: bytes) -> bytes:
    if not pdu:
        return exception_response(0, 3)

    function_code = pdu[0]

    if function_code == 3:
        simulate_battery(registers)
        if len(pdu) != 5:
            return exception_response(function_code, 3)
        address, count = struct.unpack(">HH", pdu[1:5])
        if count < 1 or count > 125:
            return exception_response(function_code, 3)
        values = [registers[(unit_id, address + offset)] for offset in range(count)]
        return struct.pack(">BB", function_code, count * 2) + struct.pack(
            f">{count}H", *values
        )

    if function_code == 6:
        if len(pdu) != 5:
            return exception_response(function_code, 3)
        address, value = struct.unpack(">HH", pdu[1:5])
        registers[(unit_id, address)] = value
        return pdu

    if function_code == 16:
        if len(pdu) < 6:
            return exception_response(function_code, 3)
        address, count, byte_count = struct.unpack(">HHB", pdu[1:6])
        expected_length = 6 + byte_count
        if count < 1 or byte_count != count * 2 or len(pdu) != expected_length:
            return exception_response(function_code, 3)
        values = struct.unpack(f">{count}H", pdu[6:expected_length])
        for offset, value in enumerate(values):
            registers[(unit_id, address + offset)] = value
        return struct.pack(">BHH", function_code, address, count)

    return exception_response(function_code, 1)


def build_frame(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
    header = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, unit_id)
    return header + pdu


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    registers: RegisterMap,
    duplicate_write_response: bool,
    duplicate_delay: float,
) -> None:
    peer = writer.get_extra_info("peername")
    LOGGER.info("Client connected: %s", peer)
    try:
        while not reader.at_eof():
            header = await reader.readexactly(7)
            transaction_id, protocol_id, length, unit_id = struct.unpack(
                ">HHHB", header
            )
            if protocol_id != 0 or length < 2:
                LOGGER.warning("Invalid MBAP header from %s", peer)
                return

            pdu = await reader.readexactly(length - 1)
            response_pdu = handle_pdu(registers, unit_id, pdu)
            frame = build_frame(transaction_id, unit_id, response_pdu)

            writer.write(frame)
            await writer.drain()

            if duplicate_write_response and pdu and pdu[0] in (6, 16):
                await asyncio.sleep(duplicate_delay)
                writer.write(frame)
                await writer.drain()

    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()
        LOGGER.info("Client disconnected: %s", peer)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1502)
    parser.add_argument("--duplicate-write-response", action="store_true")
    parser.add_argument("--duplicate-delay", type=float, default=0.05)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(message)s")
    registers = build_registers()

    server = await asyncio.start_server(
        lambda reader, writer: handle_client(
            reader,
            writer,
            registers,
            args.duplicate_write_response,
            args.duplicate_delay,
        ),
        args.host,
        args.port,
    )

    sockets = ", ".join(str(socket.getsockname()) for socket in server.sockets or [])
    LOGGER.info("H3X Modbus emulator listening on %s", sockets)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
