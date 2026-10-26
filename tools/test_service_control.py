"""Exercise bridge ownership through real Home Assistant entity services."""

from datetime import timedelta
import asyncio
import importlib
import logging
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import pytest_asyncio

from test_runtime_controls import system  # noqa: F401
from test_bridge_registry import registries
from homeassistant.auth import auth_manager_from_config
from homeassistant.components.number import const as number_const
from homeassistant.components.number.services import (
    async_setup_services as number_services,
)
from homeassistant.components.select import const as select_const
from homeassistant.components.select.services import (
    async_setup_services as select_services,
)
from homeassistant.components.switch import const as switch_const
from homeassistant.components.switch.services import (
    async_setup_services as switch_services,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.pylontech_h3x_bridge import number, select, sensor, switch

ROOT = Path(__file__).resolve().parents[1]
FORCE = "switch.pylontech_h3x_bridge_force_charge"
REFERENCE = "number.pylontech_h3x_bridge_charge_discharge_power_ref"


@pytest_asyncio.fixture
async def service_system(system):
    hass, coordinator, control, old_entry, registers = system
    entry, device, registry = await registries(
        hass, old_entry.data, "Pylontech H3X Bridge"
    )
    hass.auth = await auth_manager_from_config(hass, [], [])
    user = await hass.auth.async_create_user("Dashboard user")
    hass.data["test_user_id"] = user.id
    hass.data.setdefault(entry.domain, {})[entry.entry_id] = coordinator
    coordinator.data.update(control.diagnostics())
    platforms = []
    try:
        for domain, descriptions, cls, data_key, setup in (
            (
                "number",
                number.NUMBER_TYPES,
                number.PylontechNumber,
                number_const.DATA_COMPONENT,
                number_services,
            ),
            (
                "select",
                select.SELECT_TYPES,
                select.PylontechSelect,
                select_const.DATA_COMPONENT,
                select_services,
            ),
            (
                "switch",
                switch.SWITCH_TYPES,
                switch.PylontechSwitch,
                switch_const.DATA_COMPONENT,
                switch_services,
            ),
            ("sensor", sensor.SENSOR_TYPES, sensor.PylontechSensor, "sensor", None),
        ):
            component = EntityComponent(logging.getLogger(__name__), domain, hass)
            hass.data[data_key] = component
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
            component._platforms[entry.domain] = platform
            platforms.append(platform)
            if setup:
                setup(hass)
            for description in descriptions:
                if description.key in {
                    "force_charge",
                    "force_charge_rate",
                    "charge_discharge_power",
                    "ems_mode",
                    "charge_limit_soc",
                    "discharge_limit_soc",
                    "control_owner",
                    "charge_control_status",
                    "battery_soc",
                    "charge_method",
                    "weather_threshold",
                    "weather_reserve",
                    "weather_discharge_rate",
                }:
                    await platform._async_add_entity(
                        cls(coordinator, description, entry), False, registry, None
                    )
        yield hass, coordinator, control, entry, registers
    finally:
        for platform in platforms:
            await platform.async_reset()


@pytest.mark.asyncio
async def test_force_switch_service_charges_without_number_service(
    service_system, monkeypatch
):
    hass, coordinator, control, entry, registers = service_system
    call = hass.services.async_call
    calls = []

    async def capture(self, domain, service, *args, **kwargs):
        calls.append((domain, service))
        return await call(domain, service, *args, **kwargs)

    monkeypatch.setattr(type(hass.services), "async_call", capture)
    await hass.services.async_call(
        "switch",
        "turn_on",
        {},
        target={"entity_id": FORCE},
        blocking=True,
        context=Context(user_id=hass.data["test_user_id"]),
    )
    assert ("number", "set_value") not in calls
    assert registers[(2, 40901)] > 32767
    assert coordinator.last_power_command["readback_matches"] is True
    assert hass.states.get(FORCE).state == "on"
    await call("switch", "turn_off", {}, target={"entity_id": FORCE}, blocking=True)
    assert registers[(2, 40901)] == 0
    assert control.charging_response == "not_requested"


@pytest.mark.asyncio
async def test_service_reports_conflict_separately_from_modbus_failure(service_system):
    hass, coordinator, control, entry, registers = service_system
    await hass.services.async_call(
        "switch", "turn_on", {}, target={"entity_id": FORCE}, blocking=True
    )
    reference = registers[(2, 40901)]
    with pytest.raises(HomeAssistantError, match="reserved by manual force charge"):
        await hass.services.async_call(
            "number",
            "set_value",
            {"value": 10},
            target={"entity_id": REFERENCE},
            blocking=True,
        )
    assert registers[(2, 40901)] == reference
    assert coordinator.last_rejected_command["entity_id"] == REFERENCE
    assert coordinator.last_rejected_command["owner"] == "force_charge"


@pytest.mark.asyncio
async def test_manual_negative_service_can_update_active_force_charge(service_system):
    hass, coordinator, control, entry, registers = service_system
    await hass.services.async_call(
        "switch", "turn_on", {}, target={"entity_id": FORCE}, blocking=True
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"value": -10},
        target={"entity_id": REFERENCE},
        blocking=True,
        context=Context(user_id=hass.data["test_user_id"]),
    )
    assert control.settings["force_charge_rate"] == 10
    assert registers[(2, 40901)] == 0xFF9C
    assert coordinator.last_rejected_command is None


@pytest.mark.asyncio
async def test_actual_write_failure_is_not_mislabeled_as_ownership(
    service_system, monkeypatch
):
    hass, coordinator, control, entry, registers = service_system
    read = coordinator.client.read_holding_registers

    async def unapplied_read(address, count, slave):
        result = await read(address, count, slave)
        if address == 40901 and count == 7:
            result[0] = 0
        return result

    monkeypatch.setattr(coordinator.client, "read_holding_registers", unapplied_read)
    with pytest.raises(
        HomeAssistantError, match="Register 40901 read-back 0, requested -100"
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {"value": -10},
            target={"entity_id": REFERENCE},
            blocking=True,
        )
    assert coordinator.last_rejected_command is None


@pytest.mark.asyncio
async def test_force_switch_failure_exposes_diagnostics_in_home_assistant(
    service_system, monkeypatch
):
    hass, coordinator, control, entry, registers = service_system
    read = coordinator.client.read_holding_registers

    async def rejected_command(address, count, slave):
        result = await read(address, count, slave)
        if address == 40901 and count == 7:
            result[0] = 0
        return result

    monkeypatch.setattr(coordinator.client, "read_holding_registers", rejected_command)
    with pytest.raises(HomeAssistantError, match="Register 40901 read-back 0"):
        await hass.services.async_call(
            "switch", "turn_on", {}, target={"entity_id": FORCE}, blocking=True
        )
    status = hass.states.get("sensor.pylontech_h3x_bridge_charge_control_status")
    assert status.attributes["charging_response"] == "command_failed"
    assert "Register 40901 read-back 0" in status.attributes["last_power_command_error"]
    assert status.attributes["last_rejected_entity_id"] == "none"
    assert status.state == "command failed"
    assert status.attributes["telemetry_available"] is False
    assert status.attributes["measured_battery_power_w"] is None
    assert (
        hass.states.get("sensor.pylontech_h3x_bridge_battery_soc").state
        == "unavailable"
    )
    assert hass.states.get(FORCE).state == "on"
    assert (
        hass.states.get("select.pylontech_h3x_bridge_charge_method").state
        == "user_reference"
    )
    await hass.services.async_call(
        "switch", "turn_off", {}, target={"entity_id": FORCE}, blocking=True
    )
    assert hass.states.get(FORCE).state == "off"
    assert registers[(2, 40901)] == 0


def controller_module(repo, domain):
    import custom_components

    path = ROOT / repo / "custom_components"
    if not path.exists():
        pytest.skip(f"{repo} sibling checkout not present")
    if str(path) not in custom_components.__path__:
        custom_components.__path__.append(str(path))
    return importlib.import_module(f"custom_components.{domain}.coordinator")


@pytest.mark.asyncio
@pytest.mark.parametrize("change_options", [False, True])
async def test_predictive_solver_does_not_block_ha_and_discards_changed_options(service_system, monkeypatch, change_options):
    hass, coordinator, control, entry, registers = service_system
    module = controller_module("h3x-predictive-dispatch", "h3x_predictive_dispatch")
    dispatch = module.H3XPredictiveDispatchCoordinator(hass, entry)
    main_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()

    def slow_optimizer(slots, settings):
        assert threading.get_ident() != main_thread
        started.set()
        assert release.wait(5)
        return SimpleNamespace(schedule=[], reason="worker completed")

    monkeypatch.setattr(dispatch._predictive_optimizer, "optimize", slow_optimizer)
    now = module.dt_util.utcnow()
    band = module.ForecastBand(500, 1000, 1500, 10, 0.8)
    task = asyncio.create_task(dispatch._run_predictive_optimizer(
        future_slots=[module.PriceSlot(now, now + timedelta(minutes=15), 0.1)],
        current_energy=15, min_energy=4, max_energy=26, terminal_energy=15,
        charge_allowed=True, discharge_allowed=True, usable_capacity_kwh=29.17,
        periodic_full_charge_due=False, load_forecast=SimpleNamespace(bands=[band]),
        solar_forecast=[band], current_load_power_w=1000, current_solar_power_w=0,
        block_ev_discharge=False, ev_threshold_w=1000,
    ))
    try:
        for _ in range(100):
            if started.is_set():
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert started.is_set() and not task.done()
        hass.states.async_set("sensor.executor_test", "responsive")
        assert hass.states.get("sensor.executor_test").state == "responsive"
        if change_options:
            hass.config_entries.async_update_entry(entry, options={**entry.options, "max_soc": 85})
    finally:
        release.set()
    result = await task
    if change_options:
        assert result.action == "failsafe"
        assert "settings changed" in result.reason
    else:
        assert result.action == "idle" and result.reason == "worker completed"


@pytest.mark.asyncio
async def test_async_predictive_decision_with_real_optimizer(service_system):
    hass, coordinator, control, entry, registers = service_system
    module = controller_module("h3x-predictive-dispatch", "h3x_predictive_dispatch")
    hass.config_entries.async_update_entry(entry, options={
        **entry.options, module.CONF_AREA: "NL", module.CONF_CURRENCY: "EUR",
        module.CONF_PERIODIC_FULL_CHARGE_ENABLED: False,
    })
    dispatch = module.H3XPredictiveDispatchCoordinator(hass, entry)
    now = module.dt_util.utcnow()
    slots = [module.PriceSlot(
        now + timedelta(minutes=15 * index), now + timedelta(minutes=15 * (index + 1)), price,
    ) for index, price in enumerate([0.05, 0.07, 0.3, 0.4, 0.3, 0.1, 0.06, 0.2])]
    decision = await dispatch._compute_decision(slots)
    assert decision.action in {"idle", "charge", "discharge"}
    assert decision.current_price is not None
    assert decision.attributes["optimizer"] == "predictive_dispatch_dp_v1"
    assert registers[(2, 40901)] == 0  # Computing is not device actuation.


@pytest.mark.asyncio
async def test_ev_sensor_failure_during_solve_blocks_discharge(service_system, monkeypatch):
    hass, coordinator, control, entry, registers = service_system
    module = controller_module("h3x-predictive-dispatch", "h3x_predictive_dispatch")
    ev_entity = "sensor.test_ev_power"
    hass.states.async_set(ev_entity, "11000", {"unit_of_measurement": "W"})
    hass.config_entries.async_update_entry(entry, options={
        **entry.options, module.CONF_AREA: "NL", module.CONF_CURRENCY: "EUR",
        module.CONF_PERIODIC_FULL_CHARGE_ENABLED: False,
        module.CONF_EV_FORECAST_MODE: "sensor", module.CONF_EV_POWER_ENTITY: ev_entity,
        module.CONF_BLOCK_DISCHARGE_WHILE_EV_CHARGING: True,
    })
    dispatch = module.H3XPredictiveDispatchCoordinator(hass, entry)

    async def solve(**kwargs):
        hass.states.async_set(ev_entity, "unavailable", {"unit_of_measurement": "W"})
        return module.Decision(action="discharge", target_power_w=1500, target_power_percent=10)

    monkeypatch.setattr(dispatch, "_run_predictive_optimizer", solve)
    now = module.dt_util.utcnow()
    decision = await dispatch._compute_decision([module.PriceSlot(now, now + timedelta(minutes=15), 0.3)])
    assert decision.action == "idle"
    assert decision.target_power_w == 0
    assert "EV charging state unavailable" in decision.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "takeover_point", ["after_soc", "after_power", "during_service"]
)
@pytest.mark.parametrize(
    "repo,domain,classname",
    [
        (
            "h3x-predictive-dispatch",
            "h3x_predictive_dispatch",
            "H3XPredictiveDispatchCoordinator",
        ),
        ("h3x-energy-arbitrage", "h3x_energy_arbitrage", "H3XArbitrageCoordinator"),
    ],
)
async def test_optimizer_yields_if_force_charge_starts_mid_dispatch(
    service_system, monkeypatch, caplog, takeover_point, repo, domain, classname
):
    hass, coordinator, control, entry, registers = service_system
    module = controller_module(repo, domain)
    dispatch = getattr(module, classname)(hass, entry)
    decision = module.Decision(
        action="discharge",
        reason="economic dispatch",
        target_power_w=1380,
        target_power_percent=10,
    )
    call = hass.services.async_call
    taken_over = False

    async def take_over(self, domain, service, *args, **kwargs):
        nonlocal taken_over
        entity_id = kwargs.get("target", {}).get("entity_id")
        target = (
            REFERENCE
            if takeover_point != "after_soc"
            else "number.pylontech_h3x_bridge_charge_limit_soc"
        )
        should_take_over = entity_id == target and not taken_over
        if takeover_point != "during_service":
            result = await call(domain, service, *args, **kwargs)
        if should_take_over:
            taken_over = True
            await call(
                "switch",
                "turn_on",
                {},
                target={"entity_id": FORCE},
                blocking=True,
                context=Context(user_id=hass.data["test_user_id"]),
            )
        if takeover_point == "during_service":
            result = await call(domain, service, *args, **kwargs)
        return result

    monkeypatch.setattr(type(hass.services), "async_call", take_over)
    await dispatch._apply_decision(decision)
    assert taken_over
    assert decision.applied is False
    assert decision.apply_error is None
    assert "override active" in decision.reason
    assert dispatch._last_power_percent is None
    assert dispatch._last_ems_mode is None
    assert registers[(2, 40901)] > 32767
    assert "Failed to apply H3X arbitrage decision" not in caplog.text
