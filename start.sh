#!/bin/bash
cd "$(dirname "$0")"

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

# ── Автосохранение ВСЕГО расписания при запуске ──────────────────
echo "$LOG_PREFIX 📡 Проверяем интернет..."
if curl -s --max-time 5 "https://maiapp.lavafrai.ru/api/v1/groups" > /dev/null 2>&1; then
  echo "$LOG_PREFIX ✓ Интернет есть — запускаем сохранение расписания в фоне..."
  $PY cache_schedule.py --startup &
  CACHE_PID=$!
  echo "$LOG_PREFIX   (PID=$CACHE_PID, продолжает работать в фоне)"
else
  CACHE_PID=""
  echo "$LOG_PREFIX ⚠ Интернет недоступен — работаем с кэшем"
fi

echo ""
echo "$LOG_PREFIX 🚀 Запускаем сервер..."
$PY server.py &
SERVER_PID=$!
sleep 2

if ! kill -0 $SERVER_PID 2>/dev/null; then
  echo "$LOG_PREFIX ❌ Сервер не запустился. Запусти вручную: $PY server.py"
  exit 1
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
