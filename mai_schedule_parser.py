"""
MAI Schedule Parser — адаптирован из lavafrai/maiapp
=====================================================
Реальный API-сервер: https://maiapp.lavafrai.ru/api/v1
Источник: https://github.com/lavafrai/maiapp

Реальная структура JSON (выявлена из живого API):

  GET /groups   → [{"name": "М8О-208Б-22", "fac": "...", "level": "..."}, ...]
  GET /teachers → [{"name": {"name": "Иванов И.И."}, "uid": {"uid": "..."}}, ...]
  GET /schedule/{urlEncodedName} → {
    "name": "М3О-505С-21",
    "id":   {"id": "М3О-505С-21"},
    "days": [
      {
        "date": {"year": 2026, "month": 2, "day": 10},
        "day":  "Вт",
        "lessons": [
          {
            "name":       "Математика",
            "time_start": {"time": "9:00:00"},
            "time_end":   {"time": "10:30:00"},
            "type":       "ЛК",
            "day":        {"year": 2026, "month": 2, "day": 10},
            "lectors": [
              {
                "name": {"name": "Иванов Дмитрий Александрович"},
                "uid":  {"uid": "eb7d5e30-1d99-11e0-9baf-1c6f65450efa"}
              }
            ],
            "rooms": [{"name": "3-254", "uid": "7b618afa-..."}],
            "lms": "", "teams": "", "other": ""
          }
        ]
      }
    ]
  }

Публичные функции:
  get_groups()                          → список групп
  get_teachers(query)                   → список преподавателей
  get_group_schedule(group, week)       → расписание группы на неделю
  get_teacher_schedule(name, week)      → расписание преподавателя на неделю
  get_room_schedule(room_name, week)    → занятость аудитории на неделю
  get_full_semester_schedule(name)      → всё расписание семестра
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import quote

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------
BASE_URL = "https://maiapp.lavafrai.ru/api/v1"

HEADERS = {
    "User-Agent": "MAI-Schedule-Parser/3.0",
    "Accept":     "application/json",
}

DAY_RU = {
    "Пн": "Понедельник",
    "Вт": "Вторник",
    "Ср": "Среда",
    "Чт": "Четверг",
    "Пт": "Пятница",
    "Сб": "Суббота",
    "Вс": "Воскресенье",
}

LESSON_TYPE_RU = {
    "ЛК":      "Лекция",
    "ЛР":      "Лабораторная работа",
    "ПЗ":      "Практическое занятие",
    "Экзамен": "Экзамен",
    "Встреча": "Встреча",
    "":        "Другое",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# Утилиты парсинга — с поддержкой реального формата API
# ---------------------------------------------------------------------------

def _parse_date(raw) -> Optional[date]:
    """
    Парсит дату. Реальный формат из API:
      {"year": 2026, "month": 2, "day": 10}   ← основной
      [2026, 2, 10]                             ← запасной
      "2026-02-10"                              ← запасной
    """
    if isinstance(raw, dict):
        try:
            return date(int(raw["year"]), int(raw["month"]), int(raw["day"]))
        except (KeyError, ValueError, TypeError):
            return None
    if isinstance(raw, list) and len(raw) >= 3:
        try:
            return date(int(raw[0]), int(raw[1]), int(raw[2]))
        except (ValueError, TypeError):
            return None
    if isinstance(raw, str) and raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _parse_time(raw) -> str:
    """
    Парсит время. Формат: {"time": "9:00:00"} → "09:00"
    """
    if isinstance(raw, dict):
        s = raw.get("time", "")
    elif isinstance(raw, str):
        s = raw
    else:
        return ""
    parts = s.split(":")
    if len(parts) >= 2:
        return f"{int(parts[0]):02d}:{parts[1]}"
    return s


def _unwrap_str(raw, key: str) -> str:
    """
    Раскрывает вложенный строковый объект:
      {"name": "Иванов И.И."}  → "Иванов И.И."
      {"uid": "abc-123"}       → "abc-123"
      "прямая строка"          → "прямая строка"
    """
    if isinstance(raw, dict):
        return str(raw.get(key, ""))
    if isinstance(raw, str):
        return raw
    return ""


def _normalize_lesson(raw: dict) -> dict:
    """Нормализует одно занятие к единому формату."""
    teachers   = []
    teacher_uids = []
    for lector in raw.get("lectors", []):
        if isinstance(lector, dict):
            # name → {"name": "Иванов И.И."} или просто строка
            teachers.append(_unwrap_str(lector.get("name", ""), "name"))
            # uid  → {"uid": "abc-123"}      или просто строка
            teacher_uids.append(_unwrap_str(lector.get("uid", ""), "uid"))
        elif isinstance(lector, str):
            teachers.append(lector)

    room_names = []
    room_uids  = []
    for room in raw.get("rooms", []):
        if isinstance(room, dict):
            room_names.append(str(room.get("name", "")))
            room_uids.append(str(room.get("uid", "")))
        elif isinstance(room, str):
            room_names.append(room)

    lesson_type = raw.get("type", "")
    parsed_dt   = _parse_date(raw.get("day"))
    iso_date    = parsed_dt.isoformat() if parsed_dt else ""

    return {
        "name":          raw.get("name", ""),
        "type":          lesson_type,
        "type_full":     LESSON_TYPE_RU.get(lesson_type, lesson_type),
        "time_start":    _parse_time(raw.get("time_start")),
        "time_end":      _parse_time(raw.get("time_end")),
        "date":          iso_date,
        "teachers":      teachers,
        "teacher_uids":  teacher_uids,
        "rooms":         room_names,
        "room_uids":     room_uids,
        "lms":           raw.get("lms",   ""),
        "teams":         raw.get("teams", ""),
        "other":         raw.get("other", ""),
    }


def _week_range(offset: int = 0) -> tuple[date, date]:
    """(Понедельник, Воскресенье) недели со смещением offset."""
    today  = datetime.now().date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    return monday, monday + timedelta(days=6)


def _filter_days_by_week(days: list, week_offset: int) -> list:
    """Оставляет только дни нужной недели."""
    monday, sunday = _week_range(week_offset)
    result = []
    for day in days:
        d = _parse_date(day.get("date"))
        if d and monday <= d <= sunday:
            result.append(day)
    return result


def _build_output(raw: dict, week_offset: Optional[int]) -> dict:
    """Строит итоговый словарь расписания."""
    all_days = raw.get("days", [])

    if week_offset is not None:
        days_to_show = _filter_days_by_week(all_days, week_offset)
        monday, sunday = _week_range(week_offset)
        week_info = {
            "offset": week_offset,
            "monday": monday.isoformat(),
            "sunday": sunday.isoformat(),
        }
    else:
        days_to_show = all_days
        week_info = None

    schedule_days = []
    for day in sorted(days_to_show, key=lambda d: _parse_date(d.get("date")) or date.min):
        parsed_dt = _parse_date(day.get("date"))
        day_abbr  = day.get("day", "")
        lessons   = [_normalize_lesson(l) for l in day.get("lessons", [])]
        schedule_days.append({
            "date":         parsed_dt.isoformat() if parsed_dt else "",
            "day_short":    day_abbr,
            "day_full":     DAY_RU.get(day_abbr, day_abbr),
            "lesson_count": len(lessons),
            "lessons":      lessons,
        })

    # id может прийти как {"id": "..."} или как строка
    raw_id = raw.get("id", "")
    schedule_id = _unwrap_str(raw_id, "id") if isinstance(raw_id, dict) else str(raw_id)

    out = {
        "status": "ok",
        "name":   raw.get("name", ""),
        "id":     schedule_id,
        "days":   schedule_days,
    }
    if week_info:
        out["week"] = week_info
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _get(path: str, timeout: int = 15):
    url = f"{BASE_URL}/{path.lstrip('/')}"
    try:
        resp = SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error("HTTP error: %s  URL: %s", exc, url)
        return None
    except ValueError as exc:
        logger.error("JSON error: %s  URL: %s", exc, url)
        return None


def get_schedule_raw(name: str) -> Optional[dict]:
    """Сырой ответ /schedule/{name} или None."""
    return _get(f"/schedule/{quote(name, safe='')}")


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def get_groups() -> dict:
    """
    Список всех групп МАИ.

    Возвращает:
    {
      "status": "ok",
      "count":  512,
      "groups": [{"name": "М8О-208Б-22", "faculty": "...", "level": "..."}, ...]
    }
    """
    data = _get("/groups")
    if data is None:
        return {"status": "error", "message": "Не удалось получить список групп", "groups": []}

    groups = []
    for item in (data if isinstance(data, list) else []):
        if isinstance(item, dict):
            groups.append({
                "name":    item.get("name", ""),
                "faculty": item.get("fac",   ""),
                "level":   item.get("level", ""),
            })
        elif isinstance(item, str):
            groups.append({"name": item, "faculty": "", "level": ""})

    return {"status": "ok", "count": len(groups), "groups": groups}


def get_teachers(query: str = "") -> dict:
    """
    Список преподавателей с опциональным поиском по ФИО.

    Возвращает:
    {
      "status": "ok",
      "count":  N,
      "teachers": [{"name": "Иванов Иван Иванович", "uid": "..."}, ...]
    }
    """
    data = _get("/teachers")
    if data is None:
        return {"status": "error", "message": "Не удалось получить список преподавателей", "teachers": []}

    teachers = []
    for item in (data if isinstance(data, list) else []):
        if isinstance(item, dict):
            # name → {"name": "..."} или строка
            name = _unwrap_str(item.get("name", ""), "name")
            uid  = _unwrap_str(item.get("uid",  ""), "uid")
        elif isinstance(item, str):
            name, uid = item, ""
        else:
            continue

        if query and query.lower() not in name.lower():
            continue
        if name.strip():
            teachers.append({"name": name.strip(), "uid": uid})

    return {"status": "ok", "count": len(teachers), "teachers": teachers}


def get_group_schedule(group: str, week: int = 0) -> dict:
    """
    Расписание группы на заданную неделю.

    Параметры:
        group – название группы, например "М8О-208Б-22"
        week  – смещение: 0=текущая, 1=следующая, -1=прошлая

    Возвращает:
    {
      "status": "ok",
      "name":   "М3О-505С-21",
      "id":     "М3О-505С-21",
      "week":   {"offset": 0, "monday": "2026-04-27", "sunday": "2026-05-03"},
      "days": [
        {
          "date":         "2026-04-28",
          "day_short":    "Вт",
          "day_full":     "Вторник",
          "lesson_count": 2,
          "lessons": [
            {
              "name":         "Высшая математика",
              "type":         "ЛК",
              "type_full":    "Лекция",
              "time_start":   "09:00",
              "time_end":     "10:30",
              "date":         "2026-04-28",
              "teachers":     ["Иванов Дмитрий Александрович"],
              "teacher_uids": ["eb7d5e30-1d99-11e0-9baf-1c6f65450efa"],
              "rooms":        ["3-254"],
              "room_uids":    ["7b618afa-2dbc-11e8-aec0-003048dec27f"],
              "lms":          "",
              "teams":        "",
              "other":        ""
            }
          ]
        }
      ]
    }
    """
    raw = get_schedule_raw(group)
    if raw is None:
        return {"status": "error", "message": f"Не удалось получить расписание группы '{group}'"}
    result = _build_output(raw, week)
    result["type"] = "group"
    return result


def get_teacher_schedule(teacher_name: str, week: int = 0) -> dict:
    """
    Расписание преподавателя на заданную неделю.

    Параметры:
        teacher_name – полное ФИО из get_teachers()
        week         – смещение: 0=текущая, 1=следующая, -1=прошлая

    Возвращает: тот же формат что и get_group_schedule()
    """
    raw = get_schedule_raw(teacher_name)
    if raw is None:
        return {"status": "error", "message": f"Не удалось получить расписание преподавателя '{teacher_name}'"}
    result = _build_output(raw, week)
    result["type"] = "teacher"
    return result


def get_room_schedule(room_name: str, week: int = 0) -> dict:
    """
    Занятость аудитории на заданную неделю.
    Запрашивает /schedule/{room_name} — работает для аудиторий, у которых
    есть собственный scheduleId в системе МАИ.

    Параметры:
        room_name – название аудитории, например "3-254"
        week      – смещение недели

    Возвращает: тот же формат что и get_group_schedule()
    """
    raw = get_schedule_raw(room_name)
    if raw is None:
        return {"status": "error", "message": f"Не удалось получить расписание аудитории '{room_name}'"}
    result = _build_output(raw, week)
    result["type"] = "room"
    return result


def get_full_semester_schedule(name: str) -> dict:
    """
    Полное расписание семестра без фильтрации по неделям.
    """
    raw = get_schedule_raw(name)
    if raw is None:
        return {"status": "error", "message": f"Не удалось получить расписание '{name}'"}
    return _build_output(raw, week_offset=None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _pretty(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    import sys

    HELP = """
MAI Schedule Parser  (API: maiapp.lavafrai.ru/api/v1)

Команды:
  groups                              → список всех групп
  teachers [поиск]                    → преподаватели (с поиском по ФИО)
  group  <ГРУППА>       [смещение]    → расписание группы (0=текущая неделя)
  teacher "<ФИО>"       [смещение]    → расписание преподавателя
  room   <АУДИТОРИЯ>    [смещение]    → занятость аудитории
  full   <ГРУППА/ФИО>               → весь семестр

  смещение: 0=текущая, 1=следующая, -1=прошлая (по умолчанию 0)

Примеры:
  python mai_schedule_parser.py group М3О-505С-21
  python mai_schedule_parser.py group М8О-208Б-22 1
  python mai_schedule_parser.py teacher "Антонов Дмитрий Александрович"
  python mai_schedule_parser.py teachers Антонов
  python mai_schedule_parser.py room 3-254
  python mai_schedule_parser.py full М3О-505С-21
"""

    if len(sys.argv) < 2:
        print(HELP)
        sys.exit(0)

    cmd = sys.argv[1].lower()
    arg = sys.argv[2] if len(sys.argv) >= 3 else ""
    week = int(sys.argv[3]) if len(sys.argv) >= 4 else 0

    if   cmd == "groups":                    print(_pretty(get_groups()))
    elif cmd == "teachers":                  print(_pretty(get_teachers(arg)))
    elif cmd == "group"   and arg:           print(_pretty(get_group_schedule(arg, week)))
    elif cmd == "teacher" and arg:           print(_pretty(get_teacher_schedule(arg, week)))
    elif cmd == "room"    and arg:           print(_pretty(get_room_schedule(arg, week)))
    elif cmd == "full"    and arg:           print(_pretty(get_full_semester_schedule(arg)))
    else:
        print(f"Неизвестная команда. Запустите без аргументов для справки.")
        sys.exit(1)
