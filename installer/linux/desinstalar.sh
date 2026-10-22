#!/usr/bin/env bash
# Remove o CH341 Studio (mantém suas configurações e histórico em ~/.config/ch341studio)
set -uo pipefail
APP_ID="ch341studio"
for base in "${XDG_DATA_HOME:-$HOME/.local/share}"; do
  rm -rf "$base/$APP_ID" "$base/applications/$APP_ID.desktop" \
         "$base/icons/hicolor/512x512/apps/$APP_ID.png"
done
rm -f "$HOME/.local/bin/$APP_ID"
if [ -d /opt/$APP_ID ] || [ -f /usr/share/applications/$APP_ID.desktop ]; then
  S="sudo"; [ "$(id -u)" = 0 ] && S=""
  $S rm -rf /opt/$APP_ID /usr/share/applications/$APP_ID.desktop \
            /usr/share/icons/hicolor/512x512/apps/$APP_ID.png /usr/local/bin/$APP_ID
fi
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
for d in "$DESKTOP_DIR" "$HOME/Área de Trabalho" "$HOME/Área de trabalho" "$HOME/Desktop"; do
  [ -n "$d" ] && rm -f "$d/$APP_ID.desktop"
done
echo "CH341 Studio removido. (A regra /etc/udev/rules.d/99-ch341a.rules foi mantida; "
echo "apague com 'sudo rm /etc/udev/rules.d/99-ch341a.rules' se quiser.)"
