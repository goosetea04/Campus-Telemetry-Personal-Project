# generates synthetic building sensor data for testing and demo purposes
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

FAULT_TYPES = {"stuck_sensor", "simultaneous_heat_cool", "valve_open_cold"}

COLUMNS = [
    "timestamp",
    "building_id",
    "zone_id",
    "zone_temp_c",
    "setpoint_c",
    "co2_ppm",
    "heating_valve_pct",
    "cooling_valve_pct",
    "fault_injected",
]


@dataclass
class SimConfig:
    start: str = "2026-11-02"
    days: int = 1
    interval_min: int = 5
    buildings: int = 3
    zones_per_building: int = 10
    seed: int = 42
    # zone_id -> fault type, e.g. {"B01-Z03": "stuck_sensor"}
    faults: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        bad = {f for f in self.faults.values() if f not in FAULT_TYPES}
        if bad:
            raise ValueError(f"Unknown fault type(s): {bad}. Choose from {FAULT_TYPES}")


def zone_ids(cfg: SimConfig) -> list[tuple[str, str]]:
    return [
        (f"B{b:02d}", f"B{b:02d}-Z{z:02d}")
        for b in range(1, cfg.buildings + 1)
        for z in range(1, cfg.zones_per_building + 1)
    ]


def _outdoor_temp(ts: pd.DatetimeIndex) -> np.ndarray:
    """Simple daily cycle: coolest ~5am, warmest ~3pm (Melbourne spring-ish)."""
    hour = ts.hour + ts.minute / 60
    return 15 + 6 * np.sin((hour - 9) / 24 * 2 * np.pi)


def _occupied(ts: pd.DatetimeIndex) -> np.ndarray:
    return (ts.day_of_week < 5) & (ts.hour >= 8) & (ts.hour < 18)


def simulate_zone(
    ts: pd.DatetimeIndex, building_id: str, zone_id: str, fault: str | None, rng: np.random.Generator
) -> pd.DataFrame:
    n = len(ts)
    occupied = _occupied(ts)
    outdoor = _outdoor_temp(ts)
    setpoint = np.where(occupied, 22.0, 18.0)

    # Zone drifts toward outdoor temp; HVAC pulls it back toward setpoint.
    temp = np.empty(n)
    heat = np.zeros(n)
    cool = np.zeros(n)
    t = setpoint[0] + rng.normal(0, 0.5)
    for i in range(n):
        error = setpoint[i] - t
        heat[i] = np.clip(error * 40, 0, 100)
        cool[i] = np.clip(-error * 40, 0, 100)
        heat_delivered = heat[i]
        if fault == "valve_open_cold":
            heat[i] = 100.0  # valve reports fully open...
            heat_delivered = 0.0  # ...but no hot water arrives, so the zone stays cold
        t += 0.05 * (outdoor[i] - t) + 0.02 * heat_delivered - 0.02 * cool[i]
        temp[i] = t + rng.normal(0, 0.1)

    co2 = 420 + np.where(occupied, rng.uniform(250, 600), 0) + rng.normal(0, 15, n)

    if fault == "stuck_sensor":
        temp[:] = round(float(temp[0]), 1)
    if fault == "simultaneous_heat_cool":
        heat = np.maximum(heat, 60.0)
        cool = np.maximum(cool, 60.0)

    return pd.DataFrame(
        {
            "timestamp": ts,
            "building_id": building_id,
            "zone_id": zone_id,
            "zone_temp_c": temp.round(2),
            "setpoint_c": setpoint,
            "co2_ppm": co2.round(0),
            "heating_valve_pct": heat.round(1),
            "cooling_valve_pct": cool.round(1),
            "fault_injected": fault or "none",
        }
    )


def simulate(cfg: SimConfig) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.seed)
    ts = pd.date_range(
        cfg.start,
        periods=cfg.days * 24 * 60 // cfg.interval_min,
        freq=f"{cfg.interval_min}min",
        tz="Australia/Melbourne",
    )
    frames = [
        simulate_zone(ts, b, z, cfg.faults.get(z), rng) for b, z in zone_ids(cfg)
    ]
    return pd.concat(frames, ignore_index=True)[COLUMNS]


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
    p.add_argument("--fault", action="append", default=[], help="ZONE_ID:FAULT_TYPE, repeatable")
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
    print(f"Wrote {len(df):,} rows for {df.zone_id.nunique()} zones to {out}")


if __name__ == "__main__":
    main()
