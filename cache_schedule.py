#!/usr/bin/env python3
"""













"""

import sys
import json
import time
import requests
import logging
from pathlib import Path
from urllib.parse import quote
from datetime import datetime

# ── Настройка ────────────────────────────────────────────────────
KIOSK_DIR = Path(__file__).resolve().parent
CACHE_DIR = KIOSK_DIR / 'schedule_cache'
API = 'https://maiapp.lavafrai.ru/api/v1'
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'MAI-Kiosk-Cache/1.0', 'Accept': 'application/json'})

CACHE_DIR.mkdir(exist_ok=True)

# ── Логирование в консоль ────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CACHE] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('cache')

# ── Режим запуска ────────────────────────────────────────────────
STARTUP_MODE = '--startup' in sys.argv
mode_label   = 'STARTUP' if STARTUP_MODE else 'SCHEDULED-10:00'


import signal

# ── Флаг остановки (Ctrl+C / SIGTERM от start.sh) ────────────────
_stop = False

def _handle_stop(signum, frame):
    global _stop
    log.info("Получен сигнал %d — завершаем после текущей записи...", signum)
    _stop = True

signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT,  _handle_stop)


def safe_filename(name: str) -> str:
    """Безопасное имя файла из строки."""
    return name.replace(' ', '_').replace('/', '-').replace('\\', '-')[:80]


def fetch_and_save(entity_type: str, name: str, retries: int = 2) -> bool:
    """Скачать расписание с ретраями и сохранить/перезаписать JSON."""
    for attempt in range(retries + 1):
        try:
            url = f"{API}/schedule/{quote(name, safe='')}"
            r = SESSION.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()

            filename = f"{entity_type}_{safe_filename(name)}.json"
            path = CACHE_DIR / filename
            path.write_text(
                json.dumps({
                    'type':    entity_type,
                    'name':    name,
                    'data':    data,
                    'ts':      time.time(),
                    'updated': datetime.now().isoformat(),
                }, ensure_ascii=False),
                encoding='utf-8'
            )
            return True
        except Exception as e:
            if attempt < retries:
                time.sleep(1 + attempt)  # 1s, 2s между попытками
            else:
                log.warning("  FAIL %s '%s' (after %d attempts): %s", entity_type, name, retries+1, e)
    return False


def load_groups() -> list[str]:
    """Загрузить список всех групп."""
    try:
        r = SESSION.get(f"{API}/groups", timeout=20)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return [g['name'] if isinstance(g, dict) else g for g in data]
        return []
    except Exception as e:
        log.error("Не удалось загрузить список групп: %s", e)
        return []


def load_teachers() -> list[str]:
    """Загрузить список всех преподавателей."""
    try:
        r = SESSION.get(f"{API}/teachers", timeout=20)
        r.raise_for_status()
        data = r.json()
        names = []
        for t in (data if isinstance(data, list) else []):
            if isinstance(t, dict):
                n = t.get('name', '')
                name = n.get('name', '') if isinstance(n, dict) else str(n)
            else:
                name = str(t)
            if name.strip():
                names.append(name.strip())
        return names
    except Exception as e:
        log.error("Не удалось загрузить список преподавателей: %s", e)
        return []


def run():
    start_ts = time.time()
    log.info("=" * 55)
    log.info("MAI Kiosk — сохранение расписания [%s]", mode_label)
    log.info("=" * 55)

    # ── Шаг 1: Группы ───────────────────────────────────────────
    log.info("Загружаем список групп...")
    groups = load_groups()
    log.info("  Найдено групп: %d", len(groups))

    ok_groups = 0
    for i, name in enumerate(groups, 1):
        if _stop:
            log.info("Остановка по сигналу на группе %d/%d", i, len(groups))
            break
        result = fetch_and_save('group', name)
        if result:
            ok_groups += 1
        if i % 50 == 0 or i == len(groups):
            log.info("  Группы: %d/%d сохранено (%.0f%%)",
                     ok_groups, i, ok_groups / i * 100)
        if i % 10 == 0:
            time.sleep(0.3)

    log.info("✓ Группы: %d/%d", ok_groups, len(groups))

    # ── Шаг 2: Преподаватели ────────────────────────────────────
    log.info("Загружаем список преподавателей...")
    teachers = load_teachers()
    log.info("  Найдено преподавателей: %d", len(teachers))

    ok_teachers = 0
    for i, name in enumerate(teachers, 1):
        if _stop:
            log.info("Остановка по сигналу на преподавателе %d/%d", i, len(teachers))
            break
        result = fetch_and_save('teacher', name)
        if result:
            ok_teachers += 1
        if i % 20 == 0 or i == len(teachers):
            log.info("  Преподаватели: %d/%d сохранено", ok_teachers, i)
        if i % 10 == 0:
            time.sleep(0.3)

    log.info("✓ Преподаватели: %d/%d", ok_teachers, len(teachers))

    # ── Финал ───────────────────────────────────────────────────
    elapsed = time.time() - start_ts
    summary = {
        'mode':         mode_label,
        'ts':           time.time(),
        'updated':      datetime.now().isoformat(),
        'groups_ok':    ok_groups,
        'groups_total': len(groups),
        'teachers_ok':  ok_teachers,
        'teachers_total': len(teachers),
        'elapsed_sec':  round(elapsed, 1),
    }
    (CACHE_DIR / 'last_update.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    log.info("=" * 55)
    log.info("ИТОГ: группы %d/%d, преподаватели %d/%d, за %.0f сек",
             ok_groups, len(groups), ok_teachers, len(teachers), elapsed)
    log.info("Кэш сохранён в: %s", CACHE_DIR)
    log.info("=" * 55)


if __name__ == '__main__':
    run()


# ════════════════════════════════════════════════════════
# КЭШИРОВАНИЕ ПУБЛИКАЦИЙ ПРЕПОДАВАТЕЛЕЙ
# ════════════════════════════════════════════════════════

TEACHERS_GUIDS = [
    ('Алешин Борис Сергеевич',              'b0062582-1d99-11e0-9baf-1c6f65450efa'),
    ('Афонин Александр Анатольевич',        'b0062590-1d99-11e0-9baf-1c6f65450efa'),
    ('Веремеенко Константин Константинович','8062e196-1d99-11e0-9baf-1c6f65450efa'),
    ('Кошелев Борис Валентинович',          '578c17f8-1d99-11e0-9baf-1c6f65450efa'),
    ('Антонов Дмитрий Александрович',       'eb7d5e30-1d99-11e0-9baf-1c6f65450efa'),
    ('Нгуен Ныы Ман',                       '05816da9-3656-11e3-9343-3cd92bf20bfe'),
    ('Сурков Дмитрий Александрович',        'e4a9bc9c-1d99-11e0-9baf-1c6f65450efa'),
    ('Жарков Максим Витальевич',            'f253618c-1d99-11e0-9baf-1c6f65450efa'),
    ('Кузнецов Иван Михайлович',            '28e11301-1d9b-11e0-9baf-1c6f65450efa'),
    ('Лельков Константин Сергеевич',        '95d76d89-77f8-11e1-86e1-00304866c649'),
    ('Мишин Юрий Николаевич',               '1847ff04-03c3-11e2-9a50-3cd92bf20bff'),
    ('Петрухин Владимир Андреевич',         '2f38b9d1-1d9b-11e0-9baf-1c6f65450efa'),
    ('Пронькин Андрей Николаевич',          'd6097880-1d9a-11e0-9baf-1c6f65450efa'),
    ('Рябинкин Максим Сергеевич',           'ae2912f4-a1e8-11e7-b412-485b3919ee6d'),
    ('Савкин Алексей Владимирович',         '2aee59b5-f63c-11e9-9246-485b3919ee6d'),
    ('Ушаков Андрей Николаевич',            '90d920a5-1d9a-11e0-9baf-1c6f65450efa'),
    ('Хорев Тимофей Сергеевич',             '2f38b9ce-1d9b-11e0-9baf-1c6f65450efa'),
    ('Колганов Леонид Александрович',       '0e0fb874-4055-11e7-b024-3cd92bf20bfe'),
    ('Калинина Ольга Игоревна',             'd4a78fa7-5606-11ed-bbea-ac1f6b64c5eb'),
    ('Матюшенко Роман Викторович',          '0ad2ae84-0f4d-11ee-bc01-3cecef1c132f'),
    ('Рычков Александр Сергеевич',          '1b1b73fa-2e23-11ef-bc19-3cecef1c132f'),
    ('Учаева Екатерина Александровна',      '5ab9e990-9147-11ef-bc24-3cecef1c132f'),
]

HTML_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer":         "https://mai.ru/",
}

def cache_publications():
    """Кэшировать публикации всех преподавателей кафедры 305."""
    import re
    from bs4 import BeautifulSoup

    pubs_dir = KIOSK_DIR / 'pubs_cache'
    pubs_dir.mkdir(exist_ok=True)

    html_sess = requests.Session()
    html_sess.headers.update(HTML_HEADERS)

    log.info("── Публикации преподавателей (%d чел.) ──", len(TEACHERS_GUIDS))
    ok = 0
    for name, guid in TEACHERS_GUIDS:
        try:
            url = f"https://mai.ru/education/studies/schedule/ppc.php?guid={guid}"
            r = html_sess.get(url, timeout=15)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or 'utf-8'

            soup = BeautifulSoup(r.text, 'html.parser')
            pubs = []
            for table in soup.find_all('table'):
                # Ищем в <th> ИЛИ в <td> — на сайте МАИ заголовок в <th>
                first_cell = table.find(['th', 'td'])
                if not first_cell or 'Публикации' not in first_cell.get_text():
                    continue
                for row in table.find_all('tr'):
                    cells = row.find_all('td')
                    if len(cells) < 2: continue
                    a = cells[1].find('a')
                    if not a: continue
                    text = a.get_text(strip=True)
                    if not text or 'Показать' in text: continue
                    href = a.get('href', '')
                    if href in ('<>', '', '#', None): href = ''
                    year = None
                    m = re.search(r'\b(19|20)\d{2}\b', text)
                    if m: year = int(m.group())
                    if '//' in text:
                        parts = text.split('//', 1)
                        title   = parts[0].strip().rstrip('.,')
                        journal = re.sub(r'\.\s*-\s*(19|20)\d{2}.*', '', parts[1]).strip()
                    else:
                        title, journal = text.strip(), ''
                    has_cyr = bool(re.search(r'[а-яёА-ЯЁ]', title))
                    pubs.append({'title': title, 'journal': journal,
                                 'year': year, 'url': href,
                                 'lang': 'ru' if has_cyr else 'en'})
                break
            pubs.sort(key=lambda p: p['year'] or 0, reverse=True)

            result = {"pubs": pubs, "total": len(pubs), "guid": guid, "ts": time.time()}
            (pubs_dir / f"{guid}.json").write_text(
                json.dumps(result, ensure_ascii=False), encoding='utf-8')
            log.info("  ✓ %s: %d публикаций", name, len(pubs))
            ok += 1
            time.sleep(0.5)  # пауза между запросами
        except Exception as e:
            log.warning("  ✗ %s: %s", name, e)

    log.info("Публикации: %d/%d кэшировано", ok, len(TEACHERS_GUIDS))


# Добавляем кэш публикаций в основной run()
_original_run = run

def run():
    _original_run()
    log.info("")
    cache_publications()

if __name__ == '__main__':
    run()
