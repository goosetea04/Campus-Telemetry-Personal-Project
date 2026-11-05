import pandas as pd
import pytest

from simulator.simulate import COLUMNS, SimConfig, simulate


@pytest.fixture
def healthy() -> pd.DataFrame:
    return simulate(SimConfig(days=1, buildings=2, zones_per_building=3))


def test_shape_and_columns(healthy):
    # 2 buildings x 3 zones x 288 five-minute readings
    assert list(healthy.columns) == COLUMNS
    assert len(healthy) == 2 * 3 * 288
    assert len(healthy[["building_id", "zone_id"]].drop_duplicates()) == 6


def test_ids_are_lowercase_and_hyphenated(healthy):
    assert set(healthy.building_id) == {"bldg-a", "bldg-b"}
    assert set(healthy.zone_id) == {"zone-01", "zone-02", "zone-03"}


def test_no_missing_values(healthy):
    assert not healthy.isna().any().any()


def test_values_in_physical_range(healthy):
    assert healthy.zone_temp_c.between(5, 40).all()
    assert healthy.co2_ppm.between(300, 2000).all()
    assert healthy.heating_valve_pct.between(0, 100).all()
    assert healthy.cooling_valve_pct.between(0, 100).all()


def test_same_seed_is_reproducible():
    a = simulate(SimConfig(buildings=1, zones_per_building=2))
    b = simulate(SimConfig(buildings=1, zones_per_building=2))
    pd.testing.assert_frame_equal(a, b)


def test_healthy_zones_have_no_faults(healthy):
    assert (healthy.fault_injected == "none").all()


def test_stuck_sensor_flatlines():
    df = simulate(SimConfig(buildings=1, zones_per_building=2, faults={"bldg-a/zone-01": "stuck_sensor"}))
    stuck = df[df.zone_id == "zone-01"]
    normal = df[df.zone_id == "zone-02"]
    assert stuck.zone_temp_c.nunique() == 1
    assert normal.zone_temp_c.nunique() > 10
    assert (stuck.fault_injected == "stuck_sensor").all()


def test_simultaneous_heat_cool():
    df = simulate(
        SimConfig(buildings=1, zones_per_building=1, faults={"bldg-a/zone-01": "simultaneous_heat_cool"})
    )
    assert ((df.heating_valve_pct >= 60) & (df.cooling_valve_pct >= 60)).all()


def test_valve_open_cold_zone_misses_setpoint():
    df = simulate(SimConfig(buildings=1, zones_per_building=1, faults={"bldg-a/zone-01": "valve_open_cold"}))
    occupied = df[df.setpoint_c == 22.0]
    assert (df.heating_valve_pct == 100).all()
    assert (occupied.zone_temp_c < occupied.setpoint_c - 1).mean() > 0.8


def test_unknown_fault_rejected():
    with pytest.raises(ValueError):
        SimConfig(faults={"bldg-a/zone-01": "on_fire"})


def test_same_zone_number_in_two_buildings_is_two_zones():
    df = simulate(SimConfig(buildings=2, zones_per_building=1, faults={"bldg-b/zone-01": "stuck_sensor"}))
    assert (df[df.building_id == "bldg-a"].fault_injected == "none").all()
    assert (df[df.building_id == "bldg-b"].fault_injected == "stuck_sensor").all()


@pytest.mark.parametrize("target", ["B01-Z01", "bldg-a/zone-99", "zone-01"])
def test_unknown_fault_target_rejected(target):
    with pytest.raises(ValueError, match="not in this campus"):
        SimConfig(buildings=1, zones_per_building=2, faults={target: "stuck_sensor"})


@pytest.mark.parametrize("kwargs", [{"buildings": 27}, {"buildings": 0}, {"zones_per_building": 100}])
def test_campus_size_limited_by_id_format(kwargs):
    with pytest.raises(ValueError):
        SimConfig(**kwargs)
