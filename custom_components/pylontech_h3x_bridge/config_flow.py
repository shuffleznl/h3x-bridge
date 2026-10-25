"""Config flow for Pylontech H3X Bridge."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_HOST,
    CONF_PORT,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
)
from .endpoint import normalize_host
from .transport import H3XModbusTcpClient, ModbusTransportError
from homeassistant.helpers import selector

_LOGGER = logging.getLogger(__name__)


def connection_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return a schema for Modbus TCP connection settings."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
            vol.Required(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(
                "grid_power_entity",
                default=defaults.get("grid_power_entity") or vol.UNDEFINED,
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                "weather_alert_entity",
                default=defaults.get("weather_alert_entity") or vol.UNDEFINED,
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                "alert_siren_entity",
                default=defaults.get("alert_siren_entity") or vol.UNDEFINED,
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="siren")),
            vol.Optional(
                "inverter_rated_power_w",
                default=defaults.get("inverter_rated_power_w", 13800),
            ): vol.All(vol.Coerce(int), vol.Range(min=1000, max=30000)),
            vol.Optional(
                "battery_power_limit_w",
                default=defaults.get("battery_power_limit_w", 11000),
            ): vol.All(vol.Coerce(int), vol.Range(min=100, max=13800)),
            vol.Optional(
                "grid_connection_limit_w",
                default=defaults.get("grid_connection_limit_w", 17500),
            ): vol.All(vol.Coerce(int), vol.Range(min=1000, max=100000)),
        }
    )


async def validate_input(
    hass: HomeAssistant, data: dict[str, Any], coordinator=None
) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the Modbus TCP host and port values provided by the user.
    """

    try:
        data[CONF_HOST] = normalize_host(data[CONF_HOST])
        client = H3XModbusTcpClient(data[CONF_HOST], data[CONF_PORT], timeout=5)
    except ValueError as err:
        raise CannotConnect from err
    try:
        if coordinator is None:
            await client.read_holding_registers(40907, 1, 2)
        else:
            # The same inverter may only accept one TCP session, even when
            # changing from its IP to an equivalent DNS name.
            async with coordinator._modbus_lock:
                coordinator.client.close()
                try:
                    await client.read_holding_registers(40907, 1, 2)
                finally:
                    client.close()
                    coordinator._reset_client()
    except (ModbusTransportError, OSError, TimeoutError) as err:
        raise CannotConnect from err
    finally:
        client.close()

    # Return info that you want to store in the config entry.
    return {"title": DEFAULT_NAME}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pylontech H3X Bridge."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=connection_schema(user_input), errors=errors
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle editable integration options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage Modbus TCP connection options."""
        errors: dict[str, str] = {}
        current = {
            **self._config_entry.data,
            **self._config_entry.options,
        }

        if user_input is not None:
            try:
                coordinator = self.hass.data.get(DOMAIN, {}).get(
                    self._config_entry.entry_id
                )
                await validate_input(self.hass, user_input, coordinator)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=connection_schema(user_input or current),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
