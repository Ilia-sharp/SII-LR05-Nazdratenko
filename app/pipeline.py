"""Конвейер обнаружения аномалий — «данные -> пропуски -> скоринг -> baseline -> артефакты».

Каждая стадия отчитывается через callback progress(percent, message) —
именно поэтому пользователь сайта видит живой прогресс, а не «крутится и ждёт».

Параметр pace задаёт искусственную паузу между шагами (секунды), чтобы прогресс
на сайте был плавным и читаемым. Для CLI и тестов pace = 0 — скорость важнее.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import data_loader, detector, report as report_mod

STAGES = [
    (5, "Приём данных"),
    (20, "Парсинг и валидация"),
    (40, "Обработка пропусков"),
    (60, "Z-score детекция (окно из конфигурации)"),
    (75, "Baseline-сравнение"),
    (90, "График и артефакты"),
    (100, "Готово"),
]


def run_pipeline(
    df_raw: pd.DataFrame,
    cfg: dict,
    progress=None,
    pace: float = 0.0,
    artifacts_dir: str | Path | None = None,
) -> dict:
    """Полный прогон конвейера. Возвращает словарь-результат для UI/CLI/JSON."""

    def report_progress(percent: int, message: str) -> None:
        if progress:
            progress(percent, message)
        if pace > 0:
            time.sleep(pace)

    threshold = float(cfg["detection"]["threshold"])
    window = int(cfg["detection"]["window"])
    bands = cfg["detection"]["severity"]
    max_interp_gap = int(cfg["data"]["max_interp_gap"])
    model_version = cfg["service"]["model_version"]

    log: list[str] = []

    # --- 1. Приём данных ---------------------------------------------------
    report_progress(5, f"Данные приняты: {len(df_raw):,} строк")
    log.append(f"Принято {len(df_raw):,} строк исходных данных")

    # --- 2. Парсинг и валидация --------------------------------------------
    report_progress(20, "Привожу к регулярной сетке…")
    grid, step = data_loader.build_regular_grid(df_raw)
    log.append(f"Регулярная сетка: шаг {step} → {len(grid):,} точек")
    if len(grid) > 200_000:
        raise ValueError(
            f"Ряд слишком длинный ({len(grid):,} точек). Для Free-тарифа Render (512 МБ) "
            "загрузите срез до ~200 000 точек."
        )

    # --- 3. Обработка пропусков (явная!) ------------------------------------
    report_progress(40, "Ищу пропуски и применяю политику обработки…")
    grid, gap_rows = data_loader.apply_gap_policy(grid, max_interp_gap)
    stats = data_loader.describe_series(grid, gap_rows, step)
    log.append(
        f"Пропуски: дыр {stats['gaps_total']} "
        f"(интерполировано {stats['gaps_interpolated']}, "
        f"оставлено пустыми {stats['gaps_left_blank']}, "
        f"NaN-точек всего {stats['points_missing']})"
    )
    if stats["gaps_total"]:
        report_progress(45, f"Пропусков: {stats['gaps_total']} (длинные дыры не интерполируются)")

    # --- 4. Z-score детекция -------------------------------------------------
    report_progress(60, f"Скользящий z-score: окно {window}, порог {threshold}…")
    scored, flatline = detector.rolling_zscore(grid, window, threshold, bands)
    if flatline:
        log.append("РЯД ПОСТОЯНЕН (std = 0): z-score не определён — тревоги не генерируются")
    n_anom = int(scored["is_anomaly"].fillna(False).sum())
    log.append(f"Z-score: тревог {n_anom} из {int(scored['score'].notna().sum()):,} проскоренных точек")

    # --- 5. Baseline-сравнение ------------------------------------------------
    report_progress(75, "Считаю baseline: «вчера» и глобальный z-score…")
    base_y = detector.baseline_yesterday(
        grid, window, float(cfg["baseline"].get("yesterday_threshold", threshold)), bands
    )
    baselines = [
        _baseline_row(
            "z-score (окно) — метод варианта",
            scored,
            detector.mae_against(scored["value"], scored["value"].rolling(window, min_periods=window).mean()),
        )
    ]
    baselines.append(
        _baseline_row("Наивный «вчера»", base_y, detector.mae_against(grid["value"], grid["value"].shift(window)))
    )
    if cfg["baseline"].get("global_zscore", True):
        base_g = detector.baseline_global_zscore(grid, threshold, bands)
        baselines.append(_baseline_row("Глобальный z-score", base_g, None))
    for b in baselines:
        b["jaccard_with_variant"] = round(
            detector.jaccard(detector.anomaly_set(scored), b.pop("_set")), 3
        )
    log.append("Baseline: " + "; ".join(f"{b['name']} → {b['anomalies']} тревог" for b in baselines))

    # --- 6. Проверка «живости» данных -----------------------------------------
    status_notes: list[str] = []
    stale = False
    if cfg["data"].get("monitor_freshness"):
        last_ts = pd.Timestamp(stats["last_ts"])
        age_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - last_ts.to_pydatetime()).total_seconds() / 3600
        if age_hours > float(cfg["data"].get("stale_after_hours", 48)):
            stale = True
            status_notes.append(
                f"Данные не обновляются {age_hours:.0f} ч (последняя точка {stats['last_ts']}). "
                "Сервис продолжает работать на последнем срезе и помечает данные как stale."
            )
    if flatline:
        status_notes.append(
            "Ряд стал константным (std = 0): z-score не определён, все score = NaN, "
            "тревоги не генерируются. Требуется проверка источника данных."
        )

    # --- 7. Артефакты ----------------------------------------------------------
    report_progress(90, "Строю график и сохраняю артефакты…")
    artifacts: dict[str, str] = {}
    if artifacts_dir is not None:
        out_dir = Path(artifacts_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        anomalies_csv = out_dir / "anomalies.csv"
        anomalies_json = out_dir / "anomalies.json"
        anomalies_png = out_dir / "anomalies.png"
        report_html = out_dir / "report.html"

        result_for_files = _compose_result(
            scored, stats, gap_rows, baselines, cfg, log, status_notes, stale, flatline, artifacts
        )
        report_mod.write_anomalies_csv(scored, anomalies_csv)
        report_mod.write_anomalies_json(result_for_files, anomalies_json)
        report_mod.write_anomalies_png(scored, cfg, anomalies_png)
        report_mod.write_report_html(result_for_files, report_html)
        artifacts = {
            "csv": str(anomalies_csv),
            "json": str(anomalies_json),
            "png": str(anomalies_png),
            "html": str(report_html),
        }
        log.append("Артефакты сохранены: anomalies.csv / anomalies.json / anomalies.png / report.html")

    result = _compose_result(
        scored, stats, gap_rows, baselines, cfg, log, status_notes, stale, flatline, artifacts
    )
    report_progress(100, "Готово")
    return result


def _baseline_row(name: str, scored: pd.DataFrame, mae: float | None) -> dict:
    n_scored = int(scored["score"].notna().sum())
    n_anom = int(scored["is_anomaly"].fillna(False).sum())
    mean_score = float(scored["score"].mean()) if n_scored else 0.0
    return {
        "name": name,
        "scored": n_scored,
        "anomalies": n_anom,
        "anomaly_rate": round(100.0 * n_anom / n_scored, 2) if n_scored else 0.0,
        "mean_score": round(mean_score, 3),
        "mae": round(mae, 4) if mae is not None else None,
        "_set": detector.anomaly_set(scored),
    }


def _compose_result(
    scored: pd.DataFrame,
    stats: dict,
    gap_rows: list[dict],
    baselines: list[dict],
    cfg: dict,
    log: list[str],
    status_notes: list[str],
    stale: bool,
    flatline: bool,
    artifacts: dict,
) -> dict:
    threshold = float(cfg["detection"]["threshold"])
    window = int(cfg["detection"]["window"])
    mask = scored["is_anomaly"].fillna(False)
    anomalies = [
        {
            "timestamp": ts.isoformat(),
            "value": round(float(row["value"]), 4),
            "score": round(float(row["score"]), 4),
            "severity": row["severity"],
        }
        for ts, row in scored[mask].iterrows()
    ]

    # серия для графика на фронтенде (с ограничением, чтобы не убить браузер)
    series_cap = 12_000
    s_df = scored.dropna(subset=["score"])
    if len(s_df) > series_cap:
        s_df = s_df.iloc[-series_cap:]
    series = [
        {
            "t": ts.isoformat(),
            "v": None if pd.isna(row["value"]) else round(float(row["value"]), 4),
            "s": None if pd.isna(row["score"]) else round(float(row["score"]), 4),
            "a": bool(row["is_anomaly"]) if not pd.isna(row["is_anomaly"]) else False,
            "sev": row["severity"],
        }
        for ts, row in s_df.iterrows()
    ]

    n_scored = int(scored["score"].notna().sum())
    return {
        "model_version": cfg["service"]["model_version"],
        "method": cfg["detection"]["method"],
        "window": window,
        "threshold": threshold,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": stats,
        "gaps": gap_rows[:50],
        "gaps_shown": min(len(gap_rows), 50),
        "gaps_total": len(gap_rows),
        "anomalies": anomalies,
        "anomalies_count": len(anomalies),
        "anomaly_rate": round(100.0 * len(anomalies) / n_scored, 2) if n_scored else 0.0,
        "scored_points": n_scored,
        "baselines": baselines,
        "status": {"stale": stale, "flatline": flatline, "notes": status_notes},
        "log": log,
        "series": series,
        "series_truncated": bool(len(scored) > series_cap),
        "artifacts": artifacts,
    }
