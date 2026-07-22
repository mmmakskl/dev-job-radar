"""Экспорт структурированных вакансий в два листа Google Sheets."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import gspread

from tg_vacancy_bot import config
from tg_vacancy_bot.models import NOT_SPECIFIED, VacancyAnalysis


FULL_HEADERS = [
    "ID вакансии",
    "Дата публикации",
    "Дата добавления",
    "Название вакансии",
    "Компания",
    "Грейд от",
    "Грейд до",
    "Уровень ответственности",
    "Основная роль",
    "Специализация",
    "Сфера продукта",
    "Опыт от, лет",
    "Формат работы",
    "Страна",
    "Город",
    "География найма",
    "Релокация",
    "Тип занятости",
    "Зарплата от",
    "Зарплата до",
    "Валюта",
    "Период зарплаты",
    "Основной язык",
    "Обязательный стек",
    "Желательный стек",
    "Язык вакансии",
    "Контакт",
    "Ссылка для отклика",
    "Telegram-источник",
    "Название Telegram-канала",
    "Краткое описание",
    "Обязанности",
    "Требования",
    "Дополнительные условия",
    "Исходное описание",
    "Статус вакансии",
    "Качество данных",
]
SHORT_HEADERS = [
    "Дата",
    "Вакансия",
    "Компания",
    "Грейд",
    "Роль",
    "Сфера",
    "Формат",
    "Локация",
    "Зарплата",
    "Ключевой стек",
    "Краткое описание",
    "Ссылка",
    "Статус",
]
# Обратная совместимость для импортов старого имени.
HEADERS = FULL_HEADERS

_HEADER_FORMAT = {
    "backgroundColor": {"red": 0.18, "green": 0.35, "blue": 0.55},
    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
    "horizontalAlignment": "CENTER",
    "verticalAlignment": "MIDDLE",
    "wrapStrategy": "WRAP",
}
_WRAP_FORMAT = {"verticalAlignment": "TOP", "wrapStrategy": "WRAP"}
_FORMATTED_WORKSHEET_IDS: set[int] = set()


def _format_datetime(value: datetime) -> str:
    """Форматирует дату в OUTPUT_TIMEZONE; naive-дату считает UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(config.OUTPUT_TIMEZONE)).strftime(
        "%Y-%m-%d %H:%M"
    )


def _join(values: list[str]) -> str:
    return ", ".join(values) if values else NOT_SPECIFIED


def _grade_label(grade_from: str, grade_to: str) -> str:
    if grade_from == NOT_SPECIFIED and grade_to == NOT_SPECIFIED:
        return NOT_SPECIFIED
    if grade_from == grade_to:
        return grade_from
    if grade_from == NOT_SPECIFIED:
        return grade_to
    if grade_to == NOT_SPECIFIED:
        return grade_from
    return f"{grade_from}–{grade_to}"


def _location_label(data: VacancyAnalysis) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for value in (data.city, data.country, data.hiring_geography):
        if value == NOT_SPECIFIED or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        values.append(value)
    return ", ".join(values) if values else NOT_SPECIFIED


def _number_label(value: float | int) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def _salary_label(data: VacancyAnalysis) -> str:
    salary_from, salary_to = data.salary_from, data.salary_to
    if salary_from is None and salary_to is None:
        return NOT_SPECIFIED
    suffix_parts = []
    if data.currency != NOT_SPECIFIED:
        suffix_parts.append(data.currency)
    if data.salary_period != NOT_SPECIFIED:
        if suffix_parts:
            suffix_parts[-1] = f"{suffix_parts[-1]}/{data.salary_period}"
        else:
            suffix_parts.append(data.salary_period)
    suffix = f" {' '.join(suffix_parts)}" if suffix_parts else ""
    if salary_from is not None and salary_to is not None:
        if salary_from == salary_to:
            return f"{_number_label(salary_from)}{suffix}"
        return (
            f"{_number_label(salary_from)}–{_number_label(salary_to)}{suffix}"
        )
    if salary_from is not None:
        return f"от {_number_label(salary_from)}{suffix}"
    return f"до {_number_label(salary_to)}{suffix}"


def _data_quality(data: VacancyAnalysis, response_link: str) -> str:
    location_present = any(
        value != NOT_SPECIFIED
        for value in (data.country, data.city, data.hiring_geography)
    )
    important = [
        data.company != NOT_SPECIFIED,
        data.title != NOT_SPECIFIED,
        data.grade_from != NOT_SPECIFIED or data.grade_to != NOT_SPECIFIED,
        data.work_format != NOT_SPECIFIED,
        location_present,
        bool(data.required_stack),
        bool(response_link),
    ]
    if all(important):
        return "Полные"
    if sum(important) >= 3:
        return "Частичные"
    return "Низкое"


def build_full_row(
    *,
    vacancy_id: str,
    post_link: str,
    channel_name: str,
    data: VacancyAnalysis,
    raw_text: str,
    published_at: datetime,
    added_at: datetime,
) -> list[Any]:
    """Формирует 37 типизированных значений полного листа."""
    response_link = data.apply_link if data.apply_link != NOT_SPECIFIED else post_link
    return [
        vacancy_id,
        _format_datetime(published_at),
        _format_datetime(added_at),
        data.title,
        data.company,
        data.grade_from,
        data.grade_to,
        data.responsibility_level,
        _join(data.primary_roles),
        _join(data.specializations),
        data.product_domain,
        data.experience_from if data.experience_from is not None else "",
        data.work_format,
        data.country,
        data.city,
        data.hiring_geography,
        data.relocation,
        data.employment_type,
        data.salary_from if data.salary_from is not None else "",
        data.salary_to if data.salary_to is not None else "",
        data.currency,
        data.salary_period,
        data.primary_language,
        _join(data.required_stack),
        _join(data.preferred_stack),
        data.vacancy_language,
        data.contact,
        response_link,
        post_link,
        channel_name or NOT_SPECIFIED,
        data.summary,
        data.responsibilities,
        data.requirements,
        data.additional_conditions,
        raw_text,
        "Новая",
        _data_quality(data, response_link),
    ]


def build_short_row(
    *,
    post_link: str,
    data: VacancyAnalysis,
    published_at: datetime,
) -> list[Any]:
    """Формирует 13 значений краткого листа без исходного текста."""
    response_link = data.apply_link if data.apply_link != NOT_SPECIFIED else post_link
    key_stack = (data.required_stack + data.preferred_stack)[:6]
    return [
        _format_datetime(published_at),
        data.title,
        data.company,
        _grade_label(data.grade_from, data.grade_to),
        _join(data.primary_roles),
        data.product_domain,
        data.work_format,
        _location_label(data),
        _salary_label(data),
        _join(key_stack),
        data.summary[:250],
        response_link,
        "Новая",
    ]


def _column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _format_worksheet(worksheet, headers: list[str], *, full: bool) -> None:
    """Закрепляет заголовок, фильтр, перенос и умеренные ширины."""
    last_column = _column_letter(len(headers))
    worksheet.freeze(rows=1)
    worksheet.set_basic_filter(f"A1:{last_column}1000")
    worksheet.format(f"A1:{last_column}1", _HEADER_FORMAT)
    worksheet.format(f"A2:{last_column}1000", _WRAP_FORMAT)

    requests = [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(headers),
                },
                "properties": {"pixelSize": 145},
                "fields": "pixelSize",
            }
        }
    ]
    if full:
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "COLUMNS",
                        "startIndex": 30,
                        "endIndex": 35,
                    },
                    "properties": {"pixelSize": 320},
                    "fields": "pixelSize",
                }
            }
        )
    else:
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "COLUMNS",
                        "startIndex": 10,
                        "endIndex": 11,
                    },
                    "properties": {"pixelSize": 300},
                    "fields": "pixelSize",
                }
            }
        )
    worksheet.spreadsheet.batch_update({"requests": requests})


def _get_or_create_worksheet(spreadsheet, title: str, headers: list[str], *, full: bool):
    try:
        worksheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=title,
            rows=1000,
            cols=len(headers),
        )

    current_headers = worksheet.row_values(1)
    if not current_headers:
        worksheet.update([headers], "A1", value_input_option="RAW")
    elif current_headers[: len(headers)] != headers:
        raise RuntimeError(
            f"Лист «{title}» уже существует с несовместимыми заголовками"
        )
    if worksheet.id not in _FORMATTED_WORKSHEET_IDS:
        _format_worksheet(worksheet, headers, full=full)
        _FORMATTED_WORKSHEET_IDS.add(worksheet.id)
    return worksheet


def _short_row_number(
    worksheet,
    vacancy_id: str,
    expected_row: list[Any],
) -> int | None:
    """Ищет краткую строку по ID-note или точному содержимому после сбоя note."""
    for index, row in enumerate(
        worksheet.get_notes(grid_range="L2:L"),
        start=2,
    ):
        if any(note == f"vacancy_id:{vacancy_id}" for note in row):
            return index

    expected = [str(value) for value in expected_row]
    for index, row in enumerate(worksheet.get_all_values()[1:], start=2):
        if row[: len(expected)] == expected:
            worksheet.insert_note(f"L{index}", f"vacancy_id:{vacancy_id}")
            return index
    return None


def _append_row(worksheet, row: list[Any]) -> int:
    response = worksheet.append_rows(
        [row],
        value_input_option="USER_ENTERED",
        insert_data_option="INSERT_ROWS",
    )
    updated_range = response.get("updates", {}).get("updatedRange", "")
    match = re.search(r"![A-Z]+(\d+):", updated_range)
    if match:
        return int(match.group(1))
    return len(worksheet.get_all_values())


def _export_to_sheets_sync(
    *,
    vacancy_id: str,
    post_link: str,
    channel_name: str,
    data: VacancyAnalysis,
    raw_text: str,
    published_at: datetime,
) -> bool:
    """Дозаписывает отсутствующие строки и устойчив к частичному сбою."""
    gc = gspread.service_account(filename="credentials.json")
    spreadsheet = gc.open_by_url(config.GOOGLE_SHEET_URL)
    full_sheet = _get_or_create_worksheet(
        spreadsheet,
        config.GOOGLE_SHEET_FULL_TITLE,
        FULL_HEADERS,
        full=True,
    )
    short_sheet = _get_or_create_worksheet(
        spreadsheet,
        config.GOOGLE_SHEET_SHORT_TITLE,
        SHORT_HEADERS,
        full=False,
    )

    full_exists = vacancy_id in {
        value.strip() for value in full_sheet.col_values(1)[1:] if value.strip()
    }
    added_at = datetime.now(timezone.utc)
    short_row = build_short_row(
        post_link=post_link,
        data=data,
        published_at=published_at,
    )
    short_exists = _short_row_number(short_sheet, vacancy_id, short_row) is not None

    if not full_exists:
        _append_row(
            full_sheet,
            build_full_row(
                vacancy_id=vacancy_id,
                post_link=post_link,
                channel_name=channel_name,
                data=data,
                raw_text=raw_text,
                published_at=published_at,
                added_at=added_at,
            ),
        )
        full_exists = True

    if not short_exists:
        row_number = _append_row(
            short_sheet,
            short_row,
        )
        short_sheet.insert_note(f"L{row_number}", f"vacancy_id:{vacancy_id}")
        short_exists = True

    return full_exists and short_exists


def _get_existing_links_sync() -> set[str]:
    """Загружает ссылки из legacy первого листа для обратной совместимости."""
    gc = gspread.service_account(filename="credentials.json")
    spreadsheet = gc.open_by_url(config.GOOGLE_SHEET_URL)
    worksheet = spreadsheet.sheet1
    return {link.strip() for link in worksheet.col_values(2) if link.strip()}


async def get_existing_links() -> set[str]:
    """Один раз загружает ссылки из старого листа."""
    if not config.GOOGLE_SHEET_URL:
        raise RuntimeError("GOOGLE_SHEET_URL не настроен в .env файле")
    links = await asyncio.to_thread(_get_existing_links_sync)
    logging.info("Загружено legacy-ссылок из Google Таблицы: %d", len(links))
    return links


async def append_to_google_sheet(
    *,
    vacancy_id: str,
    post_link: str,
    channel_name: str,
    data: VacancyAnalysis,
    raw_text: str,
    published_at: datetime,
) -> bool:
    """Асинхронно дозаписывает вакансию в оба новых листа."""
    if not data.is_match:
        return False
    if not config.GOOGLE_SHEET_URL:
        logging.error("GOOGLE_SHEET_URL не настроен в .env файле")
        return False
    try:
        saved = await asyncio.to_thread(
            _export_to_sheets_sync,
            vacancy_id=vacancy_id,
            post_link=post_link,
            channel_name=channel_name,
            data=data,
            raw_text=raw_text,
            published_at=published_at,
        )
        if saved:
            logging.info(
                "Вакансия %s записана в полный и краткий листы",
                vacancy_id,
            )
        return saved
    except Exception as exc:
        logging.error(
            "Ошибка записи вакансии %s в Google Sheets: %s",
            vacancy_id,
            exc,
        )
        return False
