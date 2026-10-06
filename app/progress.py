"""Хранилище задач расчёта (in-memory) с живым прогрессом.

Прогресс нужен пользователю сайта, чтобы не смотреть на крутящийся спиннер,
а видеть стадии: приём данных → парсинг → пропуски → z-score → baseline → артефакты.

Особенности Free-тарифа Render (0.1 CPU, 512 МБ, один процесс):
* расчёты выполняются по одному (семафор) — два параллельных прогона
  на 0.1 CPU просто мешали бы друг другу;
* задачи живут в памяти процесса; после перезапуска сервиса история
  расчётов очищается (артефакты Regenerate повторным прогоном).
"""

from __future__ import annotations

import threading
import time
import uuid

_LOCK = threading.Lock()
JOBS: dict[str, dict] = {}
ACTIVE = threading.Semaphore(1)
MAX_JOBS_KEPT = 40


def _cleanup_locked() -> None:
    if len(JOBS) <= MAX_JOBS_KEPT:
        return
    jobs = sorted(JOBS.items(), key=lambda kv: kv[1].get("created", 0))
    for job_id, _ in jobs[: len(JOBS) - MAX_JOBS_KEPT]:
        JOBS.pop(job_id, None)


def create_job(meta: dict | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",  # queued | running | done | error | rejected
            "stage": "",
            "stage_index": 0,
            "percent": 0,
            "log": [],
            "result": None,
            "error": None,
            "created": time.time(),
            "meta": meta or {},
        }
        _cleanup_locked()
    return job_id


def update(job_id: str, **fields) -> None:
    with _LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        message = fields.pop("message", None)
        if message:
            job["log"].append(message)
        job.update(fields)


def progress_cb(job_id: str, stages: list[tuple[int, str]]):
    """Замыкание для pipeline: percent+message → состояние задачи."""

    def _cb(percent: int, message: str) -> None:
        stage_index = 0
        stage_name = ""
        for i, (pct, name) in enumerate(stages):
            if percent >= pct:
                stage_index = i
                stage_name = name
        update(
            job_id,
            status="running",
            percent=percent,
            stage=stage_name,
            stage_index=stage_index,
            message=f"[{percent:3d}%] {message}",
        )

    return _cb


def get(job_id: str) -> dict | None:
    with _LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def try_acquire() -> bool:
    return ACTIVE.acquire(blocking=False)


def release() -> None:
    try:
        ACTIVE.release()
    except ValueError:
        pass
