#!/usr/bin/env bash
# Gera o AppImage do Linux: dist/CH341Studio-<versão>-x86_64.AppImage
#
# Um arquivo único: quem baixa só marca como executável e abre.
# Na primeira abertura o programa oferece criar o atalho na área de trabalho e no menu.
#
# Requisitos (na máquina que gera): Python 3.10+ com pip, e internet (baixa o appimagetool).
# Gere numa distro mais antiga que as dos usuários (ex.: Ubuntu 22.04): o AppImage funciona
# nessa versão e nas mais novas.
set -euo pipefail
cd "$(dirname "$0")"

VER="$(python3 -c 'import ch341studio; print(ch341studio.__version__)')"
ARCH="$(uname -m)"
OUT="dist/CH341Studio-$VER-$ARCH.AppImage"

python3 -m pip install -r requirements.txt pyinstaller

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
python3 -m PyInstaller --noconfirm --clean --windowed --name CH341Studio \
  --workpath "$WORK/pyi" --specpath "$WORK/pyi" --distpath "$WORK/pyi-dist" \
  --add-data "$PWD/ch341studio/chips:ch341studio/chips" \
  --add-data "$PWD/ch341studio/assets:ch341studio/assets" \
  --collect-all libusb_package \
  --collect-data certifi \
  run.py

# ---- monta o AppDir
APPDIR="$WORK/CH341Studio.AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/512x512/apps"
cp -a "$WORK/pyi-dist/CH341Studio" "$APPDIR/usr/bin/"
cp ch341studio/assets/icon.png "$APPDIR/ch341studio.png"
cp ch341studio/assets/icon.png "$APPDIR/usr/share/icons/hicolor/512x512/apps/ch341studio.png"
ln -sf ch341studio.png "$APPDIR/.DirIcon"

cat > "$APPDIR/ch341studio.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=CH341 Studio
GenericName=Gravador CH341A
Comment=Gravador de memórias SPI/I2C com CH341A — GDTech Informática
Exec=CH341Studio %F
Icon=ch341studio
Terminal=false
Categories=Development;Utility;
Keywords=ch341a;bios;spi;eeprom;flash;programmer;gravador;
StartupWMClass=ch341studio
X-AppImage-Version=$VER
DESK
cp "$APPDIR/ch341studio.desktop" "$APPDIR/usr/share/applications/"

cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/CH341Studio/CH341Studio" "$@"
RUN
chmod +x "$APPDIR/AppRun"

# ---- appimagetool (baixado na hora, se não estiver no PATH)
TOOL="$(command -v appimagetool || true)"
if [ -z "$TOOL" ]; then
  TOOL="$WORK/appimagetool"
  curl -fL --retry 3 -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
  chmod +x "$TOOL"
fi

mkdir -p dist
rm -f "$OUT"
# APPIMAGE_EXTRACT_AND_RUN: roda o appimagetool sem precisar de FUSE (ex.: no GitHub Actions)
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$TOOL" --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"

echo
echo "Pronto: $OUT"
echo "Para usar: chmod +x $(basename "$OUT") && ./$(basename "$OUT")"
