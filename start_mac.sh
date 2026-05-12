#!/bin/bash
cd "$(dirname "$0")"

# Находим python3
PY=""
for cmd in python3 python3.12 python3.11 python3.10 python; do
  if command -v "$cmd" &>/dev/null; then
    PY="$cmd"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "❌ Python не найден. Установи Python 3: https://www.python.org"
  exit 1
fi

echo "✓ Используем: $PY ($($PY --version 2>&1))"

echo "📦 Устанавливаем зависимости..."
$PY -m pip install flask flask-cors requests beautifulsoup4 --quiet 2>/dev/null

echo "🚀 Запускаем сервер..."
$PY server.py &
SERVER_PID=$!

sleep 2

# Проверяем что сервер поднялся
if ! kill -0 $SERVER_PID 2>/dev/null; then
  echo "❌ Сервер не запустился. Запусти вручную: $PY server.py"
  exit 1
fi

echo "🌐 Открываем браузер: http://localhost:8765"
if command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:8765"
elif command -v open &>/dev/null; then
    open "http://localhost:8765"
fi

echo "✅ Готово! Нажми Ctrl+C чтобы остановить."
trap "kill $SERVER_PID 2>/dev/null; echo '🛑 Сервер остановлен.'" EXIT INT TERM
wait $SERVER_PID
