# Источник данных

## Что лежит рядом с этим файлом

| Файл | Что это | Размер |
|------|---------|--------|
| `energy_hourly.csv` | **демо-сэмпл**: среднечасовое потребление, 01.11.2008 – 31.12.2008, 1464 точки | ~60 КБ |
| `energy_full_hourly.csv` | полный часовой ряд (2006-12-16 – 2010-11-26), 34 589 точек — для собственных экспериментов | ~1 МБ |

Формат обоих файлов: колонки `timestamp,value` (значение — средняя активная мощность за час, кВт).

## Источник

**UCI Machine Learning Repository — «Individual Household Electric Power Consumption»**

- Страница: <https://archive.ics.uci.edu/dataset/275/individual+household+electric+power+consumption>
- Авторы: Georges Hebrail (EDF R&D), Alice Gerard (EDF R&D), 2012
- Сценарий варианта 16: `energy` — потребление электроэнергии одной квартиры
  (Париж), минутные измерения за ~4 года.

## Лицензия

**CC BY 4.0** (Creative Commons Attribution 4.0 International) — свободное
использование с указанием авторства. Полный текст: <https://creativecommons.org/licenses/by/4.0/>

## Как был получен сэмпл

Скрипт `scripts/prepare_sample.py` (идет в репозитории):

1. скачивает архив датасета с UCI (~20 МБ);
2. читает колонки `Date`, `Time`, `Global_active_power` порциями;
3. агрегирует минутные данные в **среднечасовые значения** (`resample('h').mean()`);
4. берёт срез **ноябрь–декабрь 2008** (зимний сезон: яркие суточные пики,
   интересные для детекции аномалий);
5. сохраняет `data/energy_hourly.csv` и полный ряд `data/energy_full_hourly.csv`.

Воспроизведение:

```bash
python scripts/prepare_sample.py
```

## Цитирование (если понадобится)

> Hebrail, G. & Gerard, A. (2012). Individual Household Electric Power Consumption [Dataset].
> UCI Machine Learning Repository. https://doi.org/10.24432/C58K54
