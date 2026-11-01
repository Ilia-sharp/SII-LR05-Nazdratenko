# SII-LR05-Nazdratenko · Поиск аномалий во временном ряде

[![Python](https://img.shields.io/badge/Python-3.10%2B-5B8DEF?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-7BE0C8?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Render](https://img.shields.io/badge/Render-Blueprint%20Free-8A9CF5?logo=render&logoColor=white)](https://render.com/)
[![License: MIT](https://img.shields.io/badge/Code-MIT-E6EEF6)](LICENSE)

Лабораторная работа №5 «Системы искусственного интеллекта» — **вариант 16**:
сценарий `energy` (потребление электроэнергии), метод **z-score**, окно **24**.

Сервис: данные → явная обработка пропусков → оценка аномальности → тревоги → отчёт.
Временной ряд рассматривается **как сервис**, а не только как статистическая задача:
веб-интерфейс с живым прогрессом по стадиям, CLI, конфигурируемый порог, baseline-сравнение, JSON-выдача с `model_version`.

![Интерфейс сервиса](docs/screenshot.png)

---

## Возможности

- **Веб-интерфейс** (FastAPI + лёгкий SPA, ~120 МБ RAM): встроенный демо-ряд или загрузка своего CSV, живой прогресс по 7 стадиям с журналом расчёта, KPI, интерактивный график (Chart.js) с тревогами поцветно, таблицы тревог/пропусков/baseline, скачивание всех артефактов.
- **Конвейер**: разбор CSV (русские и английские заголовки, `дд.мм.гггг` и ISO), приведение к регулярной сетке, **явная политика пропусков** (короткие дыры интерполируются, длинные — остаются NaN и попадают в отчёт), скользящий z-score (окно 24, включая текущую точку), серьёзность тревог (low/medium/high), защита от константного ряда (flatline).
- **Baseline-сравнение**: наивный прогноз «вчера» + бонусом глобальный z-score — метрики: число тревог, доля, MAE, пересечение множеств (Жаккар).
- **Конфигурация без хардкода**: порог, окно, границы серьёзности, политика пропусков — в `config.yaml`; переопределяются переменными окружения (`ANOMALY_THRESHOLD`, `ANOMALY_WINDOW`) и параметрами CLI/UI.
- **Артефакты**: `anomalies.csv` (timestamp, value, score, is_anomaly, severity), `anomalies.json` (с `model_version`, `threshold`, `items`), `anomalies.png`, `report.html` (самодостаточный отчёт).
- **Пропуски не только в коде**: второй прогон на полном ряду (34 589 точек) реально содержит 8 дыр — 1 интерполирована, 7 оставлены пустыми; артефакты в `outputs/full_series/`, разбор в `REPORT.md §4`.
- **Тесты**: 13 pytest-тестов — пропуски, детекция, flatline, конфиг, формат JSON.

## Быстрый старт

```bash
# 1. Зависимости (Python 3.10+)
pip install -r requirements.txt

# 2. CLI-прогон на встроенном демо-ряде → артефакты в outputs/
python run_pipeline.py

# 2b. Проверка политики пропусков на полном ряду (8 реальных дыр) → outputs/full_series/
python run_pipeline.py --input data/energy_full_hourly.csv --out outputs/full_series

# 3. Сайт
uvicorn app.main:app --port 8000
# открыть http://localhost:8000

# 4. Тесты
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

> Демо-данные уже в репозитории (`data/energy_hourly.csv`). Если их нет —
> пересоберите: `python scripts/prepare_sample.py` (скачает UCI-датасет, ~20 МБ).

## Конфигурация (`config.yaml`)

| Параметр | По умолчанию | Смысл | Переопределение |
|---|---|---|---|
| `detection.threshold` | 3.0 | порог тревоги, σ | env `ANOMALY_THRESHOLD`, CLI `--threshold`, поле на сайте |
| `detection.window` | 24 | окно z-score, точек | env `ANOMALY_WINDOW`, CLI `--window`, поле на сайте |
| `detection.severity` | medium 1.1 / high 1.3 | границы серьёзности (кратные порогу) | `config.yaml` |
| `data.max_interp_gap` | 3 | дыры ≤ N точек интерполируются, длинные — нет | `config.yaml` |
| `data.monitor_freshness` | false | следить за «свежестью» живого потока (stale) | `config.yaml` |
| `baseline.yesterday_threshold` | 3.0 | порог baseline «вчера» | `config.yaml` |

## API

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/` | одностраничный интерфейс |
| GET | `/api/health` | живость + `model_version` + демо-данные + `freshness` (возраст последней точки) |
| GET | `/api/config` | публичная конфигурация (порог/окно/серьёзность) |
| POST | `/api/run` | запуск расчёта: `mode=demo|file`, `file`, `threshold`, `window` |
| GET | `/api/jobs/{id}` | статус/прогресс/журнал задачи (опрос сайтом) |
| GET | `/api/results/{id}/{файл}` | `anomalies.csv · anomalies.json · anomalies.png · report.html` |

Минимальный формат результата (требование задания) соблюдён:

```json
{"model_version": "anomaly-v1", "threshold": 3.0,
 "items": [{"timestamp": "2008-11-04T18:00:00", "value": 3.736, "score": 3.97,
            "is_anomaly": true, "severity": "high"}]}
```

## Деплой на Render (Blueprint, тариф Free)

1. Залейте репозиторий на GitHub (или GitVerse) — см. ниже.
2. На [render.com](https://render.com): **New + → Blueprint** → выберите репозиторий.
3. Render прочитает `render.yaml` и сам создаст веб-сервис: Python 3.11, установка зависимостей, старт `uvicorn`, health-check `/api/health`, переменные окружения.
4. Готово. Тариф Free: $0/мес, 0.1 CPU, 512 МБ RAM — сервис укладывается (~120–150 МБ RSS).

Заметки о Free-тарифе: сервис засыпает после ~15 минут неактивности, первый запрос «будит» его (~40–60 с); файловая система эфемерна — артефакты веб-прогонов живут до перезапуска (CLI-прогон пересоздаёт их за секунды).

## Репозиторий и тег v1.0

Репозиторий опубликован: <https://github.com/Ilia-sharp/SII-LR05-Nazdratenko>.
Тег `v1.0` указывает на текущий `main` и запушен (проверка: `git ls-remote --tags origin` → `refs/tags/v1.0`).

Перевесить тег после правок (тег `v1.0` должен указывать на сдаваемый коммит):

```bash
git add -A && git commit -m "..."
git push origin main
git tag -f v1.0 && git push -f origin v1.0
```

По заданию сдача идёт на **GitVerse** (репозиторий `SII-LR05-<фамилия>`). Если нужен GitVerse-репозиторий:

```bash
git remote add gitverse https://gitverse.ru/<ваш-логин>/SII-LR05-Nazdratenko.git
git push gitverse main
git push gitverse v1.0
```

## Структура проекта

```text
SII-LR05-Nazdratenko/
├── app/                    # бэкенд
│   ├── config.py           #   YAML + env, без хардкода порогов
│   ├── data_loader.py      #   CSV, регулярная сетка, политика пропусков
│   ├── detector.py         #   z-score (окно 24) + baseline'ы
│   ├── pipeline.py         #   конвейер со стадиями и прогрессом
│   ├── progress.py         #   задачи, живой прогресс для сайта
│   ├── report.py           #   CSV / JSON / PNG / HTML-артефакты
│   └── main.py             #   FastAPI: /, /api/run, /api/jobs, /api/results…
├── static/                 # фронтенд (ванильный JS + Chart.js)
│   ├── index.html          #   логотип Nazdratenko, простой плоский UI
│   ├── style.css           #   синий акцент на белом, без теней и градиентов
│   └── app.js              #   прогресс по стадиям, график, таблицы
├── data/
│   ├── energy_hourly.csv   # демо-сэмпл UCI (ноя–дек 2008), CC BY 4.0
│   ├── energy_full_hourly.csv  # полный часовой ряд 2006–2010
│   └── SOURCE.md           # источник, лицензия, как получен сэмпл
├── outputs/                # артефакты CLI-прогона (в репозитории)
│   └── full_series/        #   прогон на полном ряду — доказательство политики пропусков
├── scripts/
│   ├── prepare_sample.py   # сборка демо-сэмпла из UCI-датасета
│   └── inspect_triggers.py # служебный: контекст тревог для REPORT.md
├── tests/test_pipeline.py  # 13 pytest-тестов
├── config.yaml             # все пороги и политики — здесь
├── render.yaml             # Render Blueprint (Free)
├── run_pipeline.py         # CLI без сайта
├── REPORT.md               # разбор тревог, baseline, пропуски
├── PROMPT.md               # ультра-подробный промт воспроизведения проекта
└── README.md
```

## Данные и лицензии

- Код проекта — MIT (`LICENSE`).
- Данные — [UCI: Individual Household Electric Power Consumption](https://archive.ics.uci.edu/dataset/275/individual+household+electric+power+consumption), Hebrail & Gerard (EDF R&D), 2012, лицензия **CC BY 4.0** (детали: `data/SOURCE.md`).

## Отчёт

Разбор всех 9 срабатываний, сравнение с baseline и поведение сервиса при пропаже данных — в [REPORT.md](REPORT.md).
