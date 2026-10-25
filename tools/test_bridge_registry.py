"""Exercise Home Assistant's real entity-ID generation for bridge controls."""

from datetime import timedelta
import logging
from types import MappingProxyType
from pathlib import Path

import pytest
import yaml
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.helpers import device_registry as dr, entity_registry as er, frame
from homeassistant.helpers.entity_platform import (
    EntityPlatform,
    _async_derive_object_ids,
)

from test_runtime_controls import system  # noqa: F401
from custom_components.pylontech_h3x_bridge import number, select, sensor, switch
from custom_components.pylontech_h3x_bridge.entity_ids import migrate_control_entity_ids

EXPECTED = {
    "switch.force_charge": "switch.pylontech_h3x_bridge_force_charge",
    "switch.weather_reserve": "switch.pylontech_h3x_bridge_weather_reserve",
    "number.force_charge_rate": "number.pylontech_h3x_bridge_force_charge_rate",
    "number.weather_discharge_rate": "number.pylontech_h3x_bridge_weather_discharge_rate",
    "select.charge_method": "select.pylontech_h3x_bridge_charge_method",
    "select.weather_threshold": "select.pylontech_h3x_bridge_weather_alert_threshold",
    "sensor.control_owner": "sensor.pylontech_h3x_bridge_control_owner",
    "sensor.charge_control_status": "sensor.pylontech_h3x_bridge_charge_control_status",
}


async def registries(hass, data, title):
    frame.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    entry = ConfigEntry(
        data=data,
        domain="pylontech_h3x_bridge",
        options={},
        title=title,
        version=1,
        minor_version=1,
        source="user",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=(),
    )
    hass.config_entries._entries[entry.entry_id] = entry
    hass.config_entries._initialized.set()
    hass.data[dr.DATA_REGISTRY] = dr.DeviceRegistry(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, entry.entry_id)},
        name=title,
    )
    return entry, device, er.async_get(hass)


def register(hass, entry, device, registry, domain, entity):
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain=domain,
        platform_name=entry.domain,
        platform=None,
        scan_interval=timedelta(seconds=10),
        entity_namespace=None,
    )
    entity.internal_integration_suggested_object_id = (
        entity.entity_id.split(".", 1)[1] if entity.entity_id else None
    )
    suggested, base = _async_derive_object_ids(entity, platform)
    return registry.async_get_or_create(
        domain,
        entry.domain,
        entity.unique_id,
        config_entry=entry,
        device_id=device.id,
        suggested_object_id=suggested,
        object_id_base=base,
        original_name=entity.name,
        has_entity_name=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title", ["Force H3X Bridge", "Garage battery", "Pylontech H3X Bridge"]
)
async def test_dashboard_control_ids_do_not_depend_on_device_title(system, title):
    hass, coordinator, control, old_entry, registers = system
    entry, device, registry = await registries(hass, old_entry.data, title)
    for domain, descriptions, cls in (
        ("switch", switch.SWITCH_TYPES, switch.PylontechSwitch),
        ("number", number.NUMBER_TYPES, number.PylontechNumber),
        ("select", select.SELECT_TYPES, select.PylontechSelect),
        ("sensor", sensor.SENSOR_TYPES, sensor.PylontechSensor),
    ):
        for description in descriptions:
            key = f"{domain}.{description.key}"
            if key not in EXPECTED:
                continue
            entity = cls(coordinator, description, entry)
            registered = register(hass, entry, device, registry, domain, entity)
            assert registered.entity_id == EXPECTED[key]


@pytest.mark.asyncio
async def test_upgrade_migrates_only_new_automatic_ids(system):
    hass, coordinator, control, old_entry, registers = system
    entry, device, registry = await registries(hass, old_entry.data, "Force H3X Bridge")
    force = switch.PylontechSwitch(
        coordinator,
        next(d for d in switch.SWITCH_TYPES if d.key == "force_charge"),
        entry,
    )
    force.entity_id = None  # Reproduce the entity created by 0.4.0.
    old = register(hass, entry, device, registry, "switch", force)
    soc = sensor.PylontechSensor(
        coordinator,
        next(d for d in sensor.SENSOR_TYPES if d.key == "battery_soc"),
        entry,
    )
    legacy = register(hass, entry, device, registry, "sensor", soc)
    assert old.entity_id == "switch.force_h3x_bridge_force_charge"
    migrate_control_entity_ids(hass, entry)
    assert (
        registry.async_get_entity_id("switch", entry.domain, force.unique_id)
        == EXPECTED["switch.force_charge"]
    )
    assert registry.async_get(legacy.entity_id) is not None


@pytest.mark.asyncio
async def test_upgrade_preserves_custom_names_and_other_devices(system):
    hass, coordinator, control, old_entry, registers = system
    entry, device, registry = await registries(hass, old_entry.data, "Garage battery")
    force = switch.PylontechSwitch(
        coordinator,
        next(d for d in switch.SWITCH_TYPES if d.key == "force_charge"),
        entry,
    )
    force.entity_id = None
    old = register(hass, entry, device, registry, "switch", force)
    registry.async_update_entity(old.entity_id, name="My emergency button")
    migrate_control_entity_ids(hass, entry)
    assert registry.async_get(old.entity_id).name == "My emergency button"


@pytest.mark.asyncio
async def test_upgrade_preserves_custom_ids_and_occupied_targets(system):
    hass, coordinator, control, old_entry, registers = system
    entry, device, registry = await registries(hass, old_entry.data, "Garage battery")
    force = switch.PylontechSwitch(
        coordinator,
        next(d for d in switch.SWITCH_TYPES if d.key == "force_charge"),
        entry,
    )
    force.entity_id = None
    old = register(hass, entry, device, registry, "switch", force)
    custom = registry.async_update_entity(
        old.entity_id, new_entity_id="switch.emergency_force_charge"
    )
    migrate_control_entity_ids(hass, entry)
    assert registry.async_get(custom.entity_id) is not None
    registry.async_update_entity(custom.entity_id, new_entity_id=old.entity_id)
    occupied = registry.async_get_or_create(
        "switch",
        "other_vendor",
        "other",
        config_entry=None,
        suggested_object_id="pylontech_h3x_bridge_force_charge",
    )
    migrate_control_entity_ids(hass, entry)
    assert registry.async_get(old.entity_id) is not None
    assert registry.async_get(occupied.entity_id).platform == "other_vendor"


@pytest.mark.asyncio
async def test_all_override_entities_are_added_to_home_assistant(system):
    hass, coordinator, control, old_entry, registers = system
    entry, device, registry = await registries(hass, old_entry.data, "Force H3X Bridge")
    coordinator.data.update(control.diagnostics())
    platforms = []
    try:
        for domain, descriptions, cls in (
            ("switch", switch.SWITCH_TYPES, switch.PylontechSwitch),
            ("number", number.NUMBER_TYPES, number.PylontechNumber),
            ("select", select.SELECT_TYPES, select.PylontechSelect),
            ("sensor", sensor.SENSOR_TYPES, sensor.PylontechSensor),
        ):
            platform = EntityPlatform(
                hass=hass,
                logger=logging.getLogger(__name__),
                domain=domain,
                platform_name=entry.domain,
                platform=None,
                scan_interval=timedelta(seconds=10),
                entity_namespace=None,
            )
            platform.config_entry = entry
            platforms.append(platform)
            for description in descriptions:
                if f"{domain}.{description.key}" in EXPECTED:
                    await platform._async_add_entity(
                        cls(coordinator, description, entry), False, registry, None
                    )
        for entity_id in EXPECTED.values():
            assert hass.states.get(entity_id) is not None, entity_id
    finally:
        for platform in platforms:
            await platform.async_reset()


@pytest.mark.asyncio
async def test_dashboard_bridge_attributes_exist_in_idle_and_after_command(system):
    hass, coordinator, control, old_entry, registers = system
    entry, device, registry = await registries(
        hass, old_entry.data, "Pylontech H3X Bridge"
    )
    coordinator.data.update(control.diagnostics())
    platforms = []
    required = {
        EXPECTED["sensor.charge_control_status"]: {
            "requested_power_percent",
            "measured_battery_status",
            "measured_battery_power_w",
            "grid_power_entity",
            "charge_headroom_w",
            "grid_without_battery_w",
        },
        "number.pylontech_h3x_bridge_charge_discharge_power_ref": {
            "readback_raw",
            "readback_matches",
            "power_control_path",
        },
    }
    root = Path(__file__).resolve().parents[1]

    def collect(node):
        if isinstance(node, dict):
            entity_id = node.get("entity", "")
            if ".pylontech_h3x_bridge_" in entity_id and "attribute" in node:
                required.setdefault(entity_id, set()).add(node["attribute"])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for repo, name in (
        ("h3x-predictive-dispatch", "h3x-predictive-dispatch"),
        ("h3x-energy-arbitrage", "pylontech-h3x-energy"),
    ):
        path = root / repo / "dashboards" / f"{name}.yaml"
        if path.exists():
            collect(yaml.safe_load(path.read_text(encoding="utf-8")))

    def assert_contract():
        for entity_id, attributes in required.items():
            state = hass.states.get(entity_id)
            assert state is not None, entity_id
            assert attributes <= state.attributes.keys(), (
                entity_id,
                attributes - state.attributes.keys(),
            )

    try:
        for domain, descriptions, cls in (
            ("sensor", sensor.SENSOR_TYPES, sensor.PylontechSensor),
            ("number", number.NUMBER_TYPES, number.PylontechNumber),
        ):
            platform = EntityPlatform(
                hass=hass,
                logger=logging.getLogger(__name__),
                domain=domain,
                platform_name=entry.domain,
                platform=None,
                scan_interval=timedelta(seconds=10),
                entity_namespace=None,
            )
            platform.config_entry = entry
            platforms.append(platform)
            for description in descriptions:
                if description.key in {
                    "charge_control_status",
                    "charge_discharge_power",
                }:
                    await platform._async_add_entity(
                        cls(coordinator, description, entry), False, registry, None
                    )
        assert_contract()
        await control.async_set("force_charge_rate", 10, refresh=False)
        await control.async_set("force_charge", True)
        assert_contract()
        state = hass.states.get(EXPECTED["sensor.charge_control_status"])
        assert state.attributes["requested_power_percent"] == -10
        reference = hass.states.get(
            "number.pylontech_h3x_bridge_charge_discharge_power_ref"
        )
        assert reference.attributes["readback_raw"] == -100
        assert reference.attributes["readback_matches"] is True
        assert reference.attributes["requested_register_word_hex"] == "0xFF9C"
    finally:
        for platform in platforms:
            await platform.async_reset()
