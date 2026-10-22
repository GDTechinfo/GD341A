#!/usr/bin/env bash
# Instalador do CH341 Studio para Linux — criado por GDTech Informática
#
#   ./instalar.sh            instala para o seu usuário (não precisa de senha)
#   ./instalar.sh --sistema  instala em /opt para todos os usuários (pede sudo)
#   ./instalar.sh --sem-udev não instala a regra de acesso ao USB
#
# Cria atalho no menu de aplicativos e na área de trabalho.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="ch341studio"
APP_NAME="CH341 Studio"
SYSTEM=0
UDEV=ask
for a in "$@"; do
  case "$a" in
    --sistema|--system) SYSTEM=1 ;;
    --sem-udev|--no-udev) UDEV=no ;;
    --udev) UDEV=yes ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Opção desconhecida: $a"; exit 1 ;;
  esac
done

if [ ! -x "$HERE/CH341Studio/CH341Studio" ]; then
  echo "Erro: pasta CH341Studio não encontrada ao lado deste script."
  exit 1
fi

if [ "$SYSTEM" = 1 ]; then
  SUDO="sudo"
  [ "$(id -u)" = 0 ] && SUDO=""
  DEST="/opt/$APP_ID"
  BIN_DIR="/usr/local/bin"
  APPS_DIR="/usr/share/applications"
  ICON_DIR="/usr/share/icons/hicolor/512x512/apps"
else
  SUDO=""
  DEST="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
  BIN_DIR="$HOME/.local/bin"
  APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"
fi

echo "Instalando $APP_NAME em $DEST ..."
$SUDO rm -rf "$DEST"
$SUDO mkdir -p "$DEST" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
$SUDO cp -a "$HERE/CH341Studio/." "$DEST/"
$SUDO cp "$HERE/ch341studio.png" "$ICON_DIR/$APP_ID.png"
$SUDO cp "$HERE/ch341studio.png" "$DEST/$APP_ID.png"
$SUDO cp "$HERE/desinstalar.sh" "$DEST/desinstalar.sh"
$SUDO chmod +x "$DEST/CH341Studio" "$DEST/desinstalar.sh"
$SUDO ln -sf "$DEST/CH341Studio" "$BIN_DIR/$APP_ID"

DESKTOP_FILE="$(mktemp)"
cat > "$DESKTOP_FILE" <<DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=$APP_NAME
GenericName=Gravador CH341A
Comment=Gravador de memórias SPI/I2C com CH341A — GDTech Informática
Exec="$DEST/CH341Studio"
Icon=$DEST/$APP_ID.png
Terminal=false
Categories=Development;Utility;
Keywords=ch341a;bios;spi;eeprom;flash;programmer;gravador;
StartupWMClass=$APP_ID
DESK
$SUDO install -m 644 "$DESKTOP_FILE" "$APPS_DIR/$APP_ID.desktop"

# atalho na área de trabalho (do usuário que está rodando o script)
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
if [ -z "$DESKTOP_DIR" ] || [ "$DESKTOP_DIR" = "$HOME" ]; then
  for d in "$HOME/Área de Trabalho" "$HOME/Área de trabalho" "$HOME/Desktop"; do
    [ -d "$d" ] && DESKTOP_DIR="$d" && break
  done
fi
if [ -n "$DESKTOP_DIR" ] && [ "$DESKTOP_DIR" != "$HOME" ]; then
  mkdir -p "$DESKTOP_DIR"
  install -m 755 "$DESKTOP_FILE" "$DESKTOP_DIR/$APP_ID.desktop"
  # GNOME/Ubuntu: marca o atalho como confiável para abrir com duplo clique
  gio set "$DESKTOP_DIR/$APP_ID.desktop" metadata::trusted true 2>/dev/null || true
  echo "Atalho criado em: $DESKTOP_DIR"
else
  echo "Aviso: não encontrei a pasta da área de trabalho; o atalho ficou só no menu."
fi
rm -f "$DESKTOP_FILE"
command -v update-desktop-database >/dev/null && $SUDO update-desktop-database "$APPS_DIR" 2>/dev/null || true

# regra udev: permite usar o CH341A sem ser root
RULE=/etc/udev/rules.d/99-ch341a.rules
if [ "$UDEV" = ask ]; then
  if [ -f "$RULE" ]; then
    UDEV=no
  elif [ -t 0 ]; then
    read -r -p "Instalar a regra de acesso ao USB do CH341A (precisa da senha de administrador)? [S/n] " r
    case "${r:-s}" in [sSyY]*) UDEV=yes ;; *) UDEV=no ;; esac
  else
    UDEV=no
  fi
fi
if [ "$UDEV" = yes ]; then
  S="sudo"; [ "$(id -u)" = 0 ] && S=""
  if $S mkdir -p /etc/udev/rules.d && $S install -m 644 "$HERE/99-ch341a.rules" "$RULE"; then
    $S udevadm control --reload-rules 2>/dev/null || true
    $S udevadm trigger 2>/dev/null || true
    echo "Regra USB instalada. Desconecte e reconecte o gravador."
  else
    echo "Aviso: não consegui instalar a regra USB. Depois rode:"
    echo "  sudo cp 99-ch341a.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules"
  fi
fi

echo
echo "Pronto! Abra o $APP_NAME pelo atalho na área de trabalho ou pelo menu de aplicativos."
echo "Para desinstalar: $DEST/desinstalar.sh"
