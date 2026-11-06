# generates synthetic building sensor data for testing and demo purposes
from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

FAULT_TYPES = {"stuck_sensor", "simultaneous_heat_cool", "valve_open_cold"}
MAX_BUILDINGS = 26 
MAX_ZONES = 99

# Occupancy and weather follow the campus clock, so the simulation runs in local time.
# Everything it outputs is UTC: local times repeat or skip an hour at daylight-saving changes.
LOCAL_TZ = "Australia/Melbourne"
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # e.g. 2026-11-10T03:05:00Z

ZONE, METER = "zone", "meter"  # device_type values
METER_ID = "meter-main"  # one electricity meter per building

# Building electricity: base load (lights, plug loads, fans) plus HVAC. Heating and cooling
# are both assumed electric (reverse-cycle), so either valve opening draws power.
BASE_KW_OCCUPIED = 40.0
BASE_KW_UNOCCUPIED = 12.0
KW_PER_VALVE_PCT = 0.05  # one zone at 100% heating or cooling draws 5 kW

# Zones and meters share one flat table. Each row fills only its own device type's fields;
# the other type's fields are null, as they will be in Parquet.
ZONE_FIELDS = ["zone_temp_c", "setpoint_c", "co2_ppm", "heating_valve_pct", "cooling_valve_pct"]
METER_FIELDS = ["kw", "kwh_total"]
COLUMNS = ["ts", "device_type", "building_id", "device_id", *ZONE_FIELDS, *METER_FIELDS]

# MQTT: each device publishes to campus/{building_id}/{device_id}. The IDs travel in the topic,
# so the payload carries everything else; the IoT rule adds the IDs back from the topic.
TOPIC_PREFIX = "campus"
PAYLOAD_FIELDS = ["ts", "device_type", *ZONE_FIELDS, *METER_FIELDS]

# Ground truth for testing the fault rules. Real sensors don't report their own faults, so this
# never goes into the readings or MQTT messages; the simulator writes it to a separate file.
# One row per injected fault; zones not listed are healthy. start_ts and end_ts are inclusive.
LABEL_COLUMNS = ["building_id", "device_id", "fault", "start_ts", "end_ts"]


@dataclass
class SimConfig:
    start: str = "2026-11-02"
    days: int = 1
    interval_min: int = 5
    buildings: int = 3
    zones_per_building: int = 10
    seed: int = 42
    # "{building_id}/{zone_id}" into fault type, e.g. {"bldg-a/zone-03": "stuck_sensor"}
    faults: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 1 <= self.buildings <= MAX_BUILDINGS:
            raise ValueError(f"buildings must be 1-{MAX_BUILDINGS}, got {self.buildings}")
        if not 1 <= self.zones_per_building <= MAX_ZONES:
            raise ValueError(f"zones_per_building must be 1-{MAX_ZONES}, got {self.zones_per_building}")
        bad = {f for f in self.faults.values() if f not in FAULT_TYPES}
        if bad:
            raise ValueError(f"Unknown fault type(s): {bad}. Choose from {FAULT_TYPES}")
        known = {device_key(b, z) for b, z in zone_ids(self)}
        missing = set(self.faults) - known
        if missing:
            raise ValueError(
                f"Fault target(s) not in this campus: {sorted(missing)}. Use e.g. 'bldg-a/zone-03'"
            )


def building_id_for(n: int) -> str:
    """1 -> 'bldg-a'. Lowercase and hyphenated: IDs become S3 folder and column values."""
    return f"bldg-{chr(ord('a') + n - 1)}"


def zone_id_for(n: int) -> str:
    """7 -> 'zone-07'. Unique within a building only; the key is (building_id, device_id)."""
    return f"zone-{n:02d}"


def device_key(building: str, zone: str) -> str:
    """'bldg-a', 'zone-07' -> 'bldg-a/zone-07', the same as the tail of the MQTT topic."""
    return f"{building}/{zone}"


def zone_ids(cfg: SimConfig) -> list[tuple[str, str]]:
    return [
        (building_id_for(b), zone_id_for(z))
        for b in range(1, cfg.buildings + 1)
        for z in range(1, cfg.zones_per_building + 1)
    ]


def to_utc_iso(ts: pd.DatetimeIndex) -> pd.Index:
    """Timezone-aware timestamps -> ISO 8601 UTC strings, the `ts` format in every message."""
    return ts.tz_convert("UTC").strftime(TS_FORMAT)


def _outdoor_temp(ts: pd.DatetimeIndex) -> np.ndarray:
    """Simple daily cycle: coolest ~5am, warmest ~3pm try to mimic Melbourne yduring spring."""
    hour = ts.hour + ts.minute / 60
    return 15 + 6 * np.sin((hour - 9) / 24 * 2 * np.pi)


def _occupied(ts: pd.DatetimeIndex) -> np.ndarray:
    return (ts.day_of_week < 5) & (ts.hour >= 8) & (ts.hour < 18)


def simulate_zone(
    ts: pd.DatetimeIndex, building_id: str, zone_id: str, fault: str | None, rng: np.random.Generator
) -> tuple[pd.DataFrame, np.ndarray]:
    """One zone's readings, plus the electricity (kW) its HVAC actually drew at each step."""
    n = len(ts)
    occupied = _occupied(ts)
    outdoor = _outdoor_temp(ts)
    setpoint = np.where(occupied, 22.0, 18.0)

    # Zone drifts toward outdoor temp; HVAC pulls it back toward setpoint.
    temp = np.empty(n)
    heat = np.zeros(n)
    cool = np.zeros(n)
    delivered = np.zeros(n)  # heating actually supplied, which can differ from what the valve reports
    t = setpoint[0] + rng.normal(0, 0.5)
    for i in range(n):
        error = setpoint[i] - t
        heat[i] = np.clip(error * 40, 0, 100)
        cool[i] = np.clip(-error * 40, 0, 100)
        delivered[i] = heat[i]
        if fault == "valve_open_cold":
            heat[i] = 100.0  # valve reports fully open...
            delivered[i] = 0.0  # ...but no hot water arrives, so the zone stays cold
        t += 0.05 * (outdoor[i] - t) + 0.02 * delivered[i] - 0.02 * cool[i]
        temp[i] = t + rng.normal(0, 0.1)

    co2 = 420 + np.where(occupied, rng.uniform(250, 600), 0) + rng.normal(0, 15, n)

    if fault == "stuck_sensor":
        temp[:] = round(float(temp[0]), 1)
    if fault == "simultaneous_heat_cool":
        heat = np.maximum(heat, 60.0)
        cool = np.maximum(cool, 60.0)
        delivered = heat  # both valves really are open, so both draw power

    hvac_kw = KW_PER_VALVE_PCT * (delivered + cool)
    readings = pd.DataFrame(
        {
            "ts": to_utc_iso(ts),
            "device_type": ZONE,
            "building_id": building_id,
            "device_id": zone_id,
            "zone_temp_c": temp.round(2),
            "setpoint_c": setpoint,
            "co2_ppm": co2.round(0),
            "heating_valve_pct": heat.round(1),
            "cooling_valve_pct": cool.round(1),
        }
    )
    return readings, hvac_kw


def simulate_meter(
    ts: pd.DatetimeIndex, building_id: str, hvac_kw: np.ndarray, interval_min: int, rng: np.random.Generator
) -> pd.DataFrame:
    """The building's main electricity meter: base load plus every zone's HVAC draw.

    kw is average demand over the 5 minutes ending at ts. kwh_total is the meter's running
    total at ts, starting from an arbitrary reading as a real meter would; it never decreases.
    """
    base = np.where(_occupied(ts), BASE_KW_OCCUPIED, BASE_KW_UNOCCUPIED)
    kw = np.clip(base + hvac_kw + rng.normal(0, 0.5, len(ts)), 0, None)
    kwh_total = rng.uniform(100_000, 500_000) + np.cumsum(kw * interval_min / 60)
    return pd.DataFrame(
        {
            "ts": to_utc_iso(ts),
            "device_type": METER,
            "building_id": building_id,
            "device_id": METER_ID,
            "kw": kw.round(2),
            "kwh_total": kwh_total.round(2),
        }
    )


def _timestamps(cfg: SimConfig) -> pd.DatetimeIndex:
    return pd.date_range(
        cfg.start,
        periods=cfg.days * 24 * 60 // cfg.interval_min,
        freq=f"{cfg.interval_min}min",
        tz=LOCAL_TZ,
    )


def simulate(cfg: SimConfig) -> pd.DataFrame:
    """Sensor readings only: what a real building would send, with no fault labels."""
    rng = np.random.default_rng(cfg.seed)
    ts = _timestamps(cfg)
    zones: list[pd.DataFrame] = []
    building_hvac_kw: dict[str, np.ndarray] = {}
    for b, z in zone_ids(cfg):
        readings, hvac_kw = simulate_zone(ts, b, z, cfg.faults.get(device_key(b, z)), rng)
        zones.append(readings)
        building_hvac_kw[b] = building_hvac_kw.get(b, 0) + hvac_kw
    # Meters draw random numbers only after every zone has, so a given seed gives the same
    # zone readings it did before meters existed.
    meters = [
        simulate_meter(ts, b, hvac_kw, cfg.interval_min, rng) for b, hvac_kw in building_hvac_kw.items()
    ]
    return pd.concat(zones + meters, ignore_index=True)[COLUMNS]


def fault_labels(cfg: SimConfig) -> pd.DataFrame:
    """Which zones have which fault, and when. Faults currently last the whole run."""
    first, last = to_utc_iso(_timestamps(cfg)[[0, -1]])
    rows = [
        {"building_id": b, "device_id": z, "fault": fault, "start_ts": first, "end_ts": last}
        for b, z in zone_ids(cfg)
        if (fault := cfg.faults.get(device_key(b, z)))
    ]
    return pd.DataFrame(rows, columns=LABEL_COLUMNS)


def labels_path(readings_path: Path) -> Path:
    """data/week.parquet -> data/week_labels.csv"""
    return readings_path.with_name(f"{readings_path.stem}_labels.csv")


def topic_for(building_id: str, device_id: str) -> str:
    """'bldg-a', 'zone-07' -> 'campus/bldg-a/zone-07'."""
    for segment in (building_id, device_id):
        # '/' would add a topic level; '+' and '#' are MQTT wildcards and can't be published to.
        if not segment or any(ch in segment for ch in "/+#"):
            raise ValueError(f"Invalid topic segment: {segment!r}")
    return f"{TOPIC_PREFIX}/{device_key(building_id, device_id)}"


def to_message(row: Mapping[str, object]) -> dict[str, object]:
    """One reading -> the flat JSON payload its device publishes.

    Leaves out building_id and device_id (they're in the topic) and any field the device
    doesn't report, so a meter message has no zone fields at all rather than nulls.
    """
    return {k: row[k] for k in PAYLOAD_FIELDS if not pd.isna(row[k])}


def _parse_faults(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        zone, _, fault = item.partition(":")
        out[zone] = fault
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default="2026-11-02")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--buildings", type=int, default=3)
    p.add_argument("--zones", type=int, default=10)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--fault",
        action="append",
        default=[],
        help="BUILDING/ZONE:FAULT_TYPE, e.g. bldg-a/zone-03:stuck_sensor (repeatable)",
    )
    p.add_argument("--out", default="data/readings.parquet")
    args = p.parse_args()

    cfg = SimConfig(
        start=args.start,
        days=args.days,
        buildings=args.buildings,
        zones_per_building=args.zones,
        seed=args.seed,
        faults=_parse_faults(args.fault),
    )
    df = simulate(cfg)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".csv":
        df.to_csv(out, index=False)
    else:
        df.to_parquet(out, index=False)
    devices = df.drop_duplicates(["building_id", "device_id"]).device_type.value_counts()
    n_zones, n_meters = devices.get(ZONE, 0), devices.get(METER, 0)
    print(f"Wrote {len(df):,} rows for {n_zones} zones and {n_meters} meters to {out}")

    labels = fault_labels(cfg)
    labels.to_csv(labels_path(out), index=False)
    print(f"Wrote {len(labels)} fault label(s) to {labels_path(out)}")


if __name__ == "__main__":
    main()
