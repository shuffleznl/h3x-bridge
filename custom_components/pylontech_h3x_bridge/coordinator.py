"""DataUpdateCoordinator for Pylontech H3X Bridge."""
import asyncio
import logging
import struct
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL
from .protocol import (
    EMS_MODE_USER,
    REGISTER_CHARGE_DISCHARGE_POWER,
    REGISTER_EMS_MODE,
    REGISTER_REALTIME_YEAR,
    SLOT_MODE_CHARGE,
    WEEKDAY_ALL,
    encode_16bit_int,
    encode_16bit_uint,
    encode_percent_tenths,
    encode_realtime_registers,
    encode_time_slot_values,
    time_slot_registers,
)
from .transport import H3XModbusTcpClient, ModbusTransportError, ModbusDeviceException
from .override import OVERRIDE_OWNER
from .endpoint import normalize_host

_LOGGER = logging.getLogger(__name__)

MODBUS_RECONNECT_SETTLE_SECONDS = 0.75
MODBUS_WRITE_ATTEMPTS = 3
BMS_ESS_BASE_ADDRESS = 0x1400
BMS_MODULE_NUMBER_OFFSET = 0x0036
REGISTER_BMS_MODULE_NUMBER = BMS_ESS_BASE_ADDRESS + BMS_MODULE_NUMBER_OFFSET
FORCE_H3_USABLE_DOD = 0.95
FORCE_H3_SYSTEM_CAPACITY_KWH = {
    2: 10.24,
    3: 15.36,
    4: 20.48,
    5: 25.60,
    6: 30.72,
    7: 35.84,
}
FORCE_H3_USABLE_CAPACITY_KWH = {
    2: 9.69,
    3: 14.73,
    4: 19.48,
    5: 24.32,
    6: 29.17,
    7: 34.01,
}

# =========================================================
# Helper functions for Modbus decoding
# =========================================================
def get_16bit_uint(regs, idx):
    return regs[idx]

def get_16bit_int(regs, idx):
    return struct.unpack('>h', struct.pack('>H', regs[idx]))[0]

def get_32bit_int(regs, idx):
    return struct.unpack('>i', struct.pack('>HH', regs[idx], regs[idx+1]))[0]

def get_32bit_float(regs, idx):
    return struct.unpack('>f', struct.pack('>HH', regs[idx], regs[idx+1]))[0]


def force_h3_capacity_for_modules(module_count: int) -> dict[str, float] | None:
    """Return datasheet capacity values for a valid Force H3 module count."""
    system_capacity = FORCE_H3_SYSTEM_CAPACITY_KWH.get(module_count)
    usable_capacity = FORCE_H3_USABLE_CAPACITY_KWH.get(module_count)
    if system_capacity is None or usable_capacity is None:
        return None
    theoretical_usable = round(system_capacity * FORCE_H3_USABLE_DOD, 2)
    usable_deviation_pct = (
        abs(usable_capacity - theoretical_usable) / theoretical_usable * 100
    )
    return {
        "battery_system_capacity": system_capacity,
        "battery_usable_capacity": usable_capacity,
        "battery_usable_capacity_theoretical": theoretical_usable,
        "battery_usable_capacity_deviation_pct": round(usable_deviation_pct, 2),
    }


class PylontechCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Modbus data from the inverter."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, entry=None) -> None:
        self.host = normalize_host(host)
        self.port = port
        self.client = self._new_client()
        self._modbus_lock = asyncio.Lock()
        self._last_client_reset: float | None = None
        self.override = None
        self.last_power_command = {}
        self._power_command_sequence = 0
        self._verified_power_command = None
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    def _new_client(self) -> H3XModbusTcpClient:
        """Create a fresh Modbus TCP client."""
        return H3XModbusTcpClient(host=self.host, port=self.port, timeout=5)

    async def _ensure_connected(self) -> None:
        """Ensure the Modbus TCP client is connected."""
        if self.client.connected:
            return

        if self._last_client_reset is not None:
            elapsed = asyncio.get_running_loop().time() - self._last_client_reset
            if elapsed < MODBUS_RECONNECT_SETTLE_SECONDS:
                await asyncio.sleep(MODBUS_RECONNECT_SETTLE_SECONDS - elapsed)
            self._last_client_reset = None

        if not await self.client.connect():
            raise ModbusTransportError(f"Failed to connect to {self.host}:{self.port}")
        if not self.client.connected:
            raise ModbusTransportError(
                f"Connected client is not ready for {self.host}:{self.port}"
            )

    def _reset_client(self) -> None:
        """Close and recreate the Modbus client after protocol desync/errors."""
        try:
            self.client.close()
        finally:
            self.client = self._new_client()
            self._last_client_reset = asyncio.get_running_loop().time()

    async def async_close(self) -> None:
        """Close the Modbus client safely."""
        async with self._modbus_lock:
            self.client.close()

    async def safe_read(self, address, count, slave, optional: bool = False):
        
        # 100ms pause
        await asyncio.sleep(0.1) 
        try:
            return await self.client.read_holding_registers(address, count, slave)
        except ModbusDeviceException as err:
            if not optional:
                raise
            _LOGGER.debug(
                "error while reading address %s (Slave %s): %s",
                address,
                slave,
                err,
            )
            return None
        except ModbusTransportError:
            # Abort this poll: reconnecting per register can occupy minutes.
            raise

    async def _async_update_data(self):
        """Fetch data from the inverter via Modbus with serialized access."""
        async with self._modbus_lock:
            data = await self._async_update_data_locked()
        if self.override:
            command_sequence = self._power_command_sequence
            try:
                await self.override.async_tick(data)
            except Exception as err:
                raise UpdateFailed(f"Charge override failed: {err}") from err
            data.update(self.override.diagnostics())
            if self._power_command_sequence != command_sequence:
                reference, mode = self._verified_power_command
                data["charge_discharge_power"] = reference
                if reference:
                    data["ems_mode"] = str(mode)
        return data

    async def _async_update_data_locked(self):
        """Fetch data from the inverter via Modbus."""
        try:
            await self._ensure_connected()

            data = {}

            
            # AC & Grid Power (30100 - 30101 en 30108 - 30109)
            r_ac = await self.safe_read(30100, 2, 2)
            if r_ac: data["ac_total_power"] = get_32bit_int(r_ac, 0)
            
            r_grid = await self.safe_read(30108, 2, 2)
            if r_grid: data["grid_total_power"] = get_32bit_int(r_grid, 0)

            #virtual sensor that combines ac power out of battery with grid power and thus get the load power.
            if "ac_total_power" in data and "grid_total_power" in data:
                data["load_power"] = data["ac_total_power"] + data["grid_total_power"]


            # Inverter Status (30115)
            r_status = await self.safe_read(30115, 1, 2)
            if r_status: data["inverter_status"] = get_16bit_uint(r_status, 0)

            # PV voltage & current (30119 t/m 30124)
            r_pv = await self.safe_read(30119, 6, 2)
            if r_pv:
                data["pv1_voltage"] = get_16bit_uint(r_pv, 0) * 0.1
                data["pv1_current"] = get_16bit_uint(r_pv, 1) * 0.1
                data["pv2_voltage"] = get_16bit_uint(r_pv, 2) * 0.1
                data["pv2_current"] = get_16bit_uint(r_pv, 3) * 0.1
                data["pv3_voltage"] = get_16bit_uint(r_pv, 4) * 0.1
                data["pv3_current"] = get_16bit_uint(r_pv, 5) * 0.1
            
            #virtual sensor to calculate pv1,2,3 power
            if r_pv:
                data["pv1_power"] = data["pv1_voltage"] * data["pv1_current"]
                data["pv2_power"] = data["pv2_voltage"] * data["pv2_current"]
                data["pv3_power"] = data["pv3_voltage"] * data["pv3_current"]


            # PV Power & Energy (30127 t/m 30130)
            r_pv_tot = await self.safe_read(30127, 4, 2)
            if r_pv_tot:
                data["pv_total_power"] = get_32bit_int(r_pv_tot, 0)
                data["pv_total_energy"] = get_32bit_float(r_pv_tot, 2)

            # Grid Voltages & Freq (30131 t/m 30140)
            r_grid_v = await self.safe_read(30131, 10, 2)
            if r_grid_v:
                data["grid_voltage_r"] = get_16bit_uint(r_grid_v, 0) * 0.1
                data["grid_voltage_s"] = get_16bit_uint(r_grid_v, 2) * 0.1
                data["grid_voltage_t"] = get_16bit_uint(r_grid_v, 4) * 0.1
                data["ac_frequency"] = get_16bit_uint(r_grid_v, 9) * 0.01

            # Temperature (30146)
            r_temp = await self.safe_read(30146, 2, 2)
            if r_temp: 
                data["inverter_temperature"] = get_16bit_int(r_temp, 0) * 0.1
                data["heatsink_temperature"] = get_16bit_int(r_temp, 1) * 0.1


            # Grid Energy In/Out (30156 t/m 30159)
            r_grid_e = await self.safe_read(30156, 4, 2)
            if r_grid_e:
                data["total_grid_import"] = get_32bit_float(r_grid_e, 0)
                data["total_grid_export"] = get_32bit_float(r_grid_e, 2)


            r_active = await self.safe_read(40400, 3, 2)
            if r_active:
                # 40400: Active power control mode (U16)
                data["active_power_control_mode"] = get_16bit_uint(r_active, 0)
                # 40401: Meter limit power (S32, 2 registers)
                data["meter_export_power_max"] = get_32bit_int(r_active, 1)


            BATTERY_STATUS_MAP = {
                            0: "Sleep",
                            1: "Charging",
                            2: "Discharging",
                            3: "Idle",
                            4: "Standby",
                            5: "Run",
                            6: "Fault",
                            7: "Offline",
                        }
            
            # Battery Status, Power, Voltage, Current (30161 t/m 30165)
            r_batt = await self.safe_read(30161, 5, 2)
            if r_batt:

                raw_status = get_16bit_uint(r_batt, 0)
                data["battery_status"] = BATTERY_STATUS_MAP.get(raw_status, f"Unknown ({raw_status})")

                data["battery_power"] = get_32bit_int(r_batt, 1)
                data["battery_voltage"] = get_16bit_uint(r_batt, 3) * 0.1
                data["battery_current"] = get_16bit_int(r_batt, 4) * 0.1

            # Load Power (30172)
            r_load = await self.safe_read(30172, 2, 2)
            if r_load: data["eps_power"] = get_32bit_int(r_load, 0)



            # Battery Energy (30174 t/m 30177)
            r_batt_e = await self.safe_read(30174, 4, 2)
            if r_batt_e:
                data["total_battery_charge"] = get_32bit_float(r_batt_e, 0)
                data["total_battery_discharge"] = get_32bit_float(r_batt_e, 2)

            # SOC & CT Currents (30182 t/m 30185)
            r_soc = await self.safe_read(30182, 4, 2)
            if r_soc:
                data["battery_soc"] = get_16bit_uint(r_soc, 0)


            # =========================================================
            # SLAVE 2 (inverter) - EMS Settings 
            # =========================================================
            r_ems = await self.safe_read(40901, 7, 2)
            if r_ems:
                # 40901 is S16 (Signed)
                data["charge_discharge_power"] = get_16bit_int(r_ems, 0)
                
                # De rest is U16 (Unsigned)
                data["charge_limit_soc"] = get_16bit_uint(r_ems, 1) #40902
                data["discharge_limit_soc"] = get_16bit_uint(r_ems, 2) #40903
                data["ems_mode"] = str(get_16bit_uint(r_ems, 6)) #40907


            #heatpump 
            r_hp = await self.safe_read(40848, 1, 2)
            if r_hp:
                data["heat_pump"] = get_16bit_uint(r_hp, 0)

            # charge / discharge periods
            r_p1 = await self.safe_read(40908, 1, 2)
            if r_p1:
                data["period_1"] = get_16bit_uint(r_p1, 0)

            r_p2 = await self.safe_read(40914, 1, 2)
            if r_p2:
                data["period_2"] = get_16bit_uint(r_p2, 0)

            r_p3 = await self.safe_read(40920, 1, 2)
            if r_p3:
                data["period_3"] = get_16bit_uint(r_p3, 0)

            r_p4 = await self.safe_read(40926, 1, 2)
            if r_p4:
                data["period_4"] = get_16bit_uint(r_p4, 0)


            # =========================================================
            # SLAVE 1 (BMS) 
            # =========================================================
            
            # BMS Voltage (5123 / 0x1403)
            r_bms_v = await self.safe_read(5123, 1, 1)
            if r_bms_v: data["bms_voltage"] = get_16bit_uint(r_bms_v, 0) * 0.1

            # BMS Temp, SOC, Cycles (5126 / 0x1406 t/m 0x1408)
            r_bms_t = await self.safe_read(5126, 3, 1)
            if r_bms_t:
                data["bms_temperature"] = get_16bit_int(r_bms_t, 0) * 0.1
                data["bms_soc"] = get_16bit_uint(r_bms_t, 1)
                data["bms_cycles"] = get_16bit_uint(r_bms_t, 2)

            # BMS Cell Volts (5136 / 0x1410 t/m 0x1411)
            r_bms_cv = await self.safe_read(5136, 2, 1, optional=True)
            if r_bms_cv:
                data["bms_cell_voltage_max"] = get_16bit_uint(r_bms_cv, 0) * 0.001
                data["bms_cell_voltage_min"] = get_16bit_uint(r_bms_cv, 1) * 0.001

            # BMS SOH (5152 / 0x1420)
            r_bms_soh = await self.safe_read(5152, 1, 1)
            if r_bms_soh: data["bms_soh"] = get_16bit_uint(r_bms_soh, 0)

            # BMS module count (5174 / 0x1436)
            r_bms_modules = await self.safe_read(
                REGISTER_BMS_MODULE_NUMBER, 1, 1, optional=True
            )
            if r_bms_modules:
                module_count = get_16bit_uint(r_bms_modules, 0)
                data["battery_module_count"] = module_count
                capacity = force_h3_capacity_for_modules(module_count)
                if capacity:
                    data.update(capacity)

            
            if not data:
                raise UpdateFailed("No data received out of inverter.")

            return data

        except ModbusTransportError as err:
            self._reset_client()
            raise UpdateFailed(f"error with modbus communication: {err}")
        except Exception as err:
            self._reset_client()
            raise UpdateFailed(f"unexpected error: {err}")

    async def _write_register_locked(
        self,
        address: int,
        register_value: int,
        slave: int,
        *,
        raw_value: int,
        signed: bool,
        settle_after: bool = True,
    ) -> bool:
        """Write one already-encoded register while the Modbus lock is held.

        The raw transport discards stale transaction ids, so the socket can stay
        open while the next command consumes and ignores late duplicate ACKs.
        """
        if 40901 <= address <= 40935 and self.override and self.override.active and not OVERRIDE_OWNER.get():
            return False
        for attempt in range(1, MODBUS_WRITE_ATTEMPTS + 1):
            try:
                await self._ensure_connected()
                await self.client.write_register(address, register_value, slave)
                if settle_after:
                    await asyncio.sleep(0.05)

                return True

            except ModbusTransportError as err:
                self._reset_client()
                if attempt == MODBUS_WRITE_ATTEMPTS:
                    _LOGGER.error(
                        "Modbus error while writing register %s after %s attempts: %s",
                        address,
                        attempt,
                        err,
                    )
                    return False
                _LOGGER.debug(
                    "Modbus write register %s failed on attempt %s/%s; retrying: %s",
                    address,
                    attempt,
                    MODBUS_WRITE_ATTEMPTS,
                    err,
                )
            except Exception as err:
                self._reset_client()
                if attempt == MODBUS_WRITE_ATTEMPTS:
                    _LOGGER.error(
                        "Unexpected error while writing register %s after %s attempts: %s",
                        address,
                        attempt,
                        err,
                    )
                    return False
                _LOGGER.warning(
                    "Unexpected write failure for register %s on attempt %s/%s; retrying: %s",
                    address,
                    attempt,
                    MODBUS_WRITE_ATTEMPTS,
                    err,
                )

        return False

    async def _write_registers_locked(
        self,
        address: int,
        values: list[int],
        slave: int,
        *,
        settle_after: bool = True,
    ) -> bool:
        """Write multiple already-encoded registers while the Modbus lock is held."""
        if 40901 <= address <= 40935 and self.override and self.override.active and not OVERRIDE_OWNER.get():
            return False
        for attempt in range(1, MODBUS_WRITE_ATTEMPTS + 1):
            try:
                await self._ensure_connected()
                await self.client.write_registers(address, values, slave)
                if settle_after:
                    await asyncio.sleep(0.05)

                return True

            except ModbusTransportError as err:
                self._reset_client()
                if attempt == MODBUS_WRITE_ATTEMPTS:
                    _LOGGER.error(
                        "Modbus error while writing registers %s after %s attempts: %s",
                        address,
                        attempt,
                        err,
                    )
                    return False
                _LOGGER.debug(
                    "Modbus write registers %s failed on attempt %s/%s; retrying: %s",
                    address,
                    attempt,
                    MODBUS_WRITE_ATTEMPTS,
                    err,
                )
            except Exception as err:
                self._reset_client()
                if attempt == MODBUS_WRITE_ATTEMPTS:
                    _LOGGER.error(
                        "Unexpected error writing registers %s after %s attempts: %s",
                        address,
                        attempt,
                        err,
                    )
                    return False
                _LOGGER.warning(
                    "Unexpected write failure for registers %s on attempt %s/%s; retrying: %s",
                    address,
                    attempt,
                    MODBUS_WRITE_ATTEMPTS,
                    err,
                )

        return False

    async def async_write_register(
        self,
        address: int,
        value: int,
        slave: int = 2,
        signed: bool = False,
        refresh: bool = False,
    ) -> bool:
        """Write one 16-bit register."""
        if 40901 <= address <= 40935 and self.override and self.override.active and not OVERRIDE_OWNER.get():
            return False
        try:
            register_value = (
                encode_16bit_int(value) if signed else encode_16bit_uint(value)
            )
        except ValueError as err:
            _LOGGER.error("Invalid value for register %s: %s", address, err)
            return False

        async with self._modbus_lock:
            success = await self._write_register_locked(
                address,
                register_value,
                slave,
                raw_value=value,
                signed=signed,
            )

        if success and refresh:
            await self.async_request_refresh()
        return success

    async def async_set_charge_discharge_power(
        self,
        value: int,
        slave: int = 2,
    ) -> bool:
        """Force charge/discharge power reference.

        Pylontech documents register 40901 as S16 in 0.1Pn% units:
        positive values discharge and negative values charge. Any nonzero
        forced reference must be sent while EMS mode is User mode.
        """
        if self.override and self.override.active and not OVERRIDE_OWNER.get():
            return False
        if not -1000 <= value <= 1000:
            return False
        if value < 0 and self.override and OVERRIDE_OWNER.get() and self.override.settings["charge_method"] != "user_reference":
            return await self.override.async_slot_charge(abs(value) / 10)
        if value >= 0 and self.override and self.override._slot_active:
            result = await self.async_clear_time_slot(slot=4)
            if not result["success"]:
                return False
            self.override._slot_active = False
            self.override._slot_until = None
        try:
            register_value = encode_16bit_int(value)
        except ValueError as err:
            _LOGGER.error(
                "Invalid charge/discharge power reference %s: %s",
                value,
                err,
            )
            return False

        async with self._modbus_lock:
            self.last_power_command = {
                "requested_reference_percent": value / 10,
                "requested_reference_raw": value,
                "requested_register_word_hex": f"0x{register_value:04X}",
                "write_acknowledged": False, "readback_matches": False,
            }
            if value != 0:
                mode_success = await self._write_register_locked(
                    REGISTER_EMS_MODE,
                    encode_16bit_uint(EMS_MODE_USER),
                    slave,
                    raw_value=EMS_MODE_USER,
                    signed=False,
                    settle_after=False,
                )
                if not mode_success:
                    return False
                if self.data is not None:
                    self.data["ems_mode"] = str(EMS_MODE_USER)

                await asyncio.sleep(0.2)

            success = await self._write_register_locked(
                REGISTER_CHARGE_DISCHARGE_POWER,
                register_value,
                slave,
                raw_value=value,
                signed=True,
            )
            if success:
                self.last_power_command["write_acknowledged"] = True
                try:
                    registers = await self.client.read_holding_registers(40901, 7, slave)
                    observed = get_16bit_int(registers, 0)
                    mode = registers[6]
                    matches = observed == value and (not value or mode == EMS_MODE_USER)
                    self.last_power_command.update(
                        readback_raw=observed, readback_mode=mode, readback_matches=matches
                    )
                    success = matches
                    if not matches:
                        _LOGGER.error("Power command read-back mismatch: requested %s, got %s in mode %s", value, observed, mode)
                except ModbusTransportError as err:
                    self.last_power_command["readback_error"] = str(err)
                    self._reset_client()
                    success = False

            if success:
                self._verified_power_command = (observed, mode)
                self._power_command_sequence += 1
                if self.data is not None:
                    self.data["charge_discharge_power"] = value
        return success

    async def async_program_charge_slot(
        self,
        *,
        slot: int,
        power_percent: int,
        start: datetime,
        end: datetime,
        ems_mode: int,
        sync_clock: bool = True,
        clock_time: datetime | None = None,
        weekday_mask: int = WEEKDAY_ALL,
        slave: int = 2,
    ) -> dict:
        """Program a time-slot based force-charge window."""
        if self.override and self.override.active and not OVERRIDE_OWNER.get():
            return {"success": False, "error": "Force charge/weather reserve owns control"}
        try:
            registers = time_slot_registers(slot)
            slot_values = encode_time_slot_values(
                start,
                end,
                mode=SLOT_MODE_CHARGE,
                power_percent=power_percent,
                weekday_mask=weekday_mask,
            )
            power_raw = encode_percent_tenths(power_percent)
            ems_mode_word = encode_16bit_uint(ems_mode)
            realtime_values = encode_realtime_registers(
                clock_time or datetime.now().astimezone()
            )
        except ValueError as err:
            return {"success": False, "error": str(err)}

        async with self._modbus_lock:
            if sync_clock:
                if not await self._write_registers_locked(
                    REGISTER_REALTIME_YEAR,
                    realtime_values,
                    slave,
                    settle_after=False,
                ):
                    return {"success": False, "error": "failed to sync inverter clock"}

            if not await self._write_register_locked(
                registers.enable,
                0,
                slave,
                raw_value=0,
                signed=False,
                settle_after=False,
            ):
                return {"success": False, "error": f"failed to disable slot {slot}"}

            if not await self._write_registers_locked(
                registers.start_time,
                slot_values,
                slave,
                settle_after=False,
            ):
                return {"success": False, "error": f"failed to write slot {slot}"}

            if not await self._write_register_locked(
                REGISTER_EMS_MODE,
                ems_mode_word,
                slave,
                raw_value=ems_mode,
                signed=False,
                settle_after=False,
            ):
                return {"success": False, "error": f"failed to set EMS mode {ems_mode}"}

            await asyncio.sleep(0.2)

            if not await self._write_register_locked(
                registers.enable,
                1,
                slave,
                raw_value=1,
                signed=False,
            ):
                return {"success": False, "error": f"failed to enable slot {slot}"}

        if self.data is not None:
            self.data["ems_mode"] = str(ems_mode)
            self.data[f"period_{slot}"] = 1

        return {
            "success": True,
            "slot": slot,
            "ems_mode": ems_mode,
            "mode": "charge",
            "power_percent": power_percent,
            "power_raw": power_raw,
            "start_register": slot_values[0],
            "end_register": slot_values[1],
            "weekday_mask": weekday_mask,
            "sync_clock": sync_clock,
        }

    async def async_clear_time_slot(
        self,
        *,
        slot: int,
        slave: int = 2,
    ) -> dict:
        """Disable one time slot."""
        if self.override and self.override.active and not OVERRIDE_OWNER.get():
            return {"success": False, "error": "Force charge/weather reserve owns control"}
        try:
            registers = time_slot_registers(slot)
        except ValueError as err:
            return {"success": False, "error": str(err)}

        async with self._modbus_lock:
            success = await self._write_register_locked(
                registers.enable,
                0,
                slave,
                raw_value=0,
                signed=False,
            )

        if not success:
            return {"success": False, "error": f"failed to disable slot {slot}"}

        if self.data is not None:
            self.data[f"period_{slot}"] = 0

        return {"success": True, "slot": slot, "enabled": False}

    async def async_test_force_charge_modes(
        self,
        *,
        slot: int,
        power_percent: int,
        duration_minutes: int,
        settle_seconds: int,
        sync_clock: bool = True,
    ) -> dict:
        """Try PN-Customer and User EMS modes for a slot-based charge window."""
        from .protocol import EMS_MODE_PN_CUSTOMER

        results = []
        for ems_mode in (EMS_MODE_PN_CUSTOMER, EMS_MODE_USER):
            now = datetime.now().astimezone()
            result = await self.async_program_charge_slot(
                slot=slot,
                power_percent=power_percent,
                start=now - timedelta(minutes=1),
                end=now + timedelta(minutes=duration_minutes),
                ems_mode=ems_mode,
                sync_clock=sync_clock,
                clock_time=now,
            )

            if result["success"]:
                await asyncio.sleep(settle_seconds)
                await self.async_request_refresh()

            snapshot = {
                "battery_status": self.data.get("battery_status") if self.data else None,
                "battery_power": self.data.get("battery_power") if self.data else None,
                "battery_current": self.data.get("battery_current") if self.data else None,
                "battery_soc": self.data.get("battery_soc") if self.data else None,
                "charge_discharge_power": self.data.get("charge_discharge_power")
                if self.data
                else None,
                "ems_mode": self.data.get("ems_mode") if self.data else None,
                f"period_{slot}": self.data.get(f"period_{slot}") if self.data else None,
            }
            result["snapshot"] = snapshot
            results.append(result)

        return {
            "success": any(result["success"] for result in results),
            "slot": slot,
            "power_percent": power_percent,
            "duration_minutes": duration_minutes,
            "settle_seconds": settle_seconds,
            "results": results,
        }
        
    async def async_write_register_32bit(
        self,
        address: int,
        value: int,
        slave: int = 2,
        refresh: bool = False,
    ) -> bool:
        """Write a 32-bit signed value (S32) as two consecutive 16-bit registers."""
        # Pack S32 into two U16 registers (big-endian)
        packed = struct.pack('>i', value)
        high, low = struct.unpack('>HH', packed)

        success = False
        async with self._modbus_lock:
            for attempt in range(1, MODBUS_WRITE_ATTEMPTS + 1):
                try:
                    await self._ensure_connected()
                    await self.client.write_registers(address, [high, low], slave)

                    success = True
                    break

                except ModbusTransportError as err:
                    self._reset_client()
                    if attempt == MODBUS_WRITE_ATTEMPTS:
                        _LOGGER.error(
                            "Modbus error while writing 32-bit register %s after %s attempts: %s",
                            address,
                            attempt,
                            err,
                        )
                        return False
                    _LOGGER.debug(
                        "Modbus write 32-bit register %s failed on attempt %s/%s; retrying: %s",
                        address,
                        attempt,
                        MODBUS_WRITE_ATTEMPTS,
                        err,
                    )
                except Exception as err:
                    self._reset_client()
                    if attempt == MODBUS_WRITE_ATTEMPTS:
                        _LOGGER.error(
                            "Unexpected error writing 32-bit register %s after %s attempts: %s",
                            address,
                            attempt,
                            err,
                        )
                        return False
                    _LOGGER.warning(
                        "Unexpected write failure for 32-bit register %s on attempt %s/%s; retrying: %s",
                        address,
                        attempt,
                        MODBUS_WRITE_ATTEMPTS,
                        err,
                    )

        if success and refresh:
            await self.async_request_refresh()
        return success
