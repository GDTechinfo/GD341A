"""Tema escuro do CH341 Studio."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

C = {
    "bg": "#0E1116",
    "panel": "#161A22",
    "panel2": "#1C212B",
    "border": "#2A303C",
    "text": "#E6E9EF",
    "muted": "#8B93A5",
    "dim": "#5B6375",
    "accent": "#19B5D8",
    "accent2": "#0E8FB0",
    "ok": "#22C55E",
    "warn": "#F59E0B",
    "danger": "#EF4444",
    "diff_bg": "#5A1E24",
    "diff_fg": "#FFC2C2",
    "cursor": "#19B5D8",
    "sel_bg": "#5A4A12",
}

QSS = f"""
QMainWindow, QWidget {{ background: {C['bg']}; color: {C['text']}; font-size: 10pt; }}
QFrame#panel, QGroupBox {{ background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 8px; }}
QGroupBox {{ margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {C['muted']}; }}
QLabel#title {{ font-size: 16pt; font-weight: 700; }}
QLabel#subtitle {{ color: {C['muted']}; }}
QLabel#muted {{ color: {C['muted']}; }}
QLabel#value {{ font-weight: 600; }}
QLabel#warn {{ background: #3B1C1C; color: #FFB4B4; border: 1px solid {C['danger']};
              border-radius: 6px; padding: 6px; font-weight: 600; }}
QLabel#pill {{ border-radius: 10px; padding: 3px 10px; font-weight: 600; }}
QPushButton {{ background: {C['panel2']}; border: 1px solid {C['border']}; border-radius: 6px;
               padding: 7px 10px; }}
QPushButton:hover {{ border-color: {C['accent']}; }}
QPushButton:pressed {{ background: {C['border']}; }}
QPushButton:disabled {{ color: {C['dim']}; border-color: {C['panel2']}; }}
QPushButton#primary {{ background: {C['accent2']}; border-color: {C['accent']}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {C['accent']}; }}
QPushButton#danger {{ border-color: #6B2A2A; color: #FF9C9C; }}
QPushButton#danger:hover {{ border-color: {C['danger']}; }}
QComboBox, QLineEdit {{ background: {C['panel2']}; border: 1px solid {C['border']}; border-radius: 6px;
                        padding: 5px 8px; selection-background-color: {C['accent2']}; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {C['accent']}; }}
QComboBox QAbstractItemView {{ background: {C['panel2']}; border: 1px solid {C['border']};
                               selection-background-color: {C['accent2']}; }}
QCheckBox {{ spacing: 6px; }}
QProgressBar {{ background: {C['panel2']}; border: 1px solid {C['border']}; border-radius: 6px;
                height: 18px; text-align: center; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 5px; }}
QPlainTextEdit {{ background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 8px;
                  font-family: monospace; }}
QToolBar {{ background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 8px; spacing: 4px; padding: 3px; }}
QToolButton {{ padding: 5px 9px; border-radius: 5px; }}
QToolButton:hover {{ background: {C['panel2']}; }}
QSplitter::handle {{ background: {C['bg']}; }}
QStatusBar {{ color: {C['muted']}; }}
QScrollBar:vertical {{ background: {C['panel']}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {C['border']}; min-height: 24px; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: {C['dim']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QMenuBar {{ background: {C['bg']}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {C['accent2']}; }}
QMenu {{ background: {C['panel2']}; border: 1px solid {C['border']}; }}
QToolTip {{ background: {C['panel2']}; color: {C['text']}; border: 1px solid {C['border']}; }}
"""


def apply(app: QApplication):
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: C["bg"],
        QPalette.ColorRole.WindowText: C["text"],
        QPalette.ColorRole.Base: C["panel"],
        QPalette.ColorRole.AlternateBase: C["panel2"],
        QPalette.ColorRole.Text: C["text"],
        QPalette.ColorRole.Button: C["panel2"],
        QPalette.ColorRole.ButtonText: C["text"],
        QPalette.ColorRole.Highlight: C["accent2"],
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.ToolTipBase: C["panel2"],
        QPalette.ColorRole.ToolTipText: C["text"],
        QPalette.ColorRole.PlaceholderText: C["dim"],
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
