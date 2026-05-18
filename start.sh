#!/bin/bash
cd "$(dirname "$0")"
KIOSK_DIR="$(pwd)"

LOG_PREFIX="[$(date '+%H:%M:%S')]"

# Находим python3
PY=""
for cmd in python3 python3.12 python3.11 python3.10 python; do
  if command -v "$cmd" &>/dev/null; then
    PY="$cmd"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "❌ Python не найден."
  exit 1
fi

echo "$LOG_PREFIX ✓ Python: $($PY --version 2>&1)"

echo "$LOG_PREFIX 📦 Устанавливаем зависимости..."
$PY -m pip install flask flask-cors requests beautifulsoup4 --quiet 2>/dev/null

# ── Сервер первым делом — не ждём кэш! Юзер увидит стенд сразу ────
echo "$LOG_PREFIX 🚀 Запускаем сервер..."
$PY server.py &
SERVER_PID=$!
sleep 2

if ! kill -0 $SERVER_PID 2>/dev/null; then
  echo "$LOG_PREFIX ❌ Сервер не запустился. Запусти вручную: $PY server.py"
  exit 1
fi

# ── Автосохранение расписания — параллельно в фоне ─────────────────
# Прогресс пишется в schedule_cache/_progress.json, фронт читает и показывает
# плашку «обновляем расписание» в правом нижнем углу — стенд при этом полностью
# юзабелен через онлайн-API.
CACHE_PID=""
if [ -f "$KIOSK_DIR/.wifi_creds" ] && [ -f "$KIOSK_DIR/wifi_schedule_update.py" ]; then
  echo "$LOG_PREFIX 📡 Найден .wifi_creds — подключаемся к Wi-Fi и кэшируем (фоном)"
  $PY "$KIOSK_DIR/wifi_schedule_update.py" --startup &
  CACHE_PID=$!
elif curl -s --max-time 5 "https://maiapp.lavafrai.ru/api/v1/groups" > /dev/null 2>&1; then
  echo "$LOG_PREFIX 🌐 Интернет уже есть — кэшируем напрямую (фоном, 5 потоков)"
  $PY cache_schedule.py --startup --parallel 5 &
  CACHE_PID=$!
else
  echo "$LOG_PREFIX ⚠ Нет интернета и нет .wifi_creds — работаем с тем кэшем что есть"
  echo "$LOG_PREFIX   для авто-Wi-Fi: $PY wifi_schedule_update.py --setup"
fi
[ -n "$CACHE_PID" ] && echo "$LOG_PREFIX   CACHE PID=$CACHE_PID (прогресс — в /api/cache-progress)"

# AFK-заставка живёт в браузере (mai_kiosk.html → <video>).
if [ -f "./on.mp4" ]; then
  echo "$LOG_PREFIX 🎬 AFK-видео: ./on.mp4 готово"
else
  echo "$LOG_PREFIX ⚠ on.mp4 не найден — AFK-заставка отключится автоматически"
fi

echo "$LOG_PREFIX 🌐 Открываем браузер: http://localhost:8765"
if command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:8765"
elif command -v open &>/dev/null; then
    open "http://localhost:8765"
fi

echo "$LOG_PREFIX ✅ Готово! Нажми Ctrl+C чтобы остановить."
trap "kill $SERVER_PID $CACHE_PID 2>/dev/null; echo '$LOG_PREFIX 🛑 Сервер остановлен.'" EXIT INT TERM
wait $SERVER_PID

# ── Авто-установка cron для обновления в 10:00 ────────────────────
setup_cron() {
  PY_FULL=$(command -v python3 || command -v python)
  SCRIPT_FULL="$(cd "$(dirname "$0")" && pwd)/cache_schedule.py"
  LOG_FULL="$(cd "$(dirname "$0")" && pwd)/cache.log"
  CRON_LINE="0 10 * * * $PY_FULL $SCRIPT_FULL >> $LOG_FULL 2>&1"

  if crontab -l 2>/dev/null | grep -qF "cache_schedule.py"; then
    echo "[cron] ✓ Задание уже есть в crontab"
  else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "[cron] ✓ Добавлено задание: обновление в 10:00 каждый день"
    echo "[cron]   $CRON_LINE"
  fi
}

setup_cron
