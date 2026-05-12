#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# install.sh — Установка MAI Kiosk на Lubuntu одной командой
#
# Запуск: bash install.sh
# ═══════════════════════════════════════════════════════════════

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIOSK_DIR="$HOME/mai-kiosk"
USER_NAME="$USER"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; }

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║      MAI Kiosk — Установка на Lubuntu       ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Создаём папку киоска ────────────────────────────────────
echo "📁 Создаём папку $KIOSK_DIR ..."
mkdir -p "$KIOSK_DIR"

# Копируем файлы из текущей папки
for f in mai_kiosk.html server.py mai_schedule_parser.py afk-video.sh hide-panel.sh; do
  if [ -f "$SCRIPT_DIR/$f" ]; then
    cp "$SCRIPT_DIR/$f" "$KIOSK_DIR/$f"
    ok "Скопирован: $f"
  else
    warn "Не найден: $f (скопируй вручную)"
  fi
done

# Копируем mai-kiosk.sh (главный скрипт запуска)
cp "$SCRIPT_DIR/mai-kiosk.sh" "$KIOSK_DIR/mai-kiosk.sh"
chmod +x "$KIOSK_DIR/mai-kiosk.sh"
chmod +x "$KIOSK_DIR/afk-video.sh"
chmod +x "$KIOSK_DIR/hide-panel.sh"
ok "Права доступа выставлены"

# Добавляем запуск AFK-монитора в mai-kiosk.sh если ещё не добавлен
if ! grep -q "afk-video.sh" "$KIOSK_DIR/mai-kiosk.sh"; then
  echo "" >> "$KIOSK_DIR/mai-kiosk.sh"
  echo '# AFK видео-заставка' >> "$KIOSK_DIR/mai-kiosk.sh"
  echo 'bash "$(dirname "$0")/afk-video.sh" &' >> "$KIOSK_DIR/mai-kiosk.sh"
  ok "AFK-скрипт подключён к mai-kiosk.sh"
fi

# ── 2. Устанавливаем системные зависимости ─────────────────────
echo ""
echo "📦 Устанавливаем зависимости..."

sudo apt update -qq 2>/dev/null

PACKAGES="chromium-browser xprintidle mpv xdotool xbindkeys python3-pip"
for pkg in $PACKAGES; do
  if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
    ok "$pkg уже установлен"
  else
    echo "  Устанавливаем $pkg..."
    sudo apt install -y "$pkg" -qq 2>/dev/null && ok "$pkg" || warn "$pkg — ошибка установки"
  fi
done

# Python-пакеты
echo "  Python-пакеты..."
python3 -m pip install flask flask-cors requests beautifulsoup4 --quiet 2>/dev/null
ok "Python-пакеты (flask, flask-cors, requests, beautifulsoup4)"

# ── 3. Автозапуск ──────────────────────────────────────────────
echo ""
echo "🚀 Настраиваем автозапуск..."

AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat > "$AUTOSTART_DIR/mai-kiosk.desktop" << EOF
[Desktop Entry]
Type=Application
Name=MAI Kiosk
Comment=Информационный стенд МАИ
Exec=$KIOSK_DIR/mai-kiosk.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
X-LXQt-Autostart=true
EOF
ok "Файл автозапуска создан: $AUTOSTART_DIR/mai-kiosk.desktop"

# ── 4. Скрываем панель LXQt ────────────────────────────────────
echo ""
echo "🔒 Скрываем нижнюю панель..."
bash "$KIOSK_DIR/hide-panel.sh" 2>/dev/null && ok "Панель скрыта" || warn "Панель — настрой вручную (см. hide-panel.sh)"

# ── 5. Отключаем screensaver и автовыключение дисплея ──────────
echo ""
echo "🖥️  Отключаем screensaver..."

# DPMS — управление питанием дисплея
if command -v xset &>/dev/null; then
  # Добавляем в автозапуск браузера
  cat >> "$KIOSK_DIR/mai-kiosk.sh" << 'XSET'

# Отключаем sleep/screensaver
xset s off
xset s noblank
xset -dpms
XSET
  ok "xset s off, -dpms добавлены"
fi

# Создаём конфиг lightdm для автологина (если используется)
LIGHTDM_CONF="/etc/lightdm/lightdm.conf"
if [ -f "$LIGHTDM_CONF" ]; then
  warn "Автологин: добавь в $LIGHTDM_CONF:"
  echo "    [SeatDefaults]"
  echo "    autologin-user=$USER_NAME"
  echo "    autologin-user-timeout=0"
fi

# ── 6. Создаём папку для видео-заставки ────────────────────────
if [ ! -f "$KIOSK_DIR/on.mp4" ]; then
  warn "Видео-заставка: положи файл on.mp4 в $KIOSK_DIR/"
fi

# ── Итог ───────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ✅ Установка завершена!                                 ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║                                                          ║"
echo "║  Папка киоска:  ~/mai-kiosk/                            ║"
echo "║  Видео AFK:     ~/mai-kiosk/on.mp4  ← положи сюда      ║"
echo "║                                                          ║"
echo "║  Запустить вручную:                                      ║"
echo "║    ~/mai-kiosk/mai-kiosk.sh                             ║"
echo "║                                                          ║"
echo "║  Перезагрузить для применения автозапуска:               ║"
echo "║    sudo reboot                                           ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
