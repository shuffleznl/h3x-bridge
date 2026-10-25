"""Sensor platform for Pylontech H3X Bridge."""
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .entity_ids import set_control_entity_id

# Hier definiëren we alle sensoren. De 'key' moet exact overeenkomen 
# met de naam die we in coordinator.py aan de data dictionary hebben gegeven!
SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(key="control_owner", name="Control Owner", icon="mdi:account-key"),
    SensorEntityDescription(key="charge_control_status", name="Charge Control Status", icon="mdi:battery-check"),
    # --- Status Sensoren ---
    SensorEntityDescription(
        key="inverter_status",
        name="Inverter Status",
        icon="mdi:information-outline",
    ),
    SensorEntityDescription(
        key="battery_status",
        name="Battery Status",
        icon="mdi:battery-information",
    ),

    # --- PV (Zonnepanelen) ---
    SensorEntityDescription(
        key="pv1_voltage", name="PV1 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv1_current", name="PV1 Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv2_voltage", name="PV2 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv2_current", name="PV2 Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv3_voltage", name="PV3 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv3_current", name="PV3 Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv_total_power", name="PV Total Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv_total_energy", name="PV Total Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),

    # --- Vermogen & Net ---
    SensorEntityDescription(
        key="ac_total_power", name="AC Total Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_total_power", name="Grid Total Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="load_power", name="Load Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="total_grid_import", name="Total Grid Import",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="total_grid_export", name="Total Grid Export",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="grid_voltage_r", name="Grid Voltage R",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_voltage_s", name="Grid Voltage S",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_voltage_t", name="Grid Voltage T",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="ac_frequency", name="AC Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    # --- Batterij (Omvormer registers) ---
    SensorEntityDescription(
        key="battery_power", name="Battery Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="battery_voltage", name="Battery Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="battery_current", name="Battery Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="battery_soc", name="Battery SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="total_battery_charge", name="Total Battery Charge",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="total_battery_discharge", name="Total Battery Discharge",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),

    # --- BMS Registers ---
    SensorEntityDescription(
        key="bms_voltage", name="BMS Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bms_temperature", name="BMS Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bms_soc", name="BMS SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bms_soh", name="BMS SOH",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
    ),
    SensorEntityDescription(
        key="bms_cycles", name="BMS Cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-sync",
    ),
    SensorEntityDescription(
        key="battery_module_count",
        name="Battery Module Count",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-stack",
    ),
    SensorEntityDescription(
        key="battery_system_capacity",
        name="Battery System Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-high",
    ),
    SensorEntityDescription(
        key="battery_usable_capacity",
        name="Battery Usable Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-check",
    ),
    SensorEntityDescription(
        key="battery_usable_capacity_theoretical",
        name="Battery Usable Capacity Theoretical",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-clock",
    ),
    SensorEntityDescription(
        key="battery_usable_capacity_deviation_pct",
        name="Battery Usable Capacity Deviation",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent-outline",
    ),
    SensorEntityDescription(
        key="bms_cell_voltage_max", name="BMS Cell Voltage Max",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bms_cell_voltage_min", name="BMS Cell Voltage Min",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    
    # --- Omvormer Algemeen ---
    SensorEntityDescription(
        key="inverter_temperature", name="Inverter Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    SensorEntityDescription(
        key="heatsink_temperature", name="Heatsink Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pylontech sensor based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # Maak voor elke definitie hierboven een PylontechSensor object aan
    entities = [
        PylontechSensor(coordinator, description, entry)
        for description in SENSOR_TYPES
    ]

    async_add_entities(entities)


class PylontechSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Pylontech H3X Bridge sensor."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Expose local diagnostics even when physical telemetry is unavailable."""
        if self.entity_description.key in {"control_owner", "charge_control_status"}:
            return True
        return super().available

    @property
    def extra_state_attributes(self):
        if self.entity_description.key != "charge_control_status":
            return None
        coordinator = self.coordinator
        control = coordinator.override
        raw_reference = coordinator.data.get("charge_discharge_power")
        requested = control.power_budget.get("target_reference_raw", raw_reference)
        telemetry_available = coordinator.last_update_success
        error = control.last_error or coordinator.last_power_command.get("command_error", "none")
        if not telemetry_available and coordinator.last_exception:
            error = str(coordinator.last_exception)
        return {
            "charge_headroom_w": None,
            "grid_without_battery_w": None,
            **control.power_budget,
            "weather_alert_entity": control.options.get("weather_alert_entity"),
            "grid_power_entity": control.options.get("grid_power_entity"),
            "siren_entity": control.options.get("alert_siren_entity"),
            "manual_charge_expires": control.manual_until.isoformat() if control.manual_until else None,
            "weather_full_charge_reached": control.weather_full,
            "charge_method": control.settings["charge_method"],
            "requested_power_percent": requested / 10 if requested is not None else None,
            "readback_reference_percent": raw_reference / 10 if raw_reference is not None else None,
            "power_control_path": "time_slot" if control._slot_active else "signed_reference",
            "last_rejected_command": coordinator.last_rejected_command,
            "last_rejected_entity_id": (coordinator.last_rejected_command or {}).get("entity_id", "none"),
            "last_power_command_error": coordinator.last_power_command.get("command_error", "none"),
            "last_control_error": error,
            "charging_response": control.charging_response,
            "charging_observations": control.charging_observations,
            "telemetry_available": telemetry_available,
            "measured_battery_status": coordinator.data.get("battery_status") if telemetry_available else None,
            "measured_battery_power_w": coordinator.data.get("battery_power") if telemetry_available else None,
        }

    def __init__(
        self,
        coordinator,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        
        # Zorgt voor unieke ID's in de database
        self._attr_unique_id = f"{DOMAIN}_{entry.data['host']}_{description.key}"
        set_control_entity_id(self, "sensor", description.key)

        # Dit groepeert alle sensoren onder één Apparaat in Home Assistant
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": MODEL,
        }

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self.entity_description.key in {"control_owner", "charge_control_status"}:
            return self.coordinator.override.diagnostics()[self.entity_description.key]
        # Haal de waarde op uit de dictionary die we in coordinator.py hebben gebouwd
        return self.coordinator.data.get(self.entity_description.key)
