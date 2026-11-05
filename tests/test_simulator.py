import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from simulator.simulate import (
    COLUMNS,
    LABEL_COLUMNS,
    LOCAL_TZ,
    METER_FIELDS,
    METER_ID,
    ZONE_FIELDS,
    SimConfig,
    fault_labels,
    labels_path,
    main,
    simulate,
)


def cfg(faults: dict[str, str] | None = None, buildings: int = 1, zones_per_building: int = 1) -> SimConfig:
    return SimConfig(buildings=buildings, zones_per_building=zones_per_building, faults=faults or {})


def zones(df: pd.DataFrame) -> pd.DataFrame:
    return df[df.device_type == "zone"]


def meters(df: pd.DataFrame) -> pd.DataFrame:
    return df[df.device_type == "meter"]


def building_kwh(cfg: SimConfig, building: str = "bldg-a") -> float:
    m = meters(simulate(cfg))
    m = m[m.building_id == building]
    return float(m.kwh_total.iloc[-1] - m.kwh_total.iloc[0])


@pytest.fixture
def healthy() -> pd.DataFrame:
    return simulate(SimConfig(days=1, buildings=2, zones_per_building=3))


# --- Shape and IDs -------------------------------------------------------------------------


def test_shape_and_columns(healthy):
    # 2 buildings x (3 zones + 1 meter) x 288 five-minute readings
    assert list(healthy.columns) == COLUMNS
    assert len(healthy) == 2 * 4 * 288
    devices = healthy.drop_duplicates(["building_id", "device_id"])
    assert devices.device_type.value_counts().to_dict() == {"zone": 6, "meter": 2}


def test_ids_are_lowercase_and_hyphenated(healthy):
    assert set(healthy.building_id) == {"bldg-a", "bldg-b"}
    assert set(zones(healthy).device_id) == {"zone-01", "zone-02", "zone-03"}
    assert set(meters(healthy).device_id) == {METER_ID}


def test_each_device_type_fills_only_its_own_fields(healthy):
    z, m = zones(healthy), meters(healthy)
    assert z[ZONE_FIELDS].notna().all().all()
    assert z[METER_FIELDS].isna().all().all()
    assert m[METER_FIELDS].notna().all().all()
    assert m[ZONE_FIELDS].isna().all().all()
    assert healthy[["ts", "device_type", "building_id", "device_id"]].notna().all().all()


# --- Timestamps ----------------------------------------------------------------------------


def test_ts_is_utc_iso_string(healthy):
    assert healthy.ts.str.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z").all()


def test_ts_starts_at_local_midnight():
    # 2026-11-02 00:00 in Melbourne is AEDT (UTC+11), so 13:00 UTC the day before.
    df = simulate(SimConfig(start="2026-11-02", buildings=1, zones_per_building=1))
    assert df.ts.iloc[0] == "2026-11-01T13:00:00Z"


def test_occupancy_still_follows_local_time(healthy):
    local = pd.to_datetime(healthy.ts).dt.tz_convert(LOCAL_TZ)
    occupied_hours = set(local[healthy.setpoint_c == 22.0].dt.hour)
    assert occupied_hours == set(range(8, 18))


def test_ts_unique_across_daylight_saving_end():
    # Melbourne clocks go back 3am -> 2am on 2026-04-05, so local 02:00-02:55 happens twice.
    zone = zones(simulate(SimConfig(start="2026-04-05", buildings=1, zones_per_building=1)))
    local_wall_clock = pd.to_datetime(zone.ts).dt.tz_convert(LOCAL_TZ).dt.strftime("%Y-%m-%d %H:%M")
    assert local_wall_clock.duplicated().sum() == 12  # local time is ambiguous...
    assert zone.ts.is_unique  # ...UTC is not
    assert (pd.to_datetime(zone.ts).diff().dropna() == pd.Timedelta(minutes=5)).all()


# --- Zone readings -------------------------------------------------------------------------


def test_values_in_physical_range(healthy):
    z = zones(healthy)
    assert z.zone_temp_c.between(5, 40).all()
    assert z.co2_ppm.between(300, 2000).all()
    assert z.heating_valve_pct.between(0, 100).all()
    assert z.cooling_valve_pct.between(0, 100).all()


def test_same_seed_is_reproducible():
    a = simulate(SimConfig(buildings=1, zones_per_building=2))
    b = simulate(SimConfig(buildings=1, zones_per_building=2))
    pd.testing.assert_frame_equal(a, b)


def test_stuck_sensor_flatlines():
    df = zones(simulate(cfg({"bldg-a/zone-01": "stuck_sensor"}, zones_per_building=2)))
    stuck = df[df.device_id == "zone-01"]
    normal = df[df.device_id == "zone-02"]
    assert stuck.zone_temp_c.nunique() == 1
    assert normal.zone_temp_c.nunique() > 10


def test_simultaneous_heat_cool():
    df = zones(simulate(cfg({"bldg-a/zone-01": "simultaneous_heat_cool"})))
    assert ((df.heating_valve_pct >= 60) & (df.cooling_valve_pct >= 60)).all()


def test_valve_open_cold_zone_misses_setpoint():
    df = zones(simulate(cfg({"bldg-a/zone-01": "valve_open_cold"})))
    occupied = df[df.setpoint_c == 22.0]
    assert (df.heating_valve_pct == 100).all()
    assert (occupied.zone_temp_c < occupied.setpoint_c - 1).mean() > 0.8


def test_same_zone_number_in_two_buildings_is_two_zones():
    df = zones(simulate(cfg({"bldg-b/zone-01": "stuck_sensor"}, buildings=2)))
    assert df[df.building_id == "bldg-a"].zone_temp_c.nunique() > 10
    assert df[df.building_id == "bldg-b"].zone_temp_c.nunique() == 1


# --- Meters --------------------------------------------------------------------------------


def test_kwh_total_only_goes_up(healthy):
    for _, m in meters(healthy).groupby("building_id"):
        assert (m.kwh_total.diff().dropna() > 0).all()


def test_kwh_total_adds_up_kw(healthy):
    m = meters(healthy)
    m = m[m.building_id == "bldg-a"]
    increments = m.kwh_total.diff().dropna().to_numpy()
    expected = m.kw.iloc[1:].to_numpy() * 5 / 60
    assert np.allclose(increments, expected, atol=0.02)  # both columns are rounded to 0.01


def test_meter_draws_more_when_occupied(healthy):
    m = meters(healthy)
    occupied = pd.to_datetime(m.ts).dt.tz_convert(LOCAL_TZ).dt.hour.between(8, 17)
    assert m.kw[occupied].mean() > m.kw[~occupied].mean() + 20


def test_simultaneous_heat_cool_shows_up_on_the_meter():
    # Both valves held at 60%+ is at least 6 kW extra all day: well over 100 kWh.
    healthy = building_kwh(cfg(zones_per_building=3))
    faulty = building_kwh(cfg({"bldg-a/zone-02": "simultaneous_heat_cool"}, zones_per_building=3))
    assert faulty > healthy + 100


def test_meter_counts_heat_delivered_not_valve_reported():
    # valve_open_cold reports 100% heating (5 kW) all day, but no heat arrives, so none is billed.
    healthy = building_kwh(cfg(zones_per_building=3))
    faulty = building_kwh(cfg({"bldg-a/zone-02": "valve_open_cold"}, zones_per_building=3))
    assert faulty < healthy


# --- Fault labels --------------------------------------------------------------------------


def test_readings_carry_no_ground_truth(healthy):
    assert "fault_injected" not in healthy.columns
    assert "fault" not in healthy.columns


def test_healthy_campus_has_no_labels():
    labels = fault_labels(SimConfig(buildings=2, zones_per_building=3))
    assert labels.empty
    assert list(labels.columns) == LABEL_COLUMNS


def test_labels_list_each_fault_for_the_whole_run():
    cfg = SimConfig(
        buildings=2,
        zones_per_building=3,
        faults={"bldg-a/zone-02": "stuck_sensor", "bldg-b/zone-03": "valve_open_cold"},
    )
    labels = fault_labels(cfg)
    readings = simulate(cfg)
    assert list(labels.columns) == LABEL_COLUMNS
    assert set(zip(labels.building_id, labels.device_id, labels.fault, strict=True)) == {
        ("bldg-a", "zone-02", "stuck_sensor"),
        ("bldg-b", "zone-03", "valve_open_cold"),
    }
    assert (labels.start_ts == readings.ts.min()).all()
    assert (labels.end_ts == readings.ts.max()).all()


def test_labels_path_sits_next_to_readings(tmp_path):
    assert labels_path(tmp_path / "week.parquet") == tmp_path / "week_labels.csv"


# --- Config validation ---------------------------------------------------------------------


def test_unknown_fault_rejected():
    with pytest.raises(ValueError):
        SimConfig(faults={"bldg-a/zone-01": "on_fire"})


@pytest.mark.parametrize("target", ["B01-Z01", "bldg-a/zone-99", "zone-01", "bldg-a/meter-main"])
def test_unknown_fault_target_rejected(target):
    with pytest.raises(ValueError, match="not in this campus"):
        SimConfig(buildings=1, zones_per_building=2, faults={target: "stuck_sensor"})


@pytest.mark.parametrize("kwargs", [{"buildings": 27}, {"buildings": 0}, {"zones_per_building": 100}])
def test_campus_size_limited_by_id_format(kwargs):
    with pytest.raises(ValueError):
        SimConfig(**kwargs)


# --- CLI -----------------------------------------------------------------------------------


def test_cli_writes_readings_and_labels(tmp_path, monkeypatch):
    out = tmp_path / "week.parquet"
    argv = ["simulate", "--buildings", "1", "--zones", "2", "--fault", "bldg-a/zone-01:stuck_sensor"]
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out)])
    main()

    table = pq.read_table(out)
    assert "fault_injected" not in table.column_names
    # Missing fields are real Parquet nulls, not NaN values: 2 zones x 288 rows have no kw.
    assert table.column("kw").null_count == 2 * 288
    assert table.column("zone_temp_c").null_count == 288

    labels = pd.read_csv(labels_path(out))
    assert labels[["device_id", "fault"]].values.tolist() == [["zone-01", "stuck_sensor"]]
