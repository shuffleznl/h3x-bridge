"""The Pylontech H3X Bridge integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, CONF_HOST, CONF_PORT
from .coordinator import PylontechCoordinator
from .services import async_setup_services
from .override import ChargeOverride
from .entity_ids import migrate_control_entity_ids

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SWITCH]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Pylontech H3X Bridge services."""
    hass.data.setdefault(DOMAIN, {})
    return await async_setup_services(hass, config)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pylontech H3X Bridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Get IP and port from setup data, with options overriding for later changes.
    host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
    port = entry.options.get(CONF_PORT, entry.data[CONF_PORT])

    # Initialize coordinator
    coordinator = PylontechCoordinator(hass, host, port, entry)
    coordinator.override = ChargeOverride(hass, coordinator, entry)
    await coordinator.override.async_load()

    
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_close()
        raise

    
    hass.data[DOMAIN][entry.entry_id] = coordinator
    migrate_control_entity_ids(hass, entry)

    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        
        coordinator = hass.data[DOMAIN].get(entry.entry_id)
        if coordinator:
            await coordinator.async_close()
            
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
