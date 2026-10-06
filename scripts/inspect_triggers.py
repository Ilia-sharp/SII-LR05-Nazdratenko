#!/usr/bin/env python3
"""Служебный скрипт: контекст тревог для REPORT.md (день недели, соседи, доля)."""
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import data_loader, detector  # noqa: E402
from app.config import load_config  # noqa: E402

cfg = load_config()
df = data_loader.load_csv_path(BASE / cfg["data"]["sample_path"])
grid, _ = data_loader.build_regular_grid(df)
scored, flat = detector.rolling_zscore(grid, 24, 3.0, cfg["detection"]["severity"])

an = scored[scored["is_anomaly"] == True]  # noqa: E712
print(f"anomalies: {len(an)}, flatline={flat}")
DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
for ts, row in an.iterrows():
    w = scored["value"]
    i = w.index.get_loc(ts)
    before = w.iloc[max(0, i - 3):i]
    after = w.iloc[i + 1:i + 4]
    print(
        f"{ts.isoformat()} | {DAYS[ts.dayofweek]} {ts.hour:02d}:00 | "
        f"value={row['value']:.3f} score={row['score']:.2f} sev={row['severity']} | "
        f"prev3mean={before.mean():.3f} next3mean={after.mean():.3f} | "
        f"window24mean={w.iloc[max(0,i-24):i].mean():.3f} window24std={w.iloc[max(0,i-24):i].std():.3f}"
    )

# немного общей статистики для отчёта
val = scored["value"]
hourly = val.groupby(val.index.hour).mean()
print("\nсреднее по часам суток (топ-4 пиковых часа):")
print(hourly.sort_values(ascending=False).head(4).round(3).to_string())
print(f"\nсреднее будни={val[val.index.dayofweek < 5].mean():.3f} выходные={val[val.index.dayofweek >= 5].mean():.3f}")
print(f"порог severity: medium {3.0*1.1} high {3.0*1.3}")
