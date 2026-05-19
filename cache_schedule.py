#!/usr/bin/env python3
"""
cache_schedule.py — полное сохранение расписания МАИ + публикаций кафедры 305
=============================================================================

Запуск:
  python3 cache_schedule.py                          # последовательный
  python3 cache_schedule.py --parallel 5             # 5 параллельных запросов
  python3 cache_schedule.py --startup                # метка STARTUP в логах
  python3 cache_schedule.py --startup --parallel 5   # рекомендуемый стартап

Изменения по сравнению с прошлой версией:
  • ThreadPoolExecutor — параллельные HTTP-запросы (5x-10x по скорости).
  • SHA1-хэш ответа сравнивается с уже сохранённым — если контент не менялся,
    файл не перезаписываем (counter "skipped"). Экономит диск.
  • Прогресс пишется в schedule_cache/_progress.json после каждого батча
    (фронт читает через /api/cache-progress для плашки на странице).
  • Атомарная запись прогресс-файла (tmp + rename).
"""

import sys, os, json, time, hashlib, signal, logging, threading
from pathlib import Path
from urllib.parse import quote
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# ── Конфиг ───────────────────────────────────────────────────────
KIOSK_DIR     = Path(__file__).resolve().parent
CACHE_DIR     = KIOSK_DIR / 'schedule_cache'
PUBS_DIR      = KIOSK_DIR / 'pubs_cache'
PROGRESS_FILE = CACHE_DIR / '_progress.json'
API           = 'https://maiapp.lavafrai.ru/api/v1'

CACHE_DIR.mkdir(exist_ok=True)
PUBS_DIR.mkdir(exist_ok=True)

# ── Аргументы ────────────────────────────────────────────────────
STARTUP_MODE     = '--startup' in sys.argv
PARALLEL_WORKERS = 1
if '--parallel' in sys.argv:
    try:
        idx = sys.argv.index('--parallel')
        PARALLEL_WORKERS = max(1, min(20, int(sys.argv[idx + 1])))
    except (IndexError, ValueError):
        PARALLEL_WORKERS = 5
elif STARTUP_MODE:
    PARALLEL_WORKERS = 5  # для стартапа дефолтно параллелим

mode_label = 'STARTUP' if STARTUP_MODE else 'SCHEDULED-10:00'

# ── Логи ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CACHE] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('cache')

# ── Грейсфул-стоп ────────────────────────────────────────────────
_stop = threading.Event()
def _handle_stop(signum, frame):
    log.info("Получен сигнал %d — останавливаемся...", signum)
    _stop.set()
signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT,  _handle_stop)


# ── Прогресс ─────────────────────────────────────────────────────
_progress = {
    'in_progress':      True,
    'mode':             mode_label,
    'phase':            'init',
    'parallel_workers': PARALLEL_WORKERS,
    'started':          time.time(),
    'started_iso':      datetime.now().isoformat(),
    'groups':           {'current': 0, 'total': 0, 'saved': 0, 'skipped': 0, 'failed': 0},
    'teachers':         {'current': 0, 'total': 0, 'saved': 0, 'skipped': 0, 'failed': 0},
    'pubs':             {'current': 0, 'total': 0, 'saved': 0, 'skipped': 0, 'failed': 0},
}
_progress_lock = threading.Lock()

def write_progress():
    """Атомарная запись (tmp + rename). При параллели последний пишущий выиграет — это ок."""
    try:
        tmp = PROGRESS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(_progress, ensure_ascii=False), encoding='utf-8')
        tmp.replace(PROGRESS_FILE)
    except Exception as e:
        log.warning("Не удалось записать прогресс: %s", e)

def bump(phase, key):
    with _progress_lock:
        _progress[phase][key] += 1


# ── HTTP ─────────────────────────────────────────────────────────
def make_session():
    s = requests.Session()
    s.headers.update({'User-Agent': 'MAI-Kiosk-Cache/2.0', 'Accept': 'application/json'})
    return s

SESSION = make_session()

def safe_filename(name: str) -> str:
    return name.replace(' ', '_').replace('/', '-').replace('\\', '-')[:80]

def data_hash(obj) -> str:
    """SHA1 от компактного отсортированного JSON. Стабилен по содержимому.
    Перед хэшированием рекурсивно вычищаем 'шумные' поля (timestamps, IDs запроса),
    чтобы одни и те же данные не давали разный хэш из-за серверных метаданных."""
    cleaned = _strip_noise(obj)
    return hashlib.sha1(
        json.dumps(cleaned, sort_keys=True, ensure_ascii=False).encode('utf-8')
    ).hexdigest()


# Поля которые API может менять между запросами при неизменном контенте
_NOISE_KEYS = frozenset({
    'ts', 'timestamp', 'updated', 'updated_at', 'generated', 'generated_at',
    'cached_at', 'request_id', 'fetched_at', 'last_modified', 'etag',
})

def _strip_noise(obj):
    """Рекурсивно удаляет timestamp/служебные поля из dict/list для стабильного хэша."""
    if isinstance(obj, dict):
        return {k: _strip_noise(v) for k, v in obj.items() if k not in _NOISE_KEYS}
    if isinstance(obj, list):
        return [_strip_noise(x) for x in obj]
    return obj


def fetch_and_save(entity_type: str, name: str, session: requests.Session, retries: int = 2) -> str:
    """
    Возвращает: 'saved' | 'skipped' | 'failed'.
    Хэш считается от СЫРЫХ байтов HTTP-ответа — без парсинга JSON.
    При совпадении хэша: ни парсинг, ни запись не происходят (макс. скорость).
    """
    for attempt in range(retries + 1):
        if _stop.is_set():
            return 'failed'
        try:
            url = f"{API}/schedule/{quote(name, safe='')}"
            r = session.get(url, timeout=15)
            r.raise_for_status()

            # Хэш сырых байтов — без JSON-парсинга
            raw_hash = hashlib.sha1(r.content).hexdigest()

            filename = f"{entity_type}_{safe_filename(name)}.json"
            path = CACHE_DIR / filename

            # Быстрая проверка: ищем хэш в начале файла (там поле "hash" первое)
            if path.exists():
                try:
                    head = path.read_bytes()[:300]
                    if raw_hash.encode() in head:
                        return 'skipped'
                except Exception:
                    pass

            # Хэш не совпал — парсим и сохраняем
            data = r.json()
            path.write_text(
                json.dumps({
                    'hash':    raw_hash,
                    'type':    entity_type,
                    'name':    name,
                    'data':    data,
                    'ts':      time.time(),
                    'updated': datetime.now().isoformat(),
                }, ensure_ascii=False),
                encoding='utf-8'
            )
            return 'saved'
        except Exception as e:
            if attempt < retries:
                time.sleep(0.5 + attempt * 0.5)
            else:
                log.warning("  FAIL %s '%s' (after %d attempts): %s", entity_type, name, retries + 1, e)
    return 'failed'


def load_groups() -> list:
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


def load_teachers() -> list:
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


def process_batch(phase: str, names: list, entity_type: str):
    """Обработать массив имён — параллельно или последовательно."""
    _progress[phase]['total'] = len(names)
    _progress['phase'] = phase
    write_progress()

    # requests.Session не полностью thread-safe, у каждого потока своя
    thread_local = threading.local()
    def get_session():
        if not hasattr(thread_local, 's'):
            thread_local.s = make_session()
        return thread_local.s

    def worker(name):
        result = fetch_and_save(entity_type, name, get_session())
        bump(phase, result)
        with _progress_lock:
            _progress[phase]['current'] += 1
        return result

    if PARALLEL_WORKERS == 1:
        for i, name in enumerate(names, 1):
            if _stop.is_set(): break
            worker(name)
            if i % 10 == 0:
                write_progress()
            if i % 50 == 0 or i == len(names):
                p = _progress[phase]
                log.info("  %s: %d/%d (saved=%d skipped=%d failed=%d)",
                         phase, p['current'], p['total'], p['saved'], p['skipped'], p['failed'])
    else:
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
            futures = [ex.submit(worker, name) for name in names]
            done = 0
            for fut in as_completed(futures):
                if _stop.is_set():
                    for f in futures:
                        f.cancel()
                    break
                done += 1
                if done % 20 == 0:
                    write_progress()
                if done % 100 == 0 or done == len(names):
                    p = _progress[phase]
                    log.info("  %s: %d/%d (saved=%d skipped=%d failed=%d)",
                             phase, p['current'], p['total'], p['saved'], p['skipped'], p['failed'])

    write_progress()


# ── Публикации преподавателей ────────────────────────────────────
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
    """Публикации — последовательно. Их всего 22, и mai.ru агрессивнее к параллельным запросам."""
    import re
    from bs4 import BeautifulSoup

    html_sess = requests.Session()
    html_sess.headers.update(HTML_HEADERS)

    _progress['phase'] = 'pubs'
    _progress['pubs']['total'] = len(TEACHERS_GUIDS)
    write_progress()

    log.info("── Публикации преподавателей (%d чел.) ──", len(TEACHERS_GUIDS))

    for name, guid in TEACHERS_GUIDS:
        if _stop.is_set(): break
        try:
            url = f"https://mai.ru/education/studies/schedule/ppc.php?guid={guid}"
            r = html_sess.get(url, timeout=15)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or 'utf-8'

            soup = BeautifulSoup(r.text, 'html.parser')
            pubs = []
            for table in soup.find_all('table'):
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

            new_hash = data_hash(pubs)
            file_path = PUBS_DIR / f"{guid}.json"
            result_status = 'saved'

            if file_path.exists():
                try:
                    old = json.loads(file_path.read_text(encoding='utf-8'))
                    if old.get('hash') == new_hash:
                        result_status = 'skipped'
                except Exception: pass

            if result_status == 'saved':
                result = {"pubs": pubs, "total": len(pubs), "guid": guid,
                          "hash": new_hash, "ts": time.time()}
                file_path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')

            bump('pubs', result_status)
            tag = '✓' if result_status == 'saved' else '○'
            log.info("  %s %s: %d публикаций (%s)", tag, name, len(pubs), result_status)
            time.sleep(0.5)
        except Exception as e:
            bump('pubs', 'failed')
            log.warning("  ✗ %s: %s", name, e)
        finally:
            with _progress_lock:
                _progress['pubs']['current'] += 1
            write_progress()


def run():
    start_ts = time.time()
    log.info("=" * 55)
    log.info("MAI Kiosk — кэш [%s, parallel=%d]", mode_label, PARALLEL_WORKERS)
    log.info("=" * 55)
    write_progress()

    try:
        # Группы
        log.info("Загружаем список групп...")
        groups = load_groups()
        log.info("  Найдено групп: %d", len(groups))
        if groups and not _stop.is_set():
            process_batch('groups', groups, 'group')

        # Преподаватели
        if not _stop.is_set():
            log.info("Загружаем список преподавателей...")
            teachers = load_teachers()
            log.info("  Найдено: %d", len(teachers))
            if teachers:
                process_batch('teachers', teachers, 'teacher')

        # Публикации
        if not _stop.is_set():
            cache_publications()
    except Exception as e:
        log.error("КРИТИЧЕСКАЯ ОШИБКА: %s", e, exc_info=True)
    finally:
        # ВСЕГДА пишем финальный статус — даже при крэше / KeyboardInterrupt
        elapsed = time.time() - start_ts
        _progress['in_progress']  = False
        _progress['phase']        = 'done' if not _stop.is_set() else 'aborted'
        _progress['elapsed_sec']  = round(elapsed, 1)
        _progress['finished']     = time.time()
        _progress['finished_iso'] = datetime.now().isoformat()
        write_progress()

        (CACHE_DIR / 'last_update.json').write_text(
            json.dumps(_progress, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

        g, t, p = _progress['groups'], _progress['teachers'], _progress['pubs']
        log.info("=" * 55)
        log.info("ИТОГ за %.0f сек:", elapsed)
        log.info("  Группы:        saved=%d skipped=%d failed=%d (всего %d)",
                 g['saved'], g['skipped'], g['failed'], g['total'])
        log.info("  Преподаватели: saved=%d skipped=%d failed=%d (всего %d)",
                 t['saved'], t['skipped'], t['failed'], t['total'])
        log.info("  Публикации:    saved=%d skipped=%d failed=%d (всего %d)",
                 p['saved'], p['skipped'], p['failed'], p['total'])
        log.info("Кэш сохранён в: %s", CACHE_DIR)
        log.info("=" * 55)


if __name__ == '__main__':
    run()
