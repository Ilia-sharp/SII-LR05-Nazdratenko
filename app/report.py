"""Сохранение результатов конвейера: anomalies.csv, anomalies.json, anomalies.png, report.html.

Формат JSON соответствует минимальному формату результата из задания:
{"model_version": "...", "threshold": ..., "items": [{timestamp, value, score, severity, is_anomaly}]}
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # без GUI — важно для сервера Render Free
import matplotlib.pyplot as plt
import pandas as pd

INK = "#1E293B"
BLUE = "#2563EB"
RED = "#DC2626"
AMBER = "#D97706"
GREEN = "#059669"
GRID = "#F1F5F9"


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def write_anomalies_csv(scored: pd.DataFrame, path: str | Path) -> None:
    out = scored.reset_index().rename(columns={"index": "timestamp"})
    out["timestamp"] = out["timestamp"].apply(lambda x: x.isoformat())
    out["is_anomaly"] = out["is_anomaly"].map(lambda x: bool(x) if pd.notna(x) else False)
    out.to_csv(path, index=False, float_format="%.4f")


# ---------------------------------------------------------------------------
# JSON (минимальный формат результата из задания)
# ---------------------------------------------------------------------------

def write_anomalies_json(result: dict, path: str | Path) -> None:
    payload = {
        "model_version": result["model_version"],
        "method": result["method"],
        "window": result["window"],
        "threshold": result["threshold"],
        "generated_at": result["generated_at"],
        "stats": result["stats"],
        "status": result["status"],
        "items": [
            {
                "timestamp": a["timestamp"],
                "value": a["value"],
                "score": a["score"],
                "is_anomaly": True,
                "severity": a["severity"],
            }
            for a in result["anomalies"]
        ],
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# PNG-график (matplotlib, два полотна: ряд + score с порогом)
# ---------------------------------------------------------------------------

def write_anomalies_png(scored: pd.DataFrame, cfg: dict, path: str | Path) -> None:
    df = scored.reset_index()
    ts = df["timestamp"]
    value = df["value"]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 6.5), sharex=True, constrained_layout=True, dpi=150
    )
    fig.patch.set_facecolor("white")

    ax1.plot(ts, value, color=BLUE, linewidth=1.0, label="Потребление, кВт")
    anom = df[df["is_anomaly"] == True]  # noqa: E712
    sev_colors = {"high": RED, "medium": AMBER, "low": GREEN}
    for sev, color in sev_colors.items():
        part = anom[anom["severity"] == sev]
        if len(part):
            ax1.scatter(
                part["timestamp"], part["value"], s=26, color=color, zorder=5,
                label=f"Аномалии · {sev} ({len(part)})", edgecolors="white", linewidths=0.5,
            )
    ax1.set_ylabel("кВт", color=INK)
    ax1.set_title(
        f"Обнаружение аномалий · z-score, окно {cfg['detection']['window']}, "
        f"порог {cfg['detection']['threshold']}σ · {cfg['service']['model_version']}",
        color=INK, fontsize=11, loc="left",
    )
    ax1.grid(True, color=GRID, linewidth=0.8)
    ax1.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False)
    for spine in ("top", "right"):
        ax1.spines[spine].set_visible(False)
        ax2.spines[spine].set_visible(False)

    ax2.plot(ts, df["score"], color=INK, linewidth=0.8, alpha=0.75, label="score (z)")
    ax2.axhline(cfg["detection"]["threshold"], color=AMBER, linestyle="--", linewidth=1.2,
                label=f"порог = {cfg['detection']['threshold']}σ")
    ax2.set_ylabel("score", color=INK)
    ax2.set_xlabel("Время", color=INK)
    ax2.grid(True, color=GRID, linewidth=0.8)
    ax2.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False)

    fig.savefig(path, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# HTML-отчёт (самодостаточный, на русском, простой плоский стиль)
# ---------------------------------------------------------------------------

_HTML_TMPL = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Отчёт · обнаружение аномалий</title>
<style>
:root {{ --ink:#1E293B; --muted:#64748B; --line:#E2E8F0; --blue:#2563EB; --red:#DC2626;
        --amber:#D97706; --green:#059669; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:system-ui,'Segoe UI',Roboto,Arial,sans-serif; color:var(--ink);
       background:#fff; padding:32px 16px; }}
.wrap {{ max-width:960px; margin:0 auto; display:grid; gap:20px; }}
.card {{ background:#fff; border:1px solid var(--line); border-radius:10px;
        padding:28px 32px; }}
h1 {{ font-size:22px; margin:0 0 4px; }}
h2 {{ font-size:16px; margin:0 0 14px; }}
.sub {{ color:var(--muted); font-size:13px; margin-bottom:18px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; }}
.kpi {{ background:#F8FAFC; border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
.kpi b {{ display:block; font-size:22px; }}
.kpi span {{ color:var(--muted); font-size:12px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:left; color:var(--muted); font-weight:600; padding:8px 10px; border-bottom:1px solid var(--line); }}
td {{ padding:8px 10px; border-bottom:1px solid var(--line); }}
.badge {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; }}
.b-high {{ background:#FEF2F2; color:var(--red); }}
.b-medium {{ background:#FFFBEB; color:var(--amber); }}
.b-low {{ background:#ECFDF5; color:var(--green); }}
.note {{ background:#EFF4FF; border:1px solid #DBE5FE; border-radius:8px; padding:12px 16px;
        font-size:13px; color:#1E40AF; }}
.foot {{ color:var(--muted); font-size:12px; text-align:center; }}
.grad {{ color:var(--blue); }}
</style></head><body><div class="wrap">

<div class="card">
  <h1>Отчёт: обнаружение аномалий в энергопотреблении</h1>
  <div class="sub">Лабораторная работа №5 · вариант 16 · метод z-score · окно {window} ·
  <b>{model_version}</b> · сформирован {generated_at}</div>
  <div class="kpis">
    <div class="kpi"><b>{points_total}</b><span>точек в ряду</span></div>
    <div class="kpi"><b>{scored_points}</b><span>проскорено</span></div>
    <div class="kpi"><b>{anomalies_count}</b><span>аномалий ({anomaly_rate}%)</span></div>
    <div class="kpi"><b>{threshold}σ</b><span>порог тревоги</span></div>
    <div class="kpi"><b>{gaps_total}</b><span>дыр в данных</span></div>
  </div>
</div>

<div class="card">
  <h2>Статус сервиса и данных</h2>
  {status_block}
</div>

<div class="card">
  <h2>Пропуски в данных (явная обработка)</h2>
  {gaps_block}
</div>

<div class="card">
  <h2>Сравнение с baseline</h2>
  <table><thead><tr><th>Метод</th><th>Проскорено</th><th>Аномалий</th><th>Доля, %</th>
  <th>Средний score</th><th>MAE прогноза</th><th>Пересечение с методом варианта (Жаккар)</th></tr></thead>
  <tbody>{baselines_rows}</tbody></table>
</div>

<div class="card">
  <h2>Топ-{top_n} тревог</h2>
  <table><thead><tr><th>timestamp</th><th>Значение, кВт</th><th>score</th><th>Серьёзность</th></tr></thead>
  <tbody>{anomalies_rows}</tbody></table>
</div>

<div class="foot">Файлы прогона: anomalies.csv · anomalies.json · anomalies.png · report.html
<br>Nazdratenko · Системы искусственного интеллекта · ЛР №5 · вариант 16</div>
</div></body></html>"""


def write_report_html(result: dict, path: str | Path) -> None:
    st = result["stats"]

    status_block = ""
    notes = result["status"].get("notes") or []
    if notes:
        status_block = "".join(f'<div class="note" style="margin-bottom:8px">{n}</div>' for n in notes)
    else:
        status_block = (
            f'<div class="note">Данные в порядке: {st["points_total"]:,} точек '
            f'({st["first_ts"]} … {st["last_ts"]}), шаг ряда {st["step"]}. '
            "Длинные дыры не интерполируются «молча» — см. таблицу пропусков.</div>"
        )

    if result["gaps_total"] == 0:
        gaps_block = '<div class="note">Пропусков в данных не обнаружено — ряд уже регулярный.</div>'
    else:
        rows = "".join(
            f"<tr><td>{g['start']}</td><td>{g['end']}</td><td>{g['points']}</td>"
            f"<td>{'интерполировано' if g['action'] == 'interpolated' else 'оставлено пустым (NaN)'}</td></tr>"
            for g in result["gaps"]
        )
        gaps_block = (
            f"<table><thead><tr><th>Начало</th><th>Конец</th><th>Точек</th><th>Действие</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

    baselines_rows = "".join(
        f"<tr><td>{b['name']}</td><td>{b['scored']:,}</td><td>{b['anomalies']}</td>"
        f"<td>{b['anomaly_rate']}</td><td>{b['mean_score']}</td>"
        f"<td>{b['mae'] if b['mae'] is not None else '—'}</td><td>{b['jaccard_with_variant']}</td></tr>"
        for b in result["baselines"]
    )

    top = result["anomalies"][:20]
    anomalies_rows = "".join(
        f"<tr><td>{a['timestamp']}</td><td>{a['value']}</td><td>{a['score']}</td>"
        f'<td><span class="badge b-{a["severity"]}">{a["severity"]}</span></td></tr>'
        for a in top
    )

    html = _HTML_TMPL.format(
        window=result["window"],
        model_version=result["model_version"],
        generated_at=result["generated_at"],
        points_total=f"{st['points_total']:,}",
        scored_points=f"{result['scored_points']:,}",
        anomalies_count=result["anomalies_count"],
        anomaly_rate=result["anomaly_rate"],
        threshold=result["threshold"],
        gaps_total=result["gaps_total"],
        status_block=status_block,
        gaps_block=gaps_block,
        baselines_rows=baselines_rows,
        top_n=len(top),
        anomalies_rows=anomalies_rows,
    )
    Path(path).write_text(html, encoding="utf-8")
