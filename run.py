"""Atalho: python run.py  (abre a interface)  |  python run.py read dump.bin  (linha de comando)."""

import sys

from ch341studio.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["gui"]))
