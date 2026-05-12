#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# MAI KIOSK — Автозапуск для Lubuntu (LXQt / Openbox)
# ═══════════════════════════════════════════════════════════════

KIOSK_DIR="$HOME/mai-kiosk"
PORT=8765
LOG="$KIOSK_DIR/kiosk.log"

# ── Экспортируем X11 (критично для AFK-видео и xprintidle) ─────
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

# Ждём загрузки рабочего стола
sleep 5

echo "[$(date)] Старт MAI Kiosk  DISPLAY=$DISPLAY" >> "$LOG"

# ── 1. Python-сервер ────────────────────────────────────────────
cd "$KIOSK_DIR"
PY=$(command -v python3 || command -v python)
$PY server.py >> "$LOG" 2>&1 &
SERVER_PID=$!
echo "[$(date)] Сервер PID=$SERVER_PID" >> "$LOG"

for i in $(seq 1 10); do
  sleep 1
  if curl -s "http://localhost:$PORT/api/health" > /dev/null 2>&1; then
    echo "[$(date)] Сервер готов за ${i}с" >> "$LOG"
    break
  fi
done

# ── 2. AFK-видеозаставка ────────────────────────────────────────
if [ -f "$KIOSK_DIR/afk-video.sh" ] && [ -f "$KIOSK_DIR/on.mp4" ]; then
  DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" \
    bash "$KIOSK_DIR/afk-video.sh" >> "$LOG" 2>&1 &
  AFK_PID=$!
  echo "[$(date)] AFK-монитор PID=$AFK_PID" >> "$LOG"
else
  AFK_PID=""
  [ ! -f "$KIOSK_DIR/on.mp4" ] && echo "[$(date)] ⚠ on.mp4 не найден — AFK отключён" >> "$LOG"
fi

# ── 3. Браузер в режиме киоска ──────────────────────────────────
BROWSER=""
for b in chromium-browser chromium google-chrome google-chrome-stable; do
  if command -v "$b" &>/dev/null; then BROWSER="$b"; break; fi
done

if [ -z "$BROWSER" ]; then
  echo "[$(date)] ❌ Браузер не найден" >> "$LOG"
  exit 1
fi

echo "[$(date)] Браузер: $BROWSER" >> "$LOG"

DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" $BROWSER \
  --kiosk \
  --app="http://localhost:$PORT" \
  --noerrdialogs \
  --disable-translate \
  --no-first-run \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --disable-features=TranslateUI,Translate \
  --disable-background-networking \
  --disable-sync \
  --no-default-browser-check \
  --touch-events=enabled \
  --enable-touch-drag-drop \
  2>> "$LOG" &

BROWSER_PID=$!
echo "[$(date)] Браузер PID=$BROWSER_PID" >> "$LOG"

# ── 4. Завершение ───────────────────────────────────────────────
cleanup() {
  echo "[$(date)] Завершение..." >> "$LOG"
  kill $SERVER_PID $BROWSER_PID $AFK_PID 2>/dev/null
}
trap cleanup EXIT INT TERM
wait $BROWSER_PID

kill $SERVER_PID $AFK_PID 2>/dev/null
echo "[$(date)] MAI Kiosk завершён" >> "$LOG"
