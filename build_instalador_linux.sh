#!/usr/bin/env bash
# Gera o pacote de instalação para Linux:
#   dist/CH341Studio-<versão>-linux-x86_64.tar.gz
# Quem recebe só precisa extrair e rodar ./instalar.sh
# Requisitos (na máquina que gera o pacote): Python 3.10+ com pip
set -euo pipefail
cd "$(dirname "$0")"
VER="$(python3 -c 'import ch341studio; print(ch341studio.__version__)')"
ARCH="$(uname -m)"
python3 -m pip install -r requirements.txt pyinstaller
WORK="$(mktemp -d)"
python3 -m PyInstaller --noconfirm --clean --windowed --name CH341Studio \
  --workpath "$WORK" --specpath "$WORK" \
  --add-data "$PWD/ch341studio/chips:ch341studio/chips" \
  --add-data "$PWD/ch341studio/assets:ch341studio/assets" \
  --collect-all libusb_package \
  --collect-data certifi \
  run.py
rm -rf "$WORK"

PKG="CH341Studio-$VER-linux-$ARCH"
STAGE="$(mktemp -d)/$PKG"
mkdir -p "$STAGE"
cp -a dist/CH341Studio "$STAGE/"
cp installer/linux/instalar.sh installer/linux/desinstalar.sh "$STAGE/"
cp ch341studio/assets/icon.png "$STAGE/ch341studio.png"
cp linux/99-ch341a.rules "$STAGE/"
cat > "$STAGE/LEIA-ME.txt" <<TXT
CH341 Studio $VER — criado por GDTech Informática

Instalar:      ./instalar.sh            (só para o seu usuário)
               ./instalar.sh --sistema  (todos os usuários, pede senha)
Desinstalar:   ./desinstalar.sh

O instalador cria atalho na área de trabalho e no menu, e oferece instalar a
regra USB (99-ch341a.rules) para usar o CH341A sem ser root.
Se o programa não achar o gravador: sudo apt install libusb-1.0-0
TXT
chmod +x "$STAGE/instalar.sh" "$STAGE/desinstalar.sh"
mkdir -p dist
tar -C "$(dirname "$STAGE")" -czf "dist/$PKG.tar.gz" "$PKG"
rm -rf "$(dirname "$STAGE")"
echo
echo "Pronto: dist/$PKG.tar.gz"
echo "Para instalar: tar xzf $PKG.tar.gz && cd $PKG && ./instalar.sh"
