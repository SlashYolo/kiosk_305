#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# hide-panel.sh — Скрывает нижнюю панель LXQt на сенсорном стенде
#
# Запуск: ./setup-kiosk.sh  (один раз при настройке стенда)
# ═══════════════════════════════════════════════════════════════

echo "Настраиваем панель LXQt для режима киоска..."

# ── Метод 1: конфиг LXQt-panel ─────────────────────────────────
PANEL_CONF="$HOME/.config/lxqt/panel.conf"

if [ -f "$PANEL_CONF" ]; then
  # Создаём резервную копию
  cp "$PANEL_CONF" "${PANEL_CONF}.backup"
  echo "✓ Резервная копия: ${PANEL_CONF}.backup"

  # Скрываем панель — устанавливаем hidable=2 (всегда скрыта)
  # и убираем возможность вызвать её
  python3 - << 'PYEOF'
import configparser, os

conf_path = os.path.expanduser("~/.config/lxqt/panel.conf")
config = configparser.ConfigParser()
config.read(conf_path)

for section in config.sections():
    if 'panel' in section.lower() or section == 'Global':
        # hidable: 0=нет, 1=автоскрытие, 2=всегда скрыта
        config[section]['hidable'] = '2'
        config[section]['visible'] = 'false'
        config[section]['hideOnlyIntellihide'] = 'false'
        print(f"  Секция [{section}]: панель скрыта")

with open(conf_path, 'w') as f:
    config.write(f)
print("✓ panel.conf обновлён")
PYEOF
fi

# ── Метод 2: через lxqt-config (если доступен) ─────────────────
if command -v lxqt-config-panel &>/dev/null; then
  echo "lxqt-config-panel найден — перезапускаем панель..."
  pkill lxqt-panel 2>/dev/null
  sleep 1
fi

# ── Метод 3: Openbox — убрать декорации рабочего стола ─────────
OPENBOX_CONF="$HOME/.config/openbox/lxqt-rc.xml"
if [ -f "$OPENBOX_CONF" ]; then
  echo "✓ Openbox конфиг найден"
fi

# ── Блокировка правой кнопки мыши на рабочем столе ─────────────
# (чтобы нельзя было вызвать контекстное меню)
DESKTOP_CONF="$HOME/.config/pcmanfm-qt/lxqt/settings.conf"
if [ -f "$DESKTOP_CONF" ]; then
  cp "$DESKTOP_CONF" "${DESKTOP_CONF}.backup"
  # Отключаем контекстное меню рабочего стола
  sed -i 's/ShowContextMenu=true/ShowContextMenu=false/g' "$DESKTOP_CONF" 2>/dev/null
  echo "✓ Контекстное меню рабочего стола отключено"
fi

# ── Блокировка горячих клавиш через xbindkeys ──────────────────
if command -v xbindkeys &>/dev/null; then
  cat > "$HOME/.xbindkeysrc" << 'KEYS'
# Блокируем выход из киоска
"true"
  alt + F4

"true"
  super

"true"
  ctrl + alt + t

"true"
  ctrl + alt + Delete
KEYS
  xbindkeys --poll-rc 2>/dev/null &
  echo "✓ Блокировка горячих клавиш активирована"
fi

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  Настройка завершена!                        ║"
echo "║                                              ║"
echo "║  Перезагрузи систему для применения:         ║"
echo "║  sudo reboot                                 ║"
echo "╚══════════════════════════════════════════════╝"
