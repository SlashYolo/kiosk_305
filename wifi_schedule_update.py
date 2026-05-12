#!/usr/bin/env python3
"""
wifi_schedule_update.py — Плановое обновление в 10:00 через Wi-Fi
==================================================================
Cron (10:00 каждый день):
  0 10 * * * python3 ~/mai-kiosk/wifi_schedule_update.py >> ~/mai-kiosk/update.log 2>&1

Первичная настройка (один раз):
  python3 wifi_schedule_update.py --setup
"""

import os, sys, json, time, subprocess, logging, hashlib, base64
from pathlib import Path
from datetime import datetime

KIOSK_DIR  = Path(__file__).parent
CREDS_FILE = KIOSK_DIR / '.wifi_creds'
LOG_FMT    = '%(asctime)s [WiFi] %(message)s'

logging.basicConfig(level=logging.INFO, format=LOG_FMT,
                    datefmt='%H:%M:%S', handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger('wifi')


# ── Шифрование credentials ───────────────────────────────────────
def _machine_key() -> bytes:
    mid = ''
    for p in ['/etc/machine-id', '/var/lib/dbus/machine-id']:
        try: mid = Path(p).read_text().strip(); break
        except FileNotFoundError: pass
    if not mid: mid = 'mai-kiosk-305-fallback'
    return base64.urlsafe_b64encode(hashlib.sha256(mid.encode()).digest())

def encrypt_creds(ssid: str, password: str):
    try: from cryptography.fernet import Fernet
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'cryptography', '-q'])
        from cryptography.fernet import Fernet
    enc = Fernet(_machine_key()).encrypt(json.dumps({'ssid': ssid, 'password': password}).encode())
    CREDS_FILE.write_bytes(enc)
    CREDS_FILE.chmod(0o600)
    log.info("Credentials сохранены: %s", CREDS_FILE)

def decrypt_creds() -> dict:
    from cryptography.fernet import Fernet
    return json.loads(Fernet(_machine_key()).decrypt(CREDS_FILE.read_bytes()))


# ── Wi-Fi управление ─────────────────────────────────────────────
def wifi_connect(ssid: str, pwd: str) -> bool:
    log.info("Подключаемся к Wi-Fi: %s", ssid)
    for args in [['nmcli', 'connection', 'up', ssid],
                 ['nmcli', 'device', 'wifi', 'connect', ssid, 'password', pwd]]:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                log.info("✓ Подключено")
                return True
        except Exception: pass
    log.warning("✗ Не удалось подключиться к %s", ssid)
    return False

def wifi_disconnect(ssid: str):
    try:
        subprocess.run(['nmcli', 'connection', 'down', ssid],
                       capture_output=True, timeout=10)
        log.info("✓ Отключено от %s", ssid)
    except Exception as e:
        log.warning("Отключение: %s", e)

def wait_for_network(timeout=15) -> bool:
    import requests
    for _ in range(timeout):
        try:
            requests.get('https://maiapp.lavafrai.ru', timeout=3)
            return True
        except: time.sleep(1)
    return False


# ── Основной поток ───────────────────────────────────────────────
def run_update():
    log.info("=" * 55)
    log.info("MAI Kiosk — плановое обновление [SCHEDULED-10:00]")
    log.info("=" * 55)

    if not CREDS_FILE.exists():
        log.error("Файл credentials не найден: %s", CREDS_FILE)
        log.error("Запустите: python3 %s --setup", __file__)
        sys.exit(1)

    creds = decrypt_creds()
    ssid, pwd = creds['ssid'], creds['password']

    connected = wifi_connect(ssid, pwd)
    if connected and not wait_for_network():
        log.warning("Сеть недоступна после подключения")
        wifi_disconnect(ssid)
        return

    # Запускаем полное сохранение расписания
    log.info("Запускаем cache_schedule.py...")
    cache_script = KIOSK_DIR / 'cache_schedule.py'
    subprocess.run([sys.executable, str(cache_script)], check=False)

    if connected:
        wifi_disconnect(ssid)

    log.info("Пауза 5 минут...")
    time.sleep(300)
    log.info("Готово.")


if __name__ == '__main__':
    if '--setup' in sys.argv:
        print("═══ Настройка Wi-Fi credentials ═══")
        ssid = input("SSID: ").strip()
        pwd  = input("Пароль: ").strip()
        encrypt_creds(ssid, pwd)
        print(f"\ncrontab -e → добавить строку:")
        print(f"0 10 * * * python3 {Path(__file__).resolve()} >> {KIOSK_DIR}/update.log 2>&1")
    else:
        run_update()
