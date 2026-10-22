import sys

from .cli import main

if __name__ == "__main__":
    # sem argumentos abre a interface gráfica
    sys.exit(main(sys.argv[1:] or ["gui"]))
