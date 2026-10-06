"""Загрузка конфигурации: YAML + переменные окружения.

Приоритет: переменные окружения > config.yaml > встроенные дефолты.
Пороговые значения детекции никогда не захардкожены в коде — только здесь.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"

# Допустимые диапазоны пользовательских переопределений (защита от бреда)
THRESHOLD_MIN, THRESHOLD_MAX = 1.0, 10.0
WINDOW_MIN, WINDOW_MAX = 6, 168


def load_config(path: str | None = None) -> dict:
    """Прочитать config.yaml и применить переопределения из окружения."""
    cfg_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg.setdefault("service", {})
    cfg.setdefault("data", {})
    cfg.setdefault("detection", {})
    cfg.setdefault("baseline", {})
    cfg.setdefault("output", {})
    cfg.setdefault("server", {})

    # --- окружение важнее YAML -------------------------------------------
    if os.getenv("ANOMALY_THRESHOLD"):
        cfg["detection"]["threshold"] = float(os.environ["ANOMALY_THRESHOLD"])
    if os.getenv("ANOMALY_WINDOW"):
        cfg["detection"]["window"] = int(os.environ["ANOMALY_WINDOW"])
    if os.getenv("MODEL_VERSION"):
        cfg["service"]["model_version"] = os.environ["MODEL_VERSION"]
    if os.getenv("PIPELINE_PACE"):
        cfg.setdefault("_pace", float(os.environ["PIPELINE_PACE"]))

    # --- гарантируем наличие ключей ---------------------------------------
    cfg["detection"].setdefault("method", "zscore")
    cfg["detection"].setdefault("window", 24)
    cfg["detection"].setdefault("threshold", 3.0)
    cfg["detection"].setdefault("severity", {"medium": 1.1, "high": 1.3})
    cfg["data"].setdefault("sample_path", "data/energy_hourly.csv")
    cfg["data"].setdefault("max_interp_gap", 3)
    cfg["data"].setdefault("monitor_freshness", False)
    cfg["data"].setdefault("stale_after_hours", 48)
    cfg["service"].setdefault("model_version", "anomaly-v1")
    cfg["service"].setdefault("title", "Nazdratenko · Anomaly Detection")
    cfg["output"].setdefault("dir", "outputs")
    cfg["output"].setdefault("runs_dir", "outputs/runs")
    cfg["server"].setdefault("max_upload_mb", 15)

    cfg["_path"] = str(cfg_path)
    return cfg


def validate_override(threshold: float | None, window: int | None) -> tuple[float | None, int | None]:
    """Проверить пользовательские переопределения порога и окна."""
    if threshold is not None:
        t = float(threshold)
        if not (THRESHOLD_MIN <= t <= THRESHOLD_MAX):
            raise ValueError(
                f"Порог должен быть в диапазоне {THRESHOLD_MIN}–{THRESHOLD_MAX}, получено {t}"
            )
        threshold = t
    if window is not None:
        w = int(window)
        if not (WINDOW_MIN <= w <= WINDOW_MAX):
            raise ValueError(
                f"Окно должно быть в диапазоне {WINDOW_MIN}–{WINDOW_MAX} точек, получено {w}"
            )
        window = w
    return threshold, window


def config_for_run(cfg: dict, threshold: float | None = None, window: int | None = None) -> dict:
    """Копия конфига с пользовательскими переопределениями (для одного прогона)."""
    run_cfg = copy.deepcopy(cfg)
    threshold, window = validate_override(threshold, window)
    if threshold is not None:
        run_cfg["detection"]["threshold"] = threshold
    if window is not None:
        run_cfg["detection"]["window"] = window
    return run_cfg
