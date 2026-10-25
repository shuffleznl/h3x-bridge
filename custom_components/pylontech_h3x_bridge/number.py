"""Number platform for Pylontech H3X Bridge."""
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from homeassistant.exceptions import HomeAssistantError

@dataclass
class PylontechNumberEntityDescription(NumberEntityDescription):
    
    register_address: int = 0
    slave_id: int = 2
    scale: float = 1.0 
    is_32bit: bool = False
    signed: bool = False
    round_native_value: bool = False
    force_user_mode_for_nonzero: bool = False


NUMBER_TYPES: tuple[PylontechNumberEntityDescription, ...] = (
    PylontechNumberEntityDescription(
        key="force_charge_rate", name="Force Charge Rate", icon="mdi:battery-charging",
        native_unit_of_measurement=PERCENTAGE, native_min_value=1,
        native_max_value=100, native_step=1, mode=NumberMode.BOX,
    ),
    PylontechNumberEntityDescription(
        key="weather_discharge_rate", name="Weather Discharge Rate", icon="mdi:battery-minus",
        native_unit_of_measurement=PERCENTAGE, native_min_value=0,
        native_max_value=20, native_step=1, mode=NumberMode.BOX,
    ),
    PylontechNumberEntityDescription(
        key="charge_discharge_power",
        name="Charge/Discharge Power Ref",
        icon="mdi:swap-vertical-bold",
        native_unit_of_measurement=PERCENTAGE,
        register_address=40901, 
        slave_id=2,
        native_min_value=-100.0, # -100% (max charge)
        native_max_value=100.0,  # +100% (max discharge)
        native_step=1,
        scale=0.1, # User 100% -> Modbus 1000 * 0.1Pn% = 100.0%
        mode=NumberMode.BOX, # BOX is better than slider
        signed=True,
        round_native_value=True,
        force_user_mode_for_nonzero=True,
    ),
    PylontechNumberEntityDescription(
        key="charge_limit_soc",
        name="Charge Limit SOC",
        icon="mdi:battery-arrow-up",
        native_unit_of_measurement=PERCENTAGE,
        register_address=40902, 
        slave_id=2,
        native_min_value=50,    
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    PylontechNumberEntityDescription(
        key="discharge_limit_soc",
        name="Discharge Limit SOC (EPS)",
        icon="mdi:battery-arrow-down",
        native_unit_of_measurement=PERCENTAGE,
        register_address=40903, 
        slave_id=2,
        native_min_value=5,     
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    PylontechNumberEntityDescription(
        key="meter_export_power_max",
        name="Meter max Power (Export)",
        icon="mdi:transmission-tower-export",
        native_unit_of_measurement="W",
        device_class=None,

        native_min_value=-20000,
        native_max_value=-1,
        native_step=100,
        register_address=40401,
        is_32bit=True, # Zorg dat je number.py 32-bit ondersteunt!
        slave_id=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pylontech number based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PylontechNumber(coordinator, description, entry)
        for description in NUMBER_TYPES
    ]

    async_add_entities(entities)


class PylontechNumber(CoordinatorEntity, NumberEntity):
    """Representation of a Pylontech number input."""

    _attr_has_entity_name = True
    entity_description: PylontechNumberEntityDescription

    def __init__(
        self,
        coordinator,
        description: PylontechNumberEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the number."""
        self.entity_description = description
        if description.native_min_value is not None:
            self._attr_native_min_value = description.native_min_value
        if description.native_max_value is not None:
            self._attr_native_max_value = description.native_max_value
        if description.native_step is not None:
            self._attr_native_step = description.native_step
        if description.native_unit_of_measurement is not None:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        if description.mode is not None:
            self._attr_mode = description.mode

        super().__init__(coordinator)
        
        self._attr_unique_id = f"{DOMAIN}_{entry.data['host']}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": MODEL,
        }

    @property
    def native_value(self) -> float | None:
        """Geef de huidige ingestelde waarde terug inclusief schaling."""
        if self.entity_description.key in {"force_charge_rate", "weather_discharge_rate"}:
            return self.coordinator.override.settings[self.entity_description.key]
        raw_value = self.coordinator.data.get(self.entity_description.key)
        if raw_value is not None:
            native_value = raw_value * self.entity_description.scale
            if self.entity_description.round_native_value:
                return round(native_value)
            # We ronden af op 1 decimaal om zwevende komma fouten (zoals 99.90000001) te voorkomen
            return round(native_value, 1)
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Dit wordt aangeroepen als je in Home Assistant de waarde aanpast."""
        if self.entity_description.key in {"force_charge_rate", "weather_discharge_rate"}:
            if not self.native_min_value <= value <= self.native_max_value:
                raise HomeAssistantError("Rate outside allowed range")
            await self.coordinator.override.async_set(self.entity_description.key, round(value))
            return
        if self.entity_description.key == "charge_discharge_power" and self._context.user_id:
            # A human adjustment must survive the next optimizer update.
            if self.coordinator.override.weather_active:
                raise HomeAssistantError("Disable weather reserve before changing manual power")
            if value < 0:
                await self.coordinator.override.async_set("force_charge_rate", max(1, round(abs(value))))
                await self.coordinator.override.async_set("force_charge", True)
                return
            if self.coordinator.override.active:
                await self.coordinator.override.async_set("force_charge", False)
        if self.entity_description.round_native_value:
            value = round(value)

        # Draai de schaling om voor de Modbus verzending (bijv -50 / 0.1 = -500)
        raw_value = int(round(value / self.entity_description.scale))

        if self.entity_description.force_user_mode_for_nonzero:
            success = await self.coordinator.async_set_charge_discharge_power(
                value=raw_value,
                slave=self.entity_description.slave_id,
            )
        elif getattr(self.entity_description, "is_32bit", False):
            success = await self.coordinator.async_write_register_32bit(
                address=self.entity_description.register_address,
                value=raw_value,
                slave=self.entity_description.slave_id
            )
        else:
            success = await self.coordinator.async_write_register(
                address=self.entity_description.register_address,
                value=raw_value,
                slave=self.entity_description.slave_id,
                signed=self.entity_description.signed,
            )

        if success:
            self.coordinator.data[self.entity_description.key] = raw_value
            self.async_write_ha_state()
        else:
            raise HomeAssistantError("Command failed or control is owned by force charge/weather reserve")
