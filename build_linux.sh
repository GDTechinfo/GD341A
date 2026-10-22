#!/usr/bin/env bash
# Gera dist/CH341Studio/CH341Studio
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --clean --windowed --name CH341Studio \
  --add-data "ch341studio/chips:ch341studio/chips" \
  --add-data "ch341studio/assets:ch341studio/assets" \
  --collect-all libusb_package \
  --collect-data certifi \
  run.py
echo
echo "Pronto: dist/CH341Studio/CH341Studio"
echo "Lembre de instalar a regra udev: sudo cp linux/99-ch341a.rules /etc/udev/rules.d/"
