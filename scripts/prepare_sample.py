#!/usr/bin/env python3
"""Подготовка демонстрационного сэмпла реального ряда энергопотребления.

Источник данных (открытый):
    UCI Machine Learning Repository — "Individual Household Electric Power Consumption"
    https://archive.ics.uci.edu/dataset/275/individual+household+electric+power+consumption
    Авторы: Georges Hebrail (EDF R&D), Alice Gerard (EDF R&D), 2012.
    Лицензия: CC BY 4.0 (Creative Commons Attribution 4.0 International).

Что делает скрипт:
 1. Скачивает архив датасета (~20 МБ).
 2. Читает только нужные колонки (Date, Time, Global_active_power) порциями —
    экономно по памяти.
 3. Агрегирует минутные данные в среднечасовые значения мощности (кВт).
 4. Берёт срез в 2 месяца (2008-11-01 .. 2008-12-31) — богатая структура:
    суточные пики, выходные, естественные всплески.
 5. Сохраняет data/energy_hourly.csv с колонками timestamp,value.

Скрипт идемпотентен: если архив уже скачан, повторной загрузки не будет.
"""

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

UCI_URL = (
    "https://archive.ics.uci.edu/ml/machine-learning-databases/"
    "00235/household_power_consumption.zip"
)
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_ZIP = BASE_DIR / "data" / "_uci_cache.zip"
OUT_CSV = BASE_DIR / "data" / "energy_hourly.csv"
FULL_CSV = BASE_DIR / "data" / "energy_full_hourly.csv"

SLICE_START = "2008-11-01"
SLICE_END = "2008-12-31"


def download() -> Path:
    if CACHE_ZIP.exists() and CACHE_ZIP.stat().st_size > 1_000_000:
        print(f"[1/5] Архив уже скачан: {CACHE_ZIP.name} ({CACHE_ZIP.stat().st_size/1e6:.1f} МБ)")
        return CACHE_ZIP
    print(f"[1/5] Скачиваю датасет UCI (~20 МБ): {UCI_URL}")
    CACHE_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(UCI_URL, timeout=120) as resp, open(CACHE_ZIP, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    print(f"      Сохранено: {CACHE_ZIP} ({CACHE_ZIP.stat().st_size/1e6:.1f} МБ)")
    return CACHE_ZIP


def read_hourly(zip_path: Path) -> pd.Series:
    print("[2/5] Извлекаю колонки Date, Time, Global_active_power (порциями)...")
    with zipfile.ZipFile(zip_path) as zf:
        inner = zf.namelist()[0]
        with zf.open(inner) as fh:
            chunks = []
            for chunk in pd.read_csv(
                fh,
                sep=";",
                usecols=["Date", "Time", "Global_active_power"],
                na_values=["?"],
                dtype={"Global_active_power": "float64"},
                chunksize=500_000,
            ):
                chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    print(f"      Строк минутных данных: {len(df):,}")

    print("[3/5] Агрегирую в среднечасовой ряд (кВт)...")
    ts = pd.to_datetime(
        df["Date"] + " " + df["Time"], format="%d/%m/%Y %H:%M:%S", errors="coerce"
    )
    s = pd.Series(df["Global_active_power"].to_numpy(), index=ts, name="value").dropna()
    hourly = s.resample("h").mean()
    return hourly


def main() -> int:
    zip_path = download()
    hourly = read_hourly(zip_path)

    print(f"[4/5] Сохраняю полный часовой ряд: {FULL_CSV.name}")
    full = hourly.rename_axis("timestamp").reset_index()
    full.to_csv(FULL_CSV, index=False)
    print(f"      Точек: {len(full):,} | период: {full['timestamp'].min()} .. {full['timestamp'].max()}")

    sample = hourly.loc[SLICE_START:SLICE_END].rename_axis("timestamp").reset_index()
    sample.to_csv(OUT_CSV, index=False)
    n_missing = int(sample["value"].isna().sum())
    print(f"[5/5] Демо-сэмпл: {OUT_CSV.name}")
    print(f"      Период: {sample['timestamp'].min()} .. {sample['timestamp'].max()}")
    print(f"      Точек: {len(sample)} | пропусков (NaN-часов): {n_missing}")
    print(f"      Среднее: {sample['value'].mean():.3f} кВт | Std: {sample['value'].std():.3f} кВт")

    if CACHE_ZIP.exists():
        CACHE_ZIP.unlink()
        print("      Временный архив удалён (репозиторий остаётся лёгким).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
