"""Minimal Modbus TCP transport for Force H3X.

The H3X has been observed to send late or duplicate write responses. PyModbus
logs those as transaction-id errors from its runner. This transport keeps the
Modbus TCP handling deliberately small: one serialized socket, monotonic
transaction ids, and stale response frames are discarded while waiting for the
matching response.
"""
from __future__ import annotations

import asyncio
import logging
import struct

_LOGGER = logging.getLogger(__name__)

MODBUS_PROTOCOL_ID = 0
FUNCTION_READ_HOLDING_REGISTERS = 3
FUNCTION_WRITE_REGISTER = 6
FUNCTION_WRITE_REGISTERS = 16


class ModbusTransportError(Exception):
    """Base error for Force H3X raw Modbus transport failures."""


class ModbusTimeoutError(ModbusTransportError):
    """Raised when the device does not return a matching response in time."""


class ModbusProtocolError(ModbusTransportError):
    """Raised when the device returns an invalid Modbus TCP response."""


class ModbusDeviceException(ModbusTransportError):
    """Raised when the device returns a Modbus exception response."""

    def __init__(self, function_code: int, exception_code: int) -> None:
        self.function_code = function_code
        self.exception_code = exception_code
        super().__init__(
            f"Modbus exception function={function_code} code={exception_code}"
        )


class H3XModbusTcpClient:
    """Small async Modbus TCP client tailored to one Force H3X connection."""

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._transaction_id = 0

    @property
    def connected(self) -> bool:
        """Return whether the TCP writer appears usable."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> bool:
        """Connect to the Modbus TCP endpoint."""
        if self.connected:
            return True

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host, self.port, happy_eyeballs_delay=0.25, interleave=1
                ),
                timeout=self.timeout,
            )
        except OSError as err:
            self.close()
            _LOGGER.debug("Failed to connect to %s:%s: %s", self.host, self.port, err)
            return False
        except asyncio.TimeoutError:
            self.close()
            _LOGGER.debug("Timed out connecting to %s:%s", self.host, self.port)
            return False

        return True

    def close(self) -> None:
        """Close the TCP connection."""
        if self._writer is not None:
            self._writer.close()
        self._reader = None
        self._writer = None

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        if self._transaction_id == 0:
            self._transaction_id = 1
        return self._transaction_id

    async def _ensure_connected(self) -> None:
        if not await self.connect():
            raise ModbusTransportError(
                f"Failed to connect to {self.host}:{self.port}"
            )

    async def _read_exactly(self, count: int, timeout: float) -> bytes:
        if self._reader is None:
            raise ModbusTransportError("Modbus TCP reader is not connected")
        try:
            return await asyncio.wait_for(self._reader.readexactly(count), timeout)
        except asyncio.IncompleteReadError as err:
            self.close()
            raise ModbusTransportError("Modbus TCP connection closed") from err
        except asyncio.TimeoutError as err:
            self.close()
            raise ModbusTimeoutError("Timed out waiting for Modbus response") from err

    async def _read_matching_pdu(
        self,
        transaction_id: int,
        slave: int,
        function_code: int,
    ) -> bytes:
        deadline = asyncio.get_running_loop().time() + self.timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.close()
                raise ModbusTimeoutError(
                    f"No matching response for transaction_id={transaction_id}"
                )

            header = await self._read_exactly(7, remaining)
            recv_transaction_id, protocol_id, length, recv_slave = struct.unpack(
                ">HHHB", header
            )
            if protocol_id != MODBUS_PROTOCOL_ID or length < 2:
                self.close()
                raise ModbusProtocolError(
                    f"Invalid MBAP header tx={recv_transaction_id} protocol={protocol_id} length={length}"
                )

            pdu = await self._read_exactly(length - 1, remaining)

            if recv_transaction_id != transaction_id or recv_slave != slave:
                _LOGGER.debug(
                    "Discarding stale Modbus frame tx=%s expected=%s slave=%s expected=%s pdu=%s",
                    recv_transaction_id,
                    transaction_id,
                    recv_slave,
                    slave,
                    pdu.hex(" "),
                )
                continue

            if not pdu:
                raise ModbusProtocolError(
                    f"Empty Modbus PDU for transaction_id={transaction_id}"
                )

            response_function = pdu[0]
            if response_function == function_code | 0x80:
                if len(pdu) < 2:
                    raise ModbusProtocolError(
                        f"Malformed exception response for transaction_id={transaction_id}"
                    )
                raise ModbusDeviceException(function_code, pdu[1])

            if response_function != function_code:
                raise ModbusProtocolError(
                    f"Unexpected function {response_function}; expected {function_code}"
                )

            return pdu

    async def _request(
        self,
        slave: int,
        function_code: int,
        payload: bytes,
    ) -> bytes:
        await self._ensure_connected()
        if self._writer is None:
            raise ModbusTransportError("Modbus TCP writer is not connected")

        transaction_id = self._next_transaction_id()
        pdu = bytes([function_code]) + payload
        header = struct.pack(
            ">HHHB",
            transaction_id,
            MODBUS_PROTOCOL_ID,
            len(pdu) + 1,
            slave,
        )
        self._writer.write(header + pdu)
        await self._writer.drain()
        return await self._read_matching_pdu(transaction_id, slave, function_code)

    async def read_holding_registers(
        self,
        address: int,
        count: int,
        slave: int,
    ) -> list[int]:
        """Read holding registers."""
        if count < 1 or count > 125:
            raise ValueError(f"count must be 1..125, got {count}")

        pdu = await self._request(
            slave,
            FUNCTION_READ_HOLDING_REGISTERS,
            struct.pack(">HH", address, count),
        )
        if len(pdu) < 2:
            raise ModbusProtocolError("Read response is too short")
        byte_count = pdu[1]
        expected_byte_count = count * 2
        if byte_count != expected_byte_count or len(pdu) != byte_count + 2:
            raise ModbusProtocolError(
                f"Read byte count mismatch got={byte_count} expected={expected_byte_count}"
            )
        return list(struct.unpack(f">{count}H", pdu[2:]))

    async def write_register(self, address: int, value: int, slave: int) -> None:
        """Write one holding register."""
        pdu = await self._request(
            slave,
            FUNCTION_WRITE_REGISTER,
            struct.pack(">HH", address, value & 0xFFFF),
        )
        if len(pdu) != 5:
            raise ModbusProtocolError("Write-register response has invalid length")
        response_address, response_value = struct.unpack(">HH", pdu[1:])
        if response_address != address or response_value != value & 0xFFFF:
            raise ModbusProtocolError(
                f"Write-register echo mismatch address={response_address} value={response_value}"
            )

    async def write_registers(
        self,
        address: int,
        values: list[int],
        slave: int,
    ) -> None:
        """Write multiple holding registers."""
        if not values or len(values) > 123:
            raise ValueError(f"values length must be 1..123, got {len(values)}")
        encoded_values = [value & 0xFFFF for value in values]
        pdu = await self._request(
            slave,
            FUNCTION_WRITE_REGISTERS,
            struct.pack(">HHB", address, len(encoded_values), len(encoded_values) * 2)
            + struct.pack(f">{len(encoded_values)}H", *encoded_values),
        )
        if len(pdu) != 5:
            raise ModbusProtocolError("Write-registers response has invalid length")
        response_address, response_count = struct.unpack(">HH", pdu[1:])
        if response_address != address or response_count != len(encoded_values):
            raise ModbusProtocolError(
                f"Write-registers echo mismatch address={response_address} count={response_count}"
            )
