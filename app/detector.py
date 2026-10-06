"""Детекция аномалий: метод варианта 16 и наивные baseline'ы.

Метод варианта 16 — скользящий z-score:
    score(t) = | v(t) - mean(окно W до t) | / std(окно W до t)
    is_anomaly(t) = score(t) > threshold        (порог — из конфигурации)
    W = 24 (индивидуальное задание), первые W точек — «прогрев», не скорятся.

Baseline'ы для сравнения:
    1) наивный прогноз «вчера»: v_hat(t) = v(t - W) — аномалия = резкое
       отклонение факта от «вчерашнего» часа (нормировка на std ошибок);
    2) глобальный z-score: нормировка на mean/std всего ряда сразу —
       показывает, зачем вообще нужно скользящее окно (бонус).

Защита от вырожденных случаев:
    * std == 0 (константный ряд / «плато датчика») — score считается нулевым,
      тревоги не генерируются, снаружи выставляется флаг flatline;
    * NaN-значения (незакрытые длинные дыры) не скорятся.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FLAT_EPS = 1e-12


def severity_of(score: float, threshold: float, bands: dict) -> str:
    """Серьёзность тревоги: границы — кратные порогу (задаются в config.yaml)."""
    if score >= threshold * float(bands.get("high", 1.3)):
        return "high"
    if score >= threshold * float(bands.get("medium", 1.1)):
        return "medium"
    return "low"


def _empty_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {"value": index.to_series(index=index).map(lambda _: np.nan)},
        index=index,
    )


def rolling_zscore(
    grid: pd.DataFrame,
    window: int,
    threshold: float,
    bands: dict,
    progress=None,
) -> tuple[pd.DataFrame, bool]:
    """Метод варианта: скользящий z-score с окном `window`.

    Возвращает (scored_df, flatline_flag). scored_df содержит все точки ряда,
    но первые `window - 1` (прогрев) и NaN-точки имеют score = NaN.
    """
    values = grid["value"].astype(float)

    overall_std = float(np.nanstd(values.to_numpy(), ddof=1)) if values.notna().sum() > 1 else 0.0
    flatline = overall_std < FLAT_EPS

    roll_mean = values.rolling(window, min_periods=window).mean()
    roll_std = values.rolling(window, min_periods=window).std(ddof=1)

    score = (values - roll_mean).abs() / roll_std.replace(0.0, np.nan)

    scored = pd.DataFrame(index=grid.index)
    scored["value"] = values
    scored["score"] = score

    # защиты: константное окно или NaN-значение -> score = NaN (не тревога)
    scored.loc[roll_std.isna() | (roll_std < FLAT_EPS), "score"] = np.nan
    scored.loc[values.isna(), "score"] = np.nan
    if flatline:
        scored["score"] = np.nan

    scored["is_anomaly"] = scored["score"] > threshold
    scored["severity"] = [
        severity_of(s, threshold, bands) if a else ""
        for s, a in zip(scored["score"].fillna(0.0), scored["is_anomaly"])
    ]
    if progress:
        progress(70, f"Z-score: проскорено {int(scored['score'].notna().sum()):,} точек")
    return scored, flatline


def baseline_yesterday(grid: pd.DataFrame, window: int, threshold: float, bands: dict) -> pd.DataFrame:
    """Наивный baseline «вчера»: v_hat(t) = v(t - window).

    score = |v(t) - v(t-window)| / std(таких ошибок). Прогрев — window точек.
    """
    values = grid["value"].astype(float)
    forecast = values.shift(window)
    err = (values - forecast).abs()
    scale = err.dropna().std(ddof=1)

    if scale is not None and not pd.isna(scale) and scale > FLAT_EPS:
        score = err / scale
    else:
        score = err * np.nan  # вырожденный случай: ошибок-эталона нет

    scored = pd.DataFrame(index=grid.index)
    scored["value"] = values
    scored["score"] = score
    scored.loc[values.isna() | forecast.isna(), "score"] = np.nan
    scored["is_anomaly"] = scored["score"] > threshold
    scored["severity"] = [
        severity_of(s, threshold, bands) if a else ""
        for s, a in zip(scored["score"].fillna(0.0), scored["is_anomaly"])
    ]
    return scored


def baseline_global_zscore(grid: pd.DataFrame, threshold: float, bands: dict) -> pd.DataFrame:
    """Baseline «глобальный z-score»: нормировка на mean/std всего ряда."""
    values = grid["value"].astype(float)
    mu = values.mean()
    sigma = values.std(ddof=1)

    if pd.isna(sigma) or sigma <= FLAT_EPS:
        score = values * np.nan
    else:
        score = (values - mu).abs() / sigma

    scored = pd.DataFrame(index=grid.index)
    scored["value"] = values
    scored["score"] = score
    scored.loc[values.isna(), "score"] = np.nan
    scored["is_anomaly"] = scored["score"] > threshold
    scored["severity"] = [
        severity_of(s, threshold, bands) if a else ""
        for s, a in zip(scored["score"].fillna(0.0), scored["is_anomaly"])
    ]
    return scored


def anomaly_set(scored: pd.DataFrame) -> set[pd.Timestamp]:
    """Множество timestamp тревог (для сравнения перекрытия методов)."""
    return set(scored.index[scored["is_anomaly"].fillna(False)])


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def mae_against(values: pd.Series, reference: pd.Series) -> float | None:
    """Средняя абсолютная ошибка факта против опорного прогноза."""
    diff = (values - reference).abs().dropna()
    return float(diff.mean()) if len(diff) else None
