#!/usr/bin/env python3
"""Validate Force H3 module-count wiring in the bridge."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "custom_components" / "pylontech_h3x_bridge"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(source: str, token: str, label: str) -> None:
    if token not in source:
        raise AssertionError(f"{label} missing {token!r}")


def literal_assignments(source: str) -> dict[str, object]:
    """Return literal top-level assignments from Python source."""
    tree = ast.parse(source)
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            values[node.targets[0].id] = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
    return values


def main() -> None:
    coordinator = read(BRIDGE / "coordinator.py")
    sensor = read(BRIDGE / "sensor.py")
    manifest = read(BRIDGE / "manifest.json")

    require(coordinator, "BMS_ESS_BASE_ADDRESS = 0x1400", "bridge coordinator")
    require(coordinator, "BMS_MODULE_NUMBER_OFFSET = 0x0036", "bridge coordinator")
    require(
        coordinator,
        "REGISTER_BMS_MODULE_NUMBER = BMS_ESS_BASE_ADDRESS + BMS_MODULE_NUMBER_OFFSET",
        "bridge coordinator",
    )
    require(
        coordinator,
        "REGISTER_BMS_MODULE_NUMBER, 1, 1",
        "bridge coordinator module-count read",
    )
    require(
        coordinator,
        'data["battery_module_count"] = module_count',
        "bridge coordinator module-count data",
    )
    require(sensor, 'key="battery_module_count"', "bridge sensor")
    require(sensor, 'name="Battery Module Count"', "bridge sensor")
    require(sensor, 'key="battery_system_capacity"', "bridge sensor")
    require(sensor, 'key="battery_usable_capacity"', "bridge sensor")
    require(sensor, 'key="battery_usable_capacity_theoretical"', "bridge sensor")
    require(sensor, 'key="battery_usable_capacity_deviation_pct"', "bridge sensor")
    require(manifest, '"version": "0.4.1"', "bridge manifest")

    assignments = literal_assignments(coordinator)
    system_capacity = assignments["FORCE_H3_SYSTEM_CAPACITY_KWH"]
    usable_capacity = assignments["FORCE_H3_USABLE_CAPACITY_KWH"]
    usable_dod = assignments["FORCE_H3_USABLE_DOD"]
    if not isinstance(system_capacity, dict) or not isinstance(usable_capacity, dict):
        raise AssertionError("capacity maps must be literal dictionaries")
    if usable_dod != 0.95:
        raise AssertionError("usable DoD must match datasheet 95%")
    if set(system_capacity) != set(range(2, 8)):
        raise AssertionError("system capacity map must cover 2..7 modules")
    if set(usable_capacity) != set(range(2, 8)):
        raise AssertionError("usable capacity map must cover 2..7 modules")

    for modules, nominal in system_capacity.items():
        usable = usable_capacity[modules]
        theoretical = nominal * usable_dod
        deviation_pct = abs(usable - theoretical) / theoretical * 100
        if deviation_pct > 5.0:
            raise AssertionError(
                f"usable capacity for {modules} modules differs by "
                f"{deviation_pct:.2f}% from 95% DoD theoretical"
            )



if __name__ == "__main__":
    main()
