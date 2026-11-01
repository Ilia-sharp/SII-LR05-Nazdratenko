"""FastAPI-сервис обнаружения аномалий — Nazdratenko, ЛР №5, вариант 16.

Эндпоинты:
    GET  /                          — одностраничный интерфейс (static/index.html)
    GET  /api/health                — живость сервиса + модель + текущая конфигурация
    GET  /api/config                — публичная конфигурация (порог/окно/серьёзность)
    POST /api/run                   — запустить расчёт (demo-режим или загрузка CSV)
    GET  /api/jobs/{job_id}         — статус и прогресс расчёта (опрашивается сайтом)
    GET  /api/results/{job_id}/{f}  — артефакты: anomalies.csv|json|png, report.html

Модель ответа всегда содержит model_version (требование задания).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import data_loader, pipeline, progress as jobs
from .config import BASE_DIR, config_for_run, load_config

START_TIME = time.time()
CFG = load_config()
STAGES = pipeline.STAGES

app = FastAPI(
    title=CFG["service"]["title"],
    version=CFG["service"]["model_version"],
    description="Сервис обнаружения аномалий во временном ряде энергопотребления (z-score, окно 24)",
)

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

ARTIFACT_NAMES = {"anomalies.csv", "anomalies.json", "anomalies.png", "report.html"}


def _demo_path() -> Path:
    return BASE_DIR / CFG["data"]["sample_path"]


def _freshness(path: Path) -> dict:
    """Свежесть ряда: возраст последней точки (для живых потоков).

    Флаг stale имеет смысл только при data.monitor_freshness = true —
    для исторического демо-ряда он выключен и остаётся None.
    """
    info = {
        "monitor_freshness": bool(CFG["data"].get("monitor_freshness")),
        "stale_after_hours": float(CFG["data"].get("stale_after_hours", 48)),
        "age_hours": None,
        "stale": None,
    }
    if not path.exists():
        return info
    try:
        last = pd.Timestamp(pd.read_csv(path)["timestamp"].iloc[-1])
        age = (pd.Timestamp.now().tz_localize(None) - last).total_seconds() / 3600
        info["age_hours"] = round(age, 1)
        if info["monitor_freshness"]:
            info["stale"] = bool(age > info["stale_after_hours"])
    except Exception:  # pragma: no cover - демо-файл всегда валиден
        pass
    return info


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    demo = _demo_path()
    demo_info: dict = {"available": demo.exists()}
    if demo.exists():
        try:
            head = pd.read_csv(demo)
            demo_info["points"] = int(len(head))
            demo_info["first"] = str(head["timestamp"].iloc[0]) if len(head) else None
            demo_info["last"] = str(head["timestamp"].iloc[-1]) if len(head) else None
        except Exception:  # pragma: no cover - демо-файл всегда валиден
            demo_info["points"] = None
    return {
        "status": "ok",
        "model_version": CFG["service"]["model_version"],
        "method": CFG["detection"]["method"],
        "threshold": CFG["detection"]["threshold"],
        "window": CFG["detection"]["window"],
        "uptime_s": round(time.time() - START_TIME, 1),
        "demo_data": demo_info,
        "freshness": _freshness(demo),
    }


@app.get("/api/config")
def get_config() -> dict:
    return {
        "model_version": CFG["service"]["model_version"],
        "method": CFG["detection"]["method"],
        "window": CFG["detection"]["window"],
        "threshold": CFG["detection"]["threshold"],
        "threshold_min": 1.0,
        "threshold_max": 10.0,
        "severity": CFG["detection"]["severity"],
        "max_upload_mb": CFG["server"]["max_upload_mb"],
        "max_interp_gap": CFG["data"]["max_interp_gap"],
    }


@app.post("/api/run")
async def run_analysis(
    mode: str = Form("demo"),
    file: UploadFile | None = File(None),
    threshold: float | None = Form(None),
    window: int | None = Form(None),
) -> JSONResponse:
    """Запустить расчёт. mode=demo — встроенный ряд; mode=file — CSV пользователя."""
    mode = (mode or "demo").strip().lower()
    if mode not in {"demo", "file"}:
        raise HTTPException(status_code=422, detail="mode должен быть 'demo' или 'file'")
    if mode == "file" and (file is None or not file.filename):
        raise HTTPException(status_code=422, detail="Не выбран файл CSV для режима mode=file")

    # проверка порога/окна до старта, чтобы сразу вернуть ошибку пользователю
    try:
        config_for_run(CFG, threshold, window)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    raw_bytes: bytes | None = None
    filename = None
    if mode == "file":
        max_mb = int(CFG["server"].get("max_upload_mb", 15))
        raw_bytes = await file.read()
        if len(raw_bytes) > max_mb * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"Файл больше {max_mb} МБ. Для Free-тарифа Render загрузите срез данных.",
            )
        filename = file.filename

    if not jobs.try_acquire():
        raise HTTPException(
            status_code=503,
            detail="Уже выполняется другой расчёт — на Free-тарифе (0.1 CPU) они идут по одному. Повторите через минуту.",
        )

    job_id = jobs.create_job({"mode": mode, "filename": filename})
    thread = threading.Thread(
        target=_worker,
        args=(job_id, raw_bytes, threshold, window),
        daemon=True,
        name=f"pipeline-{job_id}",
    )
    thread.start()
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "queued"})


def _worker(job_id: str, raw_bytes: bytes | None, threshold: float | None, window: int | None) -> None:
    try:
        run_cfg = config_for_run(CFG, threshold, window)

        if raw_bytes is not None:
            jobs.update(job_id, message=f"Файл принят ({len(raw_bytes) / 1024:.1f} КБ), разбираю…")
            df_raw = data_loader.parse_csv_bytes(raw_bytes)
        else:
            demo = _demo_path()
            if not demo.exists():
                raise FileNotFoundError("Встроенный демо-ряд не найден: запустите scripts/prepare_sample.py")
            df_raw = data_loader.load_csv_path(demo)
            jobs.update(job_id, message="Использую встроенный демонстрационный ряд (UCI, CC BY 4.0)")

        runs_dir = BASE_DIR / run_cfg["output"]["runs_dir"] / job_id
        result = pipeline.run_pipeline(
            df_raw,
            run_cfg,
            progress=jobs.progress_cb(job_id, STAGES),
            pace=float(run_cfg.get("_pace", 0.35)),
            artifacts_dir=runs_dir,
        )

        # артефакты отдаём по URL
        urls = {
            "csv": f"/api/results/{job_id}/anomalies.csv",
            "json": f"/api/results/{job_id}/anomalies.json",
            "png": f"/api/results/{job_id}/anomalies.png",
            "html": f"/api/results/{job_id}/report.html",
        }
        result["artifacts"] = urls
        jobs.update(job_id, status="done", percent=100, stage="Готово", result=result)
    except Exception as e:  # noqa: BLE001 - ошибку показываем пользователю
        jobs.update(job_id, status="error", error=str(e), message=f"Ошибка: {e}")
    finally:
        jobs.release()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена или устарела (перезапуск сервиса)")
    return job


@app.get("/api/results/{job_id}/{artifact}")
def job_artifact(job_id: str, artifact: str) -> FileResponse:
    if artifact not in ARTIFACT_NAMES:
        raise HTTPException(status_code=404, detail="Неизвестный артефакт")
    path = BASE_DIR / CFG["output"]["runs_dir"] / job_id / artifact
    if not path.exists():
        raise HTTPException(status_code=404, detail="Артефакт не найден (задача не завершена или устарела)")
    media = {
        "anomalies.csv": "text/csv",
        "anomalies.json": "application/json",
        "anomalies.png": "image/png",
        "report.html": "text/html",
    }[artifact]
    return FileResponse(path, media_type=media, filename=artifact)
