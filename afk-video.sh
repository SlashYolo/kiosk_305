#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# afk-video.sh — AFK-заставка для сенсорного стенда (Lubuntu)
# Запускается из mai-kiosk.sh, не запускать вручную.
# ═══════════════════════════════════════════════════════════════

KIOSK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIDEO="$KIOSK_DIR/on.mp4"
IDLE_THRESHOLD=60000    # 1 минута = 60 000 мс
CHECK_SEC=5             # проверять каждые 5 секунд
LOG="$KIOSK_DIR/kiosk.log"

log() { echo "[$(date '+%H:%M:%S')] [AFK] $*" | tee -a "$LOG"; }

# ── Находим активный X-дисплей ─────────────────────────────────
# На Lubuntu при автозапуске DISPLAY может быть не задан
find_display() {
    # 1. Уже задан в окружении
    [ -n "$DISPLAY" ] && echo "$DISPLAY" && return

    # 2. Смотрим в /tmp/.X*-lock (Xorg)
    for lock in /tmp/.X*-lock; do
        [ -f "$lock" ] || continue
        num="${lock##*/tmp/.X}"
        num="${num%-lock}"
        echo ":${num}" && return
    done

    # 3. Спрашиваем у запущенного Xorg процесса
    XPID=$(pgrep -x Xorg | head -1)
    if [ -n "$XPID" ]; then
        disp=$(cat /proc/"$XPID"/cmdline 2>/dev/null \
               | tr '\0' '\n' | grep '^:[0-9]' | head -1)
        [ -n "$disp" ] && echo "$disp" && return
    fi

    echo ":0"  # fallback
}

find_xauth() {
    # Ищем актуальный Xauthority файл
    [ -f "$HOME/.Xauthority" ] && echo "$HOME/.Xauthority" && return
    # В /run или /tmp
    find /tmp /run -maxdepth 3 -name '.Xauth*' -o -name 'xauth*' 2>/dev/null \
        | head -1
}

export DISPLAY="$(find_display)"
export XAUTHORITY="$(find_xauth)"

log "Старт. DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY"

# ── Зависимости ─────────────────────────────────────────────────
check_dep() {
    command -v "$1" &>/dev/null && return 0
    log "Устанавливаем $1..."
    DEBIAN_FRONTEND=noninteractive sudo apt-get install -y "$1" -qq 2>/dev/null
    command -v "$1" &>/dev/null
}

check_dep xprintidle || { log "ОШИБКА: xprintidle не удалось установить"; exit 1; }
check_dep mpv        || { log "ОШИБКА: mpv не удалось установить"; exit 1; }

# ── Проверяем видеофайл ─────────────────────────────────────────
if [ ! -f "$VIDEO" ]; then
    log "Файл on.mp4 не найден: $VIDEO"
    log "Создай файл on.mp4 в папке $KIOSK_DIR"
    exit 1
fi

log "Готов. Порог бездействия: $((IDLE_THRESHOLD / 1000)) сек"

MPV_PID=""

# ── Основной цикл ───────────────────────────────────────────────
while true; do

    # xprintidle требует DISPLAY
    IDLE=$(DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" xprintidle 2>/dev/null)

    # Если не число — считаем 0 (безопасно)
    [[ "$IDLE" =~ ^[0-9]+$ ]] || IDLE=0

    if [ "$IDLE" -ge "$IDLE_THRESHOLD" ] && [ -z "$MPV_PID" ]; then
        # ── Запускаем видео ──────────────────────────────────────
        log "Бездействие ${IDLE}мс ≥ ${IDLE_THRESHOLD}мс → запускаем видео"

        DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" \
        mpv \
            --fullscreen \
            --loop=inf \
            --no-osc \
            --no-input-default-bindings \
            --input-conf=/dev/null \
            --cursor-autohide=always \
            --stop-screensaver=yes \
            --ontop=yes \
            "$VIDEO" \
            >> "$LOG" 2>&1 &

        MPV_PID=$!
        log "mpv PID=$MPV_PID"

    elif [ "$IDLE" -lt "$IDLE_THRESHOLD" ] && [ -n "$MPV_PID" ]; then
        # ── Активность — останавливаем видео ────────────────────
        log "Активность (idle=${IDLE}мс) → стоп видео PID=$MPV_PID"
        kill "$MPV_PID" 2>/dev/null
        wait "$MPV_PID" 2>/dev/null
        MPV_PID=""

        # Возвращаем фокус браузеру
        sleep 0.3
        if command -v xdotool &>/dev/null; then
            WIN=$(DISPLAY="$DISPLAY" xdotool search --onlyvisible \
                    --classname "chromium\|Chromium\|chrome\|Chrome" 2>/dev/null | head -1)
            if [ -n "$WIN" ]; then
                DISPLAY="$DISPLAY" xdotool windowactivate --sync "$WIN" 2>/dev/null
                log "Фокус → браузер (win=$WIN)"
            fi
        fi
    fi

    # Mpv завершился сам (конец файла без loop или ошибка)
    if [ -n "$MPV_PID" ] && ! kill -0 "$MPV_PID" 2>/dev/null; then
        log "mpv завершился (PID=$MPV_PID)"
        MPV_PID=""
    fi

    sleep "$CHECK_SEC"
done
