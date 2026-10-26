"""Home Assistant services for Pylontech H3X Bridge."""
from __future__ import annotations

from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import PylontechCoordinator
from .protocol import EMS_MODE_PN_CUSTOMER, EMS_MODE_USER, shift_elapsed

_LOGGER = logging.getLogger(__name__)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_DURATION_MINUTES = "duration_minutes"
ATTR_EMS_MODE = "ems_mode"
ATTR_POWER_PERCENT = "power_percent"
ATTR_SETTLE_SECONDS = "settle_seconds"
ATTR_SLOT = "slot"
ATTR_SYNC_CLOCK = "sync_clock"

SERVICE_CLEAR_TIME_SLOT = "clear_time_slot"
SERVICE_FORCE_CHARGE_NOW = "force_charge_now"
SERVICE_TEST_FORCE_CHARGE_MODES = "test_force_charge_modes"

EMS_MODE_NAMES = {
    "pn_customer": EMS_MODE_PN_CUSTOMER,
    "user": EMS_MODE_USER,
}

BASE_SLOT_SCHEMA = {
    vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(ATTR_SLOT, default=4): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
}

FORCE_CHARGE_SCHEMA = vol.Schema(
    {
        **BASE_SLOT_SCHEMA,
        vol.Optional(ATTR_POWER_PERCENT, default=10): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional(ATTR_DURATION_MINUTES, default=30): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=240)
        ),
        vol.Optional(ATTR_EMS_MODE, default="pn_customer"): vol.In(tuple(EMS_MODE_NAMES)),
        vol.Optional(ATTR_SYNC_CLOCK, default=False): cv.boolean,
    }
)

TEST_MODES_SCHEMA = vol.Schema(
    {
        **BASE_SLOT_SCHEMA,
        vol.Optional(ATTR_POWER_PERCENT, default=10): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional(ATTR_DURATION_MINUTES, default=10): vol.All(
            vol.Coerce(int), vol.Range(min=2, max=60)
        ),
        vol.Optional(ATTR_SETTLE_SECONDS, default=45): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=300)
        ),
        vol.Optional(ATTR_SYNC_CLOCK, default=False): cv.boolean,
    }
)

CLEAR_SLOT_SCHEMA = vol.Schema(BASE_SLOT_SCHEMA)


def _coordinator_from_call(hass: HomeAssistant, call: ServiceCall) -> PylontechCoordinator:
    coordinators = hass.data.get(DOMAIN, {})
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    coordinator = coordinators.get(entry_id)
    if coordinator is None:
        raise HomeAssistantError(
            f"Pylontech H3X Bridge config entry {entry_id!r} is not loaded"
        )
    return coordinator


async def async_setup_services(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register Pylontech H3X Bridge services."""

    async def force_charge_now(call: ServiceCall) -> ServiceResponse | None:
        coordinator = _coordinator_from_call(hass, call)
        now = dt_util.now()
        start = shift_elapsed(now, -timedelta(minutes=1))
        end = shift_elapsed(now, timedelta(minutes=call.data[ATTR_DURATION_MINUTES]))
        ems_mode = EMS_MODE_NAMES[call.data[ATTR_EMS_MODE]]

        result = await coordinator.async_program_charge_slot(
            slot=call.data[ATTR_SLOT],
            power_percent=call.data[ATTR_POWER_PERCENT],
            start=start,
            end=end,
            ems_mode=ems_mode,
            sync_clock=call.data[ATTR_SYNC_CLOCK],
            clock_time=now,
        )
        if not result["success"]:
            raise HomeAssistantError(result["error"])
        if call.return_response:
            return result
        return None

    async def clear_time_slot(call: ServiceCall) -> ServiceResponse | None:
        coordinator = _coordinator_from_call(hass, call)
        result = await coordinator.async_clear_time_slot(slot=call.data[ATTR_SLOT])
        if not result["success"]:
            raise HomeAssistantError(result["error"])
        if call.return_response:
            return result
        return None

    async def test_force_charge_modes(call: ServiceCall) -> ServiceResponse | None:
        coordinator = _coordinator_from_call(hass, call)
        result = await coordinator.async_test_force_charge_modes(
            slot=call.data[ATTR_SLOT],
            power_percent=call.data[ATTR_POWER_PERCENT],
            duration_minutes=call.data[ATTR_DURATION_MINUTES],
            settle_seconds=call.data[ATTR_SETTLE_SECONDS],
            sync_clock=call.data[ATTR_SYNC_CLOCK],
        )
        _LOGGER.info("Force charge mode test result: %s", result)
        if call.return_response:
            return result
        return None

    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_CHARGE_NOW,
        force_charge_now,
        schema=FORCE_CHARGE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_TIME_SLOT,
        clear_time_slot,
        schema=CLEAR_SLOT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TEST_FORCE_CHARGE_MODES,
        test_force_charge_modes,
        schema=TEST_MODES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    return True
