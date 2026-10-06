"""Тесты конвейера обнаружения аномалий (ЛР №5, вариант 16).

Покрывают ключевые требования задания:
* явная обработка пропусков (короткие интерполируются, длинные — нет);
* z-score детекция с окном (вариант 16);
* поведение на константном ряде (flatline-защита);
* порог берётся из конфигурации/окружения, а не из кода;
* JSON содержит model_version/threshold/items с нужными полями;
* baseline «вчера» работает на игрушечном примере.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app import data_loader, detector, pipeline
from app.config import load_config, validate_override


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def make_df(values, start="2023-01-01", freq="h"):
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.DataFrame({"timestamp": idx, "value": np.asarray(values, dtype=float)})


def grid_of(df):
    return data_loader.build_regular_grid(df)[0]


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# ---------------------------------------------------------------------------
# Пропуски: явная политика
# ---------------------------------------------------------------------------

def test_short_gap_interpolated():
    # дыра в 2 точки (<= max_interp_gap) обязана закрыться интерполяцией
    idx = pd.date_range("2023-01-01", periods=12, freq="h").delete([4, 5])
    df = pd.DataFrame({"timestamp": idx, "value": np.arange(10, dtype=float)})
    grid, _ = data_loader.build_regular_grid(df)
    filled, gaps = data_loader.apply_gap_policy(grid, max_interp_gap=3)

    g = next(x for x in gaps if x["points"] == 2)
    assert g["action"] == "interpolated"
    # между 3 (03:00) и 4 (06:00): линейно
    assert filled.loc[pd.Timestamp("2023-01-01 04:00"), "value"] == pytest.approx(10 / 3, rel=1e-6)
    assert filled.loc[pd.Timestamp("2023-01-01 05:00"), "value"] == pytest.approx(11 / 3, rel=1e-6)


def test_long_gap_not_silently_interpolated():
    # дыра в 5 точек (> max_interp_gap) обязана остаться NaN — «молчаливой
    # интерполяции длинных дыр нет» (требование задания)
    idx = pd.date_range("2023-01-01", periods=20, freq="h").delete([4, 5, 6, 7, 8])
    df = pd.DataFrame({"timestamp": idx, "value": np.arange(15, dtype=float)})
    grid, _ = data_loader.build_regular_grid(df)
    filled, gaps = data_loader.apply_gap_policy(grid, max_interp_gap=3)

    g = next(x for x in gaps if x["points"] == 5)
    assert g["action"] == "left_blank"
    assert all(pd.isna(filled.loc[t, "value"]) for t in pd.date_range("2023-01-01 04:00", periods=5, freq="h"))


def test_edge_gap_left_blank():
    # NaN в ПЕРВОЙ точке ряда не может быть проинтерполирован (нет левого соседа) —
    # честно остаётся NaN и попадает в отчёт как left_blank
    df = make_df([np.nan, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    grid, _ = data_loader.build_regular_grid(df)
    filled, gaps = data_loader.apply_gap_policy(grid, max_interp_gap=3)
    assert gaps[0]["action"] == "left_blank"
    assert pd.isna(filled["value"].iloc[0])


def test_duplicate_timestamps_averaged():
    df = make_df([1.0, 3.0, 5.0])
    extra = pd.DataFrame({"timestamp": [df.loc[0, "timestamp"]], "value": [5.0]})
    df = pd.concat([df, extra], ignore_index=True)
    grid, _ = data_loader.build_regular_grid(df)
    assert grid.loc[df.loc[0, "timestamp"], "value"] == pytest.approx(3.0)  # (1+5)/2


# ---------------------------------------------------------------------------
# Z-score (метод варианта 16)
# ---------------------------------------------------------------------------

def test_zscore_detects_spike(cfg):
    rs = np.random.RandomState(42)
    values = 5.0 + rs.normal(0, 0.1, 60)
    values[40] = 30.0  # явный выброс
    grid = grid_of(make_df(values))

    scored, flatline = detector.rolling_zscore(grid, window=24, threshold=3.0, bands=cfg["detection"]["severity"])
    assert not flatline
    ts = pd.Timestamp("2023-01-01 40:00:00") if False else grid.index[40]
    assert bool(scored.loc[ts, "is_anomaly"]) is True
    assert scored.loc[ts, "score"] > 3.0
    # до окна прогрева тревог быть не должно
    assert scored["score"].iloc[:23].isna().all()


def test_flat_series_no_anomalies(cfg):
    # константный ряд: std = 0 -> flatline, тревог нет (защита от «плато датчика»)
    grid = grid_of(make_df(np.full(60, 7.0)))
    scored, flatline = detector.rolling_zscore(grid, window=24, threshold=3.0, bands=cfg["detection"]["severity"])
    assert flatline is True
    assert scored["is_anomaly"].fillna(False).sum() == 0


def test_severity_bands():
    bands = {"medium": 1.1, "high": 1.3}
    assert detector.severity_of(3.1, 3.0, bands) == "low"
    assert detector.severity_of(3.4, 3.0, bands) == "medium"
    assert detector.severity_of(4.0, 3.0, bands) == "high"


# ---------------------------------------------------------------------------
# Baseline «вчера»
# ---------------------------------------------------------------------------

def test_yesterday_baseline_toy(cfg):
    rs = np.random.RandomState(7)
    values = 5.0 + rs.normal(0, 0.1, 60)
    values[40] = 25.0  # резкое отклонение от «вчерашнего» часа (t-24)
    grid = grid_of(make_df(values))

    base = detector.baseline_yesterday(grid, window=24, threshold=3.0, bands=cfg["detection"]["severity"])
    assert bool(base.loc[grid.index[40], "is_anomaly"]) is True


# ---------------------------------------------------------------------------
# Конфигурация: порог не захардкожен
# ---------------------------------------------------------------------------

def test_threshold_from_env(monkeypatch):
    monkeypatch.setenv("ANOMALY_THRESHOLD", "5.5")
    cfg = load_config()
    assert cfg["detection"]["threshold"] == 5.5


def test_window_from_env(monkeypatch):
    monkeypatch.setenv("ANOMALY_WINDOW", "12")
    cfg = load_config()
    assert cfg["detection"]["window"] == 12


def test_validate_override_bounds():
    assert validate_override(3.5, 24) == (3.5, 24)
    with pytest.raises(ValueError):
        validate_override(50.0, None)
    with pytest.raises(ValueError):
        validate_override(None, 1000)


# ---------------------------------------------------------------------------
# Конвейер целиком + формат JSON
# ---------------------------------------------------------------------------

def test_pipeline_result_and_json_schema(cfg, tmp_path):
    rs = np.random.RandomState(3)
    values = 5.0 + rs.normal(0, 0.3, 120)
    values[80] = 40.0
    df = make_df(values)

    result = pipeline.run_pipeline(df, cfg, pace=0.0, artifacts_dir=tmp_path)

    # структура результата
    assert result["model_version"] == cfg["service"]["model_version"]
    assert result["threshold"] == cfg["detection"]["threshold"]
    assert result["window"] == cfg["detection"]["window"]
    assert result["anomalies_count"] >= 1
    assert len(result["baselines"]) == 3  # вариант + «вчера» + глобальный z-score
    assert result["baselines"][0]["jaccard_with_variant"] == 1.0

    # артефакты созданы
    for name in ("anomalies.csv", "anomalies.json", "anomalies.png", "report.html"):
        assert (tmp_path / name).exists(), f"нет артефакта {name}"

    # JSON: минимальный формат результата из задания
    payload = json.loads((tmp_path / "anomalies.json").read_text(encoding="utf-8"))
    assert "model_version" in payload and "threshold" in payload and "items" in payload
    item = payload["items"][0]
    for key in ("timestamp", "value", "score", "is_anomaly", "severity"):
        assert key in item


def test_pipeline_handles_gaps_end_to_end(cfg, tmp_path):
    # длинная дыра не должна ломать конвейер и не должна «залиться» интерполяцией
    idx = pd.date_range("2023-01-01", periods=120, freq="h").delete(range(50, 60))
    values = 5.0 + np.random.RandomState(5).normal(0, 0.3, 110)
    df = pd.DataFrame({"timestamp": idx, "value": values})

    result = pipeline.run_pipeline(df, cfg, pace=0.0, artifacts_dir=tmp_path)
    assert result["stats"]["gaps_total"] >= 1
    assert any(g["action"] == "left_blank" for g in result["gaps"])
    assert result["scored_points"] < result["stats"]["points_total"]
