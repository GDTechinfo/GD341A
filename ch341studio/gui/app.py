"""Ponto de entrada da interface gráfica."""

import sys


def run() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("A interface precisa do PySide6. Instale com:  pip install -r requirements.txt",
              file=sys.stderr)
        return 1
    from .. import APP_NAME
    from . import theme
    from .main_window import MainWindow

    from ..i18n import set_lang
    from .main_window import make_settings

    app = QApplication.instance() or QApplication(sys.argv)
    try:
        app.setDesktopFileName("ch341studio")   # Linux: liga a janela ao atalho .desktop
    except Exception:
        pass
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("GDTech")
    set_lang(make_settings().value("lang", "pt"))
    theme.apply(app)
    try:
        import os
        from PySide6.QtGui import QIcon
        icon = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.png")
        if os.path.isfile(icon):
            app.setWindowIcon(QIcon(icon))
    except Exception:
        pass
    win = MainWindow()
    win.show()
    return app.exec()
