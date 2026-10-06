#!/usr/bin/env python3
"""CLI-прогон конвейера без сайта (обязательная часть задания).

Примеры:
    python run_pipeline.py
    python run_pipeline.py --input data/energy_hourly.csv --out outputs
    python run_pipeline.py --threshold 3.5 --window 24
    python run_pipeline.py --input my.csv --out outputs_demo

Что делает:
    1. Загружает конфигурацию (config.yaml + переменные окружения).
    2. Читает временной ряд (по умолчанию — встроенный демо-сэмпл).
    3. Явно обрабатывает пропуски, считает z-score (окно 24), baseline'ы.
    4. Сохраняет артефакты в папку --out:
        anomalies.csv · anomalies.json · anomalies.png · report.html
    5. Печатает сводку и топ-тревог в консоль.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from app import data_loader, pipeline  # noqa: E402
from app.config import load_config, validate_override  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Обнаружение аномалий: z-score, окно 24 (вариант 16)")
    parser.add_argument("--config", default=None, help="путь к config.yaml")
    parser.add_argument("--input", default=None, help="CSV с колонками timestamp,value")
    parser.add_argument("--out", default=None, help="папка для артефактов")
    parser.add_argument("--threshold", type=float, default=None, help="переопределить порог")
    parser.add_argument("--window", type=int, default=None, help="переопределить окно")
    args = parser.parse_args()

    cfg = load_config(args.config)
    try:
        validate_override(args.threshold, args.window)
    except ValueError as e:
        print(f"Ошибка параметров: {e}")
        return 2
    if args.threshold is not None:
        cfg["detection"]["threshold"] = args.threshold
    if args.window is not None:
        cfg["detection"]["window"] = args.window

    input_path = Path(args.input) if args.input else BASE_DIR / cfg["data"]["sample_path"]
    out_dir = Path(args.out) if args.out else BASE_DIR / cfg["output"]["dir"]

    if not input_path.exists():
        print(f"Файл не найден: {input_path}. Сначала запустите: python scripts/prepare_sample.py")
        return 2

    print(f"Вход: {input_path}")
    print(f"Конфиг: {cfg['_path']}  (порог {cfg['detection']['threshold']}σ, окно {cfg['detection']['window']})")
    print("Прогон конвейера…\n")

    df_raw = data_loader.load_csv_path(input_path)
    result = pipeline.run_pipeline(df_raw, cfg, pace=0.0, artifacts_dir=out_dir)

    st = result["stats"]
    print("=" * 68)
    print(f"Точек: {st['points_total']:,} · проскорено: {result['scored_points']:,} · "
          f"аномалий: {result['anomalies_count']} ({result['anomaly_rate']}%)")
    print(f"Пропуски: дыр {st['gaps_total']} → интерполировано {st['gaps_interpolated']}, "
          f"оставлено пустыми {st['gaps_left_blank']}")
    print(f"model_version: {result['model_version']} · порог {result['threshold']}σ · окно {result['window']}")
    if result["status"]["notes"]:
        print("Статус:")
        for note in result["status"]["notes"]:
            print(f"  ! {note}")

    print("\nBaseline-сравнение:")
    hdr = f"  {'Метод':38} {'Аномалий':>9} {'Доля,%':>7} {'MAE':>8} {'Жаккар':>7}"
    print(hdr)
    for b in result["baselines"]:
        mae = f"{b['mae']:.4f}" if b["mae"] is not None else "—"
        print(f"  {b['name']:38} {b['anomalies']:>9} {b['anomaly_rate']:>7.2f} {mae:>8} {b['jaccard_with_variant']:>7.2f}")

    print(f"\nТоп-10 тревог (из {result['anomalies_count']}):")
    print(f"  {'timestamp':26} {'value':>8} {'score':>7}  severity")
    for a in result["anomalies"][:10]:
        print(f"  {a['timestamp']:26} {a['value']:>8.3f} {a['score']:>7.2f}  {a['severity']}")

    print("\nАртефакты:")
    for key, path in result["artifacts"].items():
        print(f"  {key:5} → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
