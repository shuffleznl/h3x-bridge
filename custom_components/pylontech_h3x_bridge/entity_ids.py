"""Stable IDs for dashboard controls introduced in bridge 0.4."""

import logging

from homeassistant.helpers import (
    entity_registry as er,
    device_registry as dr,
    area_registry as ar,
)
from homeassistant.util import slugify

from .const import DOMAIN, DEFAULT_NAME

LOGGER = logging.getLogger(__name__)
CONTROL_OBJECT_IDS = {
    "switch.force_charge": "pylontech_h3x_bridge_force_charge",
    "switch.weather_reserve": "pylontech_h3x_bridge_weather_reserve",
    "number.force_charge_rate": "pylontech_h3x_bridge_force_charge_rate",
    "number.weather_discharge_rate": "pylontech_h3x_bridge_weather_discharge_rate",
    "select.charge_method": "pylontech_h3x_bridge_charge_method",
    "select.weather_threshold": "pylontech_h3x_bridge_weather_alert_threshold",
    "sensor.control_owner": "pylontech_h3x_bridge_control_owner",
    "sensor.charge_control_status": "pylontech_h3x_bridge_charge_control_status",
}


def set_control_entity_id(entity, platform: str, key: str) -> None:
    """Make fresh control IDs independent of device title, area and locale."""
    if object_id := CONTROL_OBJECT_IDS.get(f"{platform}.{key}"):
        entity.entity_id = f"{platform}.{object_id}"


def migrate_control_entity_ids(hass, config_entry) -> None:
    """Repair 0.4.0's automatically generated IDs without changing legacy IDs."""
    registry = er.async_get(hass)
    for contract, object_id in CONTROL_OBJECT_IDS.items():
        platform, key = contract.split(".", 1)
        unique_id = f"{DOMAIN}_{config_entry.data['host']}_{key}"
        current_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        target_id = f"{platform}.{object_id}"
        if not current_id or current_id == target_id:
            continue
        current = registry.async_get(current_id)
        if current is None or current.name is not None:
            continue
        # 0.4.0 used the original entity name as an automatic ID base.
        suffix = slugify(current.original_name or "")
        if (
            current.suggested_object_id is not None
            or not suffix
            or not current_id.endswith(f"_{suffix}")
        ):
            continue
        prefixes = {config_entry.title, DEFAULT_NAME, "Force H3X Bridge"}
        device = (
            dr.async_get(hass).async_get(current.device_id)
            if current.device_id
            else None
        )
        if device:
            prefixes.update(name for name in (device.name, device.name_by_user) if name)
        area_id = current.area_id or (device.area_id if device else None)
        if area_id and (area := ar.async_get(hass).async_get_area(area_id)):
            prefixes.update(f"{area.name} {name}" for name in tuple(prefixes))
        automatic_ids = {
            f"{platform}.{slugify(f'{name} {current.original_name}')}"
            for name in prefixes
        }
        if current_id not in automatic_ids:
            continue
        if (
            registry.async_get(target_id) is not None
            or hass.states.get(target_id) is not None
        ):
            LOGGER.warning(
                "Dashboard ID %s is occupied; preserving %s", target_id, current_id
            )
            continue
        registry.async_update_entity(current_id, new_entity_id=target_id)
