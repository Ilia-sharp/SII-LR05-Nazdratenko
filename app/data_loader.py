"""Загрузка временного ряда и ЯВНАЯ обработка пропусков.

Правила работы с пропусками (настраиваются в config.yaml -> data.max_interp_gap):

* ряд приводится к регулярной сетке по медианному шагу между точками;
* короткие дыры (<= max_interp_gap точек подряд) закрываются линейной
  интерполяцией — и каждая из них попадает в gap-отчёт;
* длинные дыры (> max_interp_gap) НЕ интерполируются «молча»: в значении
  остаётся NaN, такие точки исключаются из скоринга, а диапазон помечается
  в gap-отчёте действием "left_blank";
* дубликаты timestamp агрегируются средним значением.

Модуль принимает как CSV-текст (файл из формы на сайте), так и путь к файлу.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

TS_CANDIDATES = ["timestamp", "datetime", "date", "time", "ds", "дата", "время", "датавремя"]
VAL_CANDIDATES = ["value", "values", "load", "power", "consumption", "y", "значение", "потребление", "энергия", "нагрузка"]

# форматы времени: ISO («2023-01-05 12:00») и точечный русский («05.01.2023 12:00»)
_RE_DOT = re.compile(r"^\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\s*$")
_RE_ISO = re.compile(r"^\s*\d{4}-\d{1,2}-\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?\s*$")


def parse_timestamps(series: pd.Series) -> pd.Series:
    """Разобрать timestamp, не ломая даты.

    ВАЖНО: pd.to_datetime(..., dayfirst=True) искажает ISO-даты вида
    «2008-11-01» (тихо меняет местами месяц и день, если день <= 12).
    Поэтому формат определяем заранее:
      * ISO-строки        -> без dayfirst;
      * «дд.мм.гггг»      -> dayfirst=True (русский формат);
      * смешанные/другие  -> берём вариант с меньшим числом NaT.
    """
    s = series.astype("string")
    n = max(len(s), 1)
    n_iso = int(s.str.match(_RE_ISO).fillna(False).sum())
    n_dot = int(s.str.match(_RE_DOT).fillna(False).sum())

    if n_iso >= n_dot and n_iso > 0:
        ts = pd.to_datetime(s, errors="coerce")
        if ts.notna().mean() >= 0.95:
            return ts
        ts_alt = pd.to_datetime(s, dayfirst=True, errors="coerce")
        return ts_alt if ts_alt.notna().mean() > ts.notna().mean() else ts
    if n_dot > 0:
        return pd.to_datetime(s, dayfirst=True, errors="coerce")

    ts = pd.to_datetime(s, errors="coerce")
    ts_alt = pd.to_datetime(s, dayfirst=True, errors="coerce")
    return ts_alt if ts_alt.notna().mean() > ts.notna().mean() else ts


class DataLoadError(ValueError):
    """Понятная ошибка разбора входного CSV (показывается пользователю)."""


# ---------------------------------------------------------------------------
# Чтение CSV
# ---------------------------------------------------------------------------

def _sniff_delimiter(sample: str) -> str:
    import csv as _csv

    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except _csv.Error:
        return ","


def _pick_column(cols: list[str], candidates: list[str]) -> str | None:
    lowered = {c.strip().lower(): c for c in cols}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    return None


def parse_csv_bytes(raw: bytes) -> pd.DataFrame:
    """Разобрать CSV (timestamp,value) в DataFrame с колонками timestamp/value."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1251")  # частая кодировка русских CSV
        except UnicodeDecodeError as e:
            raise DataLoadError("Не удалось определить кодировку файла (нужен UTF-8 или cp1251).") from e

    if not text.strip():
        raise DataLoadError("Файл пустой.")

    first_line = text.splitlines()[0]
    delimiter = _sniff_delimiter(first_line + "\n" + (text.splitlines()[1] if len(text.splitlines()) > 1 else ""))

    df = pd.read_csv(io.StringIO(text), sep=delimiter, engine="python")
    if df.shape[1] < 2:
        raise DataLoadError("В CSV должно быть минимум 2 колонки: timestamp и value.")

    ts_col = _pick_column(list(df.columns), TS_CANDIDATES)
    val_col = _pick_column(list(df.columns), VAL_CANDIDATES)
    if ts_col is None or val_col is None:
        raise DataLoadError(
            "Не нашёл колонки timestamp/value. "
            f"Ожидал колонку времени ({', '.join(TS_CANDIDATES[:5])}...) и колонку значения "
            f"({', '.join(VAL_CANDIDATES[:5])}...). Найдены: {list(df.columns)}"
        )

    out = pd.DataFrame()
    out["timestamp"] = parse_timestamps(df[ts_col])
    out["value"] = pd.to_numeric(df[val_col], errors="coerce")

    bad_ts = int(out["timestamp"].isna().sum())
    if bad_ts > len(out) * 0.5:
        raise DataLoadError(
            f"Более половины значений в колонке «{ts_col}» не распознаны как дата/время "
            f"(не распознано {bad_ts} из {len(out)}). Пример формата: 2023-01-05T12:00:00 или 05.01.2023 12:00."
        )
    out = out.dropna(subset=["timestamp"])

    if out.empty:
        raise DataLoadError("После разбора не осталось ни одной валидной точки.")
    if out["value"].notna().sum() < 10:
        raise DataLoadError("Слишком мало числовых значений в колонке «value» — проверьте файл.")

    out = out.sort_values("timestamp").reset_index(drop=True)
    # дубликаты времени — средним
    out = out.groupby("timestamp", as_index=False)["value"].mean()
    return out


def load_csv_path(path: str | Path) -> pd.DataFrame:
    return parse_csv_bytes(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Регулярная сетка, пропуски и политика их обработки
# ---------------------------------------------------------------------------

def build_regular_grid(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timedelta]:
    """Привести ряд к регулярной сетке с шагом = медианный интервал."""
    df = df.sort_values("timestamp").reset_index(drop=True)
    # дубликаты времени агрегируем средним (иначе reindex невозможен)
    if df["timestamp"].duplicated().any():
        df = df.groupby("timestamp", as_index=False)["value"].mean()
    deltas = df["timestamp"].diff().dropna()
    if deltas.empty:
        step = pd.Timedelta(hours=1)
    else:
        step = deltas.median()
        if step <= pd.Timedelta(0):
            step = pd.Timedelta(hours=1)
    full_range = pd.date_range(df["timestamp"].min(), df["timestamp"].max(), freq=step)
    grid = df.set_index("timestamp").reindex(full_range)
    grid.index.name = "timestamp"
    return grid, step


def detect_gap_runs(values: pd.Series) -> list[dict]:
    """Найти непрерывные участки NaN на регулярной сетке."""
    isna = values.isna().to_numpy()
    runs: list[dict] = []
    start = None
    for i, flag in enumerate(isna):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(isna) - 1))

    index = values.index
    out = []
    for s, e in runs:
        out.append(
            {
                "start": index[s].isoformat(),
                "end": index[e].isoformat(),
                "points": e - s + 1,
            }
        )
    return out


def apply_gap_policy(grid: pd.DataFrame, max_interp_gap: int) -> tuple[pd.DataFrame, list[dict]]:
    """Явная политика пропусков: короткие дыры — интерполяция, длинные — NaN.

    Возвращает (grid с колонкой value, список gap-записей с полями
    start/end/points/action).
    """
    gaps = detect_gap_runs(grid["value"])
    value = grid["value"].copy()
    gap_rows: list[dict] = []
    n = len(value)

    for gap in gaps:
        # позиция участка в ряду (нужно, чтобы понять, не на краю ли дыра)
        s_pos = value.index.get_loc(pd.Timestamp(gap["start"]))
        e_pos = value.index.get_loc(pd.Timestamp(gap["end"]))
        at_edge = s_pos == 0 or e_pos == n - 1  # у краевой дыры нет соседа для интерполяции
        action = (
            "interpolated" if (gap["points"] <= max_interp_gap and not at_edge) else "left_blank"
        )
        gap_rows.append({**gap, "action": action})
        if action == "interpolated":
            # линейная интерполяция по соседним ЗНАЕМЫМ точкам:
            # левая граница s_pos-1, правая e_pos+1 (обе существуют — not at_edge)
            xs = [s_pos - 1, e_pos + 1]
            ys = [float(value.iloc[s_pos - 1]), float(value.iloc[e_pos + 1])]
            for pos in range(s_pos, e_pos + 1):
                value.iloc[pos] = float(np.interp(pos, xs, ys))

    grid = grid.copy()
    grid["value"] = value
    return grid, gap_rows


def describe_series(grid: pd.DataFrame, gap_rows: list[dict], step: pd.Timedelta) -> dict:
    vals = grid["value"]
    interp = sum(1 for g in gap_rows if g["action"] == "interpolated")
    blank = sum(1 for g in gap_rows if g["action"] == "left_blank")
    return {
        "points_total": int(len(grid)),
        "points_missing": int(vals.isna().sum()),
        "gaps_total": len(gap_rows),
        "gaps_interpolated": interp,
        "gaps_left_blank": blank,
        "first_ts": grid.index.min().isoformat() if len(grid) else None,
        "last_ts": grid.index.max().isoformat() if len(grid) else None,
        "step": str(step),
        "mean": float(np.nanmean(vals)) if vals.notna().any() else None,
        "std": float(np.nanstd(vals, ddof=1)) if vals.notna().sum() > 1 else None,
        "min": float(np.nanmin(vals)) if vals.notna().any() else None,
        "max": float(np.nanmax(vals)) if vals.notna().any() else None,
    }
