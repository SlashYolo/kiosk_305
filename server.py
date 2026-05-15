"""
MAI Kiosk — локальный прокси-сервер v3
========================================
Запуск: python3 server.py   →   http://localhost:8765
"""
import sys, os, json, logging, time, re
from pathlib import Path
from urllib.parse import quote

KIOSK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(KIOSK_DIR))

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import requests as req_lib

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
log = logging.getLogger(__name__)

API       = "https://maiapp.lavafrai.ru/api/v1"
CACHE     = KIOSK_DIR / 'schedule_cache'
PUBS      = KIOSK_DIR / 'pubs_cache'
CACHE.mkdir(exist_ok=True)
PUBS.mkdir(exist_ok=True)

# Сессия для API расписания
SESSION = req_lib.Session()
SESSION.headers.update({"User-Agent": "MAI-Kiosk/3.0", "Accept": "application/json"})

# Отдельная сессия для HTML-страниц МАИ (браузерные заголовки!)
HTML_SES = req_lib.Session()
HTML_SES.headers.update({
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer":         "https://mai.ru/",
})


# ── Утилиты ──────────────────────────────────────────────────────

def safe_name(name: str) -> str:
    return name.replace(' ', '_').replace('/', '-').replace('\\', '-')[:80]

def fetch_api(path: str, timeout=15):
    url = f"{API}/{path.lstrip('/')}"
    log.info("→ %s", url)
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json(), 200
    except Exception as e:
        log.warning("API error: %s", e)
        return None, 502

def read_disk_cache(entity_type: str, name: str):
    path = CACHE / f"{entity_type}_{safe_name(name)}.json"
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding='utf-8'))
            return d.get('data'), True
        except Exception:
            pass
    return None, False

def write_disk_cache(name: str, data: dict):
    days  = data.get('days', [])
    etype = 'group'
    if days and days[0].get('lessons'):
        grps = days[0]['lessons'][0].get('groups', [])
        if grps:
            etype = 'teacher'
    path = CACHE / f"{etype}_{safe_name(name)}.json"
    try:
        path.write_text(
            json.dumps({'type': etype, 'name': name, 'data': data, 'ts': time.time()},
                       ensure_ascii=False),
            encoding='utf-8')
    except Exception as e:
        log.warning("Cache write error: %s", e)

def iter_cache_index(etype: str):
    prefix = f"{etype}_" if etype else ""
    for path in sorted(CACHE.glob(f"{prefix}*.json")):
        if path.name == 'last_update.json':
            continue
        try:
            d = json.loads(path.read_text(encoding='utf-8'))
            yield {"name": d.get('name', ''), "type": d.get('type', etype)}
        except Exception:
            continue


# ── Парсер публикаций ─────────────────────────────────────────────

def parse_pubs(html_text: str) -> list:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, 'html.parser')
    pubs = []

    # Структура реальная: <table class='data-table'><tr><th colspan='2'>Публикации</th></tr>...
    pub_table = None
    for table in soup.find_all('table'):
        # Ищем "Публикации" в <th> ИЛИ в <td> первой строки
        header = table.find(['th', 'td'])
        if header and 'Публикации' in header.get_text():
            pub_table = table
            log.info("parse_pubs: found publications table (tag=%s)", header.name)
            break

    if not pub_table:
        # Запасной: ищем все ссылки elibrary/scopus напрямую
        log.warning("parse_pubs: table not found, using links fallback")
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            if 'elibrary.ru' not in href and 'scopus.com' not in href:
                continue
            text = a.get_text(strip=True)
            if not text or 'Показать' in text:
                continue
            year = None
            m = re.search(r'\b(19|20)\d{2}\b', text)
            if m: year = int(m.group())
            has_cyr = bool(re.search(r'[а-яёА-ЯЁ]', text))
            pubs.append({'title': text[:300], 'journal': '', 'year': year,
                         'url': href, 'lang': 'ru' if has_cyr else 'en'})
        pubs.sort(key=lambda p: p['year'] or 0, reverse=True)
        log.info("parse_pubs fallback: %d pubs", len(pubs))
        return pubs

    for row in pub_table.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) < 2:
            continue
        a = cells[1].find('a')
        if not a:
            continue
        text = a.get_text(strip=True)
        if not text or 'Показать' in text:
            continue
        href = a.get('href', '')
        if href in ('<>', '', '#', None):
            href = ''

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

    log.info("parse_pubs: extracted %d publications (before dedup)", len(pubs))

    # ── Дедупликация ─────────────────────────────────────────────
    # МАИ дублирует: одна запись со ссылкой, другая без (href='<>')
    # Ключ = первые 50 нормализованных символов + год
    import unicodedata

    def norm_key(p: dict) -> str:
        t = unicodedata.normalize('NFC', p['title'].lower())
        t = re.sub(r'[^а-яёa-z0-9]', '', t)
        return f"{t[:50]}_{p['year'] or 0}"

    seen: dict[str, int] = {}
    deduped = []
    for p in pubs:
        key = norm_key(p)
        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(p)
        else:
            # Есть дубль — оставляем вариант с реальным URL
            idx = seen[key]
            if p['url'] and not deduped[idx]['url']:
                deduped[idx] = p

    pubs = deduped
    log.info("parse_pubs: %d after deduplication", len(pubs))
    # ─────────────────────────────────────────────────────────────

    pubs.sort(key=lambda p: p['year'] or 0, reverse=True)
    return pubs

# ── Эндпоинты ────────────────────────────────────────────────────

@app.route('/api/groups')
def groups():
    data, code = fetch_api('/groups')
    if data is not None:
        return jsonify(data), code
    names = [f['name'] for f in iter_cache_index('group')]
    return jsonify(names) if names else (jsonify({"error": "unavailable"}), 502)


@app.route('/api/teachers')
def teachers():
    data, code = fetch_api('/teachers')
    if data is not None:
        return jsonify(data), code
    names = [f['name'] for f in iter_cache_index('teacher')]
    return jsonify(names) if names else (jsonify({"error": "unavailable"}), 502)


@app.route('/api/schedule/<path:name>')
def schedule(name):
    data, code = fetch_api(f'/schedule/{quote(name, safe="")}')
    if data is not None:
        write_disk_cache(name, data)
        return jsonify(data), code
    for etype in ('group', 'teacher'):
        cached, ok = read_disk_cache(etype, name)
        if ok and cached is not None:
            return jsonify(cached)
    return jsonify({"error": f"Расписание для '{name}' недоступно"}), 404


@app.route('/api/teacher-pubs/<guid>')
def teacher_pubs(guid):
    """Публикации преподавателя: disk cache (только непустой) → mai.ru."""
    cache_file = PUBS / f"{guid}.json"

    # Читаем из disk cache — ТОЛЬКО если там есть публикации (total > 0)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding='utf-8'))
            if cached.get('total', 0) > 0:
                log.info("Pubs from cache: %s (%d)", guid, cached['total'])
                return jsonify(cached)
            else:
                log.info("Pubs cache empty for %s — refetching", guid)
                cache_file.unlink()  # удаляем пустой кэш чтобы переспросить
        except Exception:
            pass

    # Загружаем с mai.ru
    url = f"https://mai.ru/education/studies/schedule/ppc.php?guid={guid}"
    try:
        r = HTML_SES.get(url, timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or 'utf-8'
        log.info("Pubs fetched: status=%d len=%d guid=%s", r.status_code, len(r.text), guid)
    except Exception as e:
        log.warning("Pubs fetch failed %s: %s", guid, e)
        return jsonify({"error": str(e), "pubs": [], "total": 0}), 200

    pubs   = parse_pubs(r.text)
    result = {"pubs": pubs, "total": len(pubs), "guid": guid, "ts": time.time()}
    log.info("Pubs parsed: %d for %s", len(pubs), guid)

    # Сохраняем в кэш ТОЛЬКО если нашли публикации
    if pubs:
        try:
            cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            log.warning("Pubs cache write: %s", e)

    return jsonify(result)


@app.route('/api/debug-pubs/<guid>')
def debug_pubs(guid):
    """Диагностика: возвращает сырой HTML профиля преподавателя."""
    url = f"https://mai.ru/education/studies/schedule/ppc.php?guid={guid}"
    try:
        r = HTML_SES.get(url, timeout=15)
        r.encoding = r.apparent_encoding or 'utf-8'
        # Ищем таблицу публикаций в HTML
        pub_pos = r.text.find('Публикации')
        snippet = r.text[max(0, pub_pos-50):pub_pos+300] if pub_pos != -1 else 'NOT FOUND'
        tables_count = r.text.count('<table')
        return jsonify({
            "status":       r.status_code,
            "html_len":     len(r.text),
            "encoding":     r.encoding,
            "pub_found":    pub_pos != -1,
            "pub_position": pub_pos,
            "tables_count": tables_count,
            "snippet":      snippet,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route('/api/cache-names')
def cache_names():
    etype = request.args.get('type', '')
    return jsonify(list(iter_cache_index(etype)))


@app.route('/api/fullscreen-exit', methods=['POST'])
def fullscreen_exit():
    """
    Выходит из полноэкранного режима Chromium на уровне ОС.
    Работает на Lubuntu через xdotool (F11 = toggle fullscreen в Chromium).
    """
    import subprocess, os
    display = os.environ.get('DISPLAY', ':0')
    methods_tried = []

    # Способ 1: xdotool — отправляем F11 в активное окно браузера
    try:
        result = subprocess.run(
            ['xdotool', 'key', '--clearmodifiers', 'F11'],
            env={**os.environ, 'DISPLAY': display},
            capture_output=True, timeout=5
        )
        methods_tried.append(f"xdotool F11: returncode={result.returncode}")
        if result.returncode == 0:
            return jsonify({"ok": True, "method": "xdotool F11"})
    except FileNotFoundError:
        methods_tried.append("xdotool: not found")
    except Exception as e:
        methods_tried.append(f"xdotool: {e}")

    # Способ 2: wmctrl — снимаем fullscreen с активного окна
    try:
        result = subprocess.run(
            ['wmctrl', '-r', ':ACTIVE:', '-b', 'remove,fullscreen'],
            env={**os.environ, 'DISPLAY': display},
            capture_output=True, timeout=5
        )
        methods_tried.append(f"wmctrl: returncode={result.returncode}")
        if result.returncode == 0:
            return jsonify({"ok": True, "method": "wmctrl"})
    except FileNotFoundError:
        methods_tried.append("wmctrl: not found")
    except Exception as e:
        methods_tried.append(f"wmctrl: {e}")

    return jsonify({"ok": False, "tried": methods_tried}), 200


@app.route('/api/health')
def health():
    """Лёгкий healthcheck. Фронт пингует каждые 5с, mai-kiosk.sh ждёт при старте."""
    return jsonify({"status": "ok", "ts": time.time()})


@app.route('/api/cache-status')
def cache_status():
    f = CACHE / 'last_update.json'
    if f.exists():
        try:
            return jsonify(json.loads(f.read_text(encoding='utf-8')))
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "cache not found"}), 404


@app.route('/')
def index():
    kiosk = KIOSK_DIR / 'mai_kiosk.html'
    if kiosk.exists():
        return send_from_directory(str(KIOSK_DIR), 'mai_kiosk.html')
    return "<h2>Положи mai_kiosk.html рядом с server.py</h2>", 404


@app.route('/on.mp4')
def afk_video():
    """AFK-видео для заставки. conditional=True → поддержка Range-запросов,
    нужна для плавного looping <video> в браузере."""
    f = KIOSK_DIR / 'on.mp4'
    if not f.exists():
        return jsonify({"error": "on.mp4 not found"}), 404
    return send_from_directory(str(KIOSK_DIR), 'on.mp4', conditional=True)


@app.route('/lab-photos/<path:filename>')
def lab_photos(filename):
    """Раздаёт фото лабораторий из папки lab-photos/ рядом с server.py.
    Если файла нет — 404, фронт автоматически покажет заглушку."""
    folder = KIOSK_DIR / 'lab-photos'
    if not folder.exists() or not (folder / filename).exists():
        return jsonify({"error": "photo not found"}), 404
    return send_from_directory(str(folder), filename, conditional=True)


@app.route('/avatars_teachers/<path:filename>')
def teacher_avatars(filename):
    """Раздаёт аватарки преподавателей из папки avatars_teachers/.
    Если файла нет — фронт сам подставит кружок с инициалами."""
    folder = KIOSK_DIR / 'avatars_teachers'
    if not folder.exists() or not (folder / filename).exists():
        return jsonify({"error": "avatar not found"}), 404
    return send_from_directory(str(folder), filename, conditional=True)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8765))
    print(f"\n╔══════════════════╗\n║ localhost:{port} ║\n╚══════════════════╝\n")
    app.run(host='0.0.0.0', port=port, debug=False)