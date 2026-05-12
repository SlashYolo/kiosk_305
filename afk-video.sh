#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# afk-video.sh — AFK-заставка для сенсорного стенда (Lubuntu)
#
# Логика:
#   • Следит за активностью через xprintidle (X11)
#   • После 5 минут бездействия запускает on.mp4 в полный экран
#   • При любом касании/движении — закрывает видео
#
# Запуск: добавляется автоматически из mai-kiosk.sh
# ═══════════════════════════════════════════════════════════════

KIOSK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIDEO="$KIOSK_DIR/on.mp4"
IDLE_MS=300000       # 5 минут = 300 000 мс
CHECK_SEC=10         # проверять каждые 10 секунд
LOG="$KIOSK_DIR/kiosk.log"

# ── Экспортируем X11-переменные (критично для Lubuntu/LXQt) ────
# Без этого xprintidle и mpv не знают к какому дисплею обращаться
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

# Если XAUTHORITY не существует — ищем по PID процесса X
if [ ! -f "$XAUTHORITY" ]; then
    XAUTH_FOUND=$(find /tmp -name '.Xauth*' -o -name 'xauth*' 2>/dev/null | head -1)
    [ -n "$XAUTH_FOUND" ] && export XAUTHORITY="$XAUTH_FOUND"
fi

log() { echo "[$(date '+%H:%M:%S')] [AFK] $*" | tee -a "$LOG"; }

# ── Проверка зависимостей ───────────────────────────────────────
if ! command -v xprintidle &>/dev/null; then
    log "Устанавливаем xprintidle..."
    sudo apt-get install -y xprintidle -qq 2>/dev/null || {
        log "ОШИБКА: не удалось установить xprintidle. Запусти: sudo apt install xprintidle"
        exit 1
    }
fi

if ! command -v mpv &>/dev/null; then
    log "Устанавливаем mpv..."
    sudo apt-get install -y mpv -qq 2>/dev/null || {
        log "ОШИБКА: не удалось установить mpv. Запусти: sudo apt install mpv"
        exit 1
    }
fi

if [ ! -f "$VIDEO" ]; then
    log "Файл видео не найден: $VIDEO"
    log "Создай файл on.mp4 в папке $KIOSK_DIR и перезапусти"
    exit 1
fi

log "Запущен. Дисплей=$DISPLAY, порог=${IDLE_MS}мс ($(( IDLE_MS/60000 )) мин)"

MPV_PID=""

# ── Основной цикл ───────────────────────────────────────────────
while true; do
    IDLE_MS_NOW=$(xprintidle 2>/dev/null || echo "0")

    # Числовая проверка
    if ! [[ "$IDLE_MS_NOW" =~ ^[0-9]+$ ]]; then
        IDLE_MS_NOW=0
    fi

    if [ "$IDLE_MS_NOW" -ge "$IDLE_MS" ] && [ -z "$MPV_PID" ]; then
        # ── Запускаем видео ──────────────────────────────────────
        log "AFK ${IDLE_MS_NOW}мс >= ${IDLE_MS}мс → запускаем видео"

        DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" mpv \
            --fullscreen \
            --loop=inf \
            --no-osc \
            --no-input-default-bindings \
            --input-conf=/dev/null \
            --cursor-autohide=always \
            --stop-screensaver=yes \
            --ontop \
            --geometry=100%x100% \
            "$VIDEO" \
            >> "$LOG" 2>&1 &

        MPV_PID=$!
        log "mpv запущен PID=$MPV_PID"

    elif [ "$IDLE_MS_NOW" -lt "$IDLE_MS" ] && [ -n "$MPV_PID" ]; then
        # ── Активность — останавливаем видео ────────────────────
        log "Активность (idle=${IDLE_MS_NOW}мс) → останавливаем видео PID=$MPV_PID"
        kill "$MPV_PID" 2>/dev/null
        wait "$MPV_PID" 2>/dev/null
        MPV_PID=""

        # Возвращаем фокус браузеру
        sleep 0.5
        if command -v xdotool &>/dev/null; then
            WIN=$(xdotool search --onlyvisible --class "chromium\|Chromium\|chrome\|Chrome" 2>/dev/null | head -1)
            if [ -n "$WIN" ]; then
                DISPLAY="$DISPLAY" xdotool windowactivate --sync "$WIN" 2>/dev/null
                DISPLAY="$DISPLAY" xdotool windowfocus "$WIN" 2>/dev/null
                log "Фокус возвращён браузеру (win=$WIN)"
            fi
        fi
    fi

    # Проверяем что mpv ещё жив
    if [ -n "$MPV_PID" ] && ! kill -0 "$MPV_PID" 2>/dev/null; then
        log "mpv завершился сам (PID=$MPV_PID)"
        MPV_PID=""
    fi

    sleep "$CHECK_SEC"
done
