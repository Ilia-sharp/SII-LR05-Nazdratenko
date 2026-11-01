#!/usr/bin/env python3
"""Служебный скрипт: контекст тревог для REPORT.md (день недели, соседи, окно).

Порог и окно берутся из config.yaml / переменных окружения — в скрипте их нет.
Окно статистики совпадает с окном модели: v[t-W+1 … t] (включает текущую точку).
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import data_loader, detector  # noqa: E402
from app.config import load_config  # noqa: E402

cfg = load_config()
window = int(cfg["detection"]["window"])
threshold = float(cfg["detection"]["threshold"])
bands = cfg["detection"]["severity"]

df = data_loader.load_csv_path(BASE / cfg["data"]["sample_path"])
grid, _ = data_loader.build_regular_grid(df)
scored, flat = detector.rolling_zscore(grid, window, threshold, bands)

an = scored[scored["is_anomaly"] == True]  # noqa: E712
print(f"anomalies: {len(an)}, flatline={flat}, threshold={threshold}, window={window}")
DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
for ts, row in an.iterrows():
    w = scored["value"]
    i = w.index.get_loc(ts)
    before = w.iloc[max(0, i - 3):i]
    after = w.iloc[i + 1:i + 4]
    win = w.iloc[max(0, i - window + 1):i + 1]
    print(
        f"{ts.isoformat()} | {DAYS[ts.dayofweek]} {ts.hour:02d}:00 | "
        f"value={row['value']:.3f} score={row['score']:.2f} sev={row['severity']} | "
        f"prev3mean={before.mean():.3f} next3mean={after.mean():.3f} | "
        f"window{window}mean={win.mean():.3f} window{window}std={win.std():.3f}"
    )

# немного общей статистики для отчёта
val = scored["value"]
hourly = val.groupby(val.index.hour).mean()
print("\nсреднее по часам суток (топ-4 пиковых часа):")
print(hourly.sort_values(ascending=False).head(4).round(3).to_string())
print(f"\nсреднее будни={val[val.index.dayofweek < 5].mean():.3f} выходные={val[val.index.dayofweek >= 5].mean():.3f}")
print(f"границы severity: medium {threshold * float(bands['medium'])} high {threshold * float(bands['high'])}")
