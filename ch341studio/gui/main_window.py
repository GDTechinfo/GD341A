"""Janela principal do CH341 Studio."""

import os
import time
from datetime import datetime

from PySide6.QtCore import QSettings, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QApplication, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QMenu, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import (APP_NAME, AUTHOR, LINKS, __version__, benchmark, chipdb, chipupdate, fileio, health,
                identify, intel)
from ..history import History
from ..i18n import tr
from ..backends import BACKENDS, create_backend
from ..eeprom24 import Eeprom24
from ..errors import Cancelled, CH341Error, ProtectedError
from ..spiflash import SpiFlash
from . import tools
from . import worker as W
from .hexview import HexView
from .theme import C

MODE_SPI, MODE_I2C = "spi", "i2c"


def make_settings() -> QSettings:
    """Configurações: no modo portátil ficam num .ini ao lado do programa."""
    pdir = chipdb.portable_dir()
    if pdir:
        os.makedirs(pdir, exist_ok=True)
        return QSettings(os.path.join(pdir, "config.ini"), QSettings.Format.IniFormat)
    return QSettings("GDTech", "CH341Studio")


def _n(x) -> str:
    """Número com separador de milhar brasileiro (1.234.567)."""
    return f"{int(x):,}".replace(",", ".")


def _fmt_eta(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = make_settings()
        self.backend = None
        self.mode = MODE_SPI
        self.chip = None                  # SpiChip ou EepromChip selecionado
        self.detected_id: int | None = None
        self.buffer = bytearray()
        self.file_path: str | None = None
        self.dirty = False
        self.busy = False
        self._thread = None
        self._worker = None
        self._stage = None
        self._stage_t0 = 0.0
        self._pending_retry = None
        self._extra_chips: list = []      # chips genéricos detectados
        self._crc_timer = None
        self._box_shown = False
        self._alert_text = None
        self._alert_ok = True
        self._alert_default = None
        self._last_read_hist = None
        try:
            self.history = History()
        except Exception:
            self.history = None

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(900, 600)
        self._initial_size()
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_actions()
        self._load_settings()
        self.cb_mode.currentIndexChanged.connect(lambda *_: self._on_mode_changed())
        self.ed_search.textChanged.connect(lambda *_: self._populate_chips())
        self.cb_chip.currentIndexChanged.connect(lambda *_: self._on_chip_selected())
        self._populate_chips()
        self._refresh_state()
        self.log(f"{APP_NAME} {__version__} — criado por {AUTHOR}")
        self.log(tr("Escolha o driver e clique em Conectar. Sem gravador? Use o modo Simulador."))
        self._upd_thread = None
        self._upd_worker = None
        self._upd_running = False
        self._db_pending_reload = False
        self._update_db_label()
        if self.ck_autoupd.isChecked() and chipupdate.due():
            QTimer.singleShot(1500, lambda: self.update_chip_db(silent=True))

    # ================================================================ UI
    def _build_ui(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        # --- cabeçalho
        head = QHBoxLayout()
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        t = QLabel(APP_NAME)
        t.setObjectName("title")
        s = QLabel(f"Gravador CH341A • criado por {AUTHOR}")
        s.setObjectName("subtitle")
        tbox.addWidget(t)
        tbox.addWidget(s)
        head.addLayout(tbox)
        head.addStretch(1)
        self.pill = QLabel(tr("● Desconectado"))
        self.pill.setObjectName("pill")
        head.addWidget(self.pill)
        outer.addLayout(head)

        # painel lateral | centro, separados por um divisor arrastável
        self.body_split = QSplitter(Qt.Orientation.Horizontal)
        self.body_split.setChildrenCollapsible(False)
        self.body_split.setHandleWidth(10)
        outer.addWidget(self.body_split, 1)

        # --- painel lateral (rolável em telas pequenas)
        side = QWidget()
        side_l = QVBoxLayout(side)
        # margem à direita: a barra de rolagem não encosta nos quadros
        side_l.setContentsMargins(0, 0, 10, 0)
        side_l.setSpacing(8)
        side_l.addWidget(self._build_device_box())
        side_l.addWidget(self._build_chip_box())
        side_l.addWidget(self._build_ops_box())
        side_l.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.side_scroll = scroll
        self.side_widget = side
        self.body_split.addWidget(scroll)

        # --- centro: barra + hex + log
        center_w = QWidget()
        center = QVBoxLayout(center_w)
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(6)
        self.toolbar = QToolBar()
        self.toolbar.setMovable(False)
        center.addWidget(self.toolbar)

        split = QSplitter(Qt.Orientation.Vertical)
        hexbox = QWidget()
        hl = QVBoxLayout(hexbox)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self.hex = HexView()
        self.hex.cursorMoved.connect(self._on_cursor)
        self.hex.edited.connect(self._on_edited)
        hl.addWidget(self.hex, 1)
        info = QHBoxLayout()
        self.lbl_file = QLabel(tr("Sem arquivo"))
        self.lbl_file.setObjectName("muted")
        self.lbl_size = QLabel("")
        self.lbl_crc = QLabel("")
        self.lbl_crc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_cursor = QLabel("")
        self.lbl_cursor.setObjectName("muted")
        for w in (self.lbl_file, self.lbl_size, self.lbl_crc):
            info.addWidget(w)
            info.addSpacing(14)
        info.addStretch(1)
        info.addWidget(self.lbl_cursor)
        hl.addLayout(info)
        split.addWidget(hexbox)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        prow = QHBoxLayout()
        self.lbl_stage = QLabel(tr("Pronto"))
        self.lbl_stage.setMinimumWidth(190)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.lbl_speed = QLabel("")
        self.lbl_speed.setObjectName("muted")
        self.lbl_speed.setMinimumWidth(170)
        self.btn_cancel = QPushButton(tr("Cancelar"))
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.clicked.connect(lambda: self.cancel_operation())
        prow.addWidget(self.lbl_stage)
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.lbl_speed)
        prow.addWidget(self.btn_cancel)
        bl.addLayout(prow)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(5000)
        bl.addWidget(self.logbox, 1)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        center.addWidget(split, 1)
        self.body_split.addWidget(center_w)
        self.body_split.setStretchFactor(0, 0)
        self.body_split.setStretchFactor(1, 1)
        self._fit_side_panel()

        self.setCentralWidget(root)
        self.statusBar().showMessage(tr("Pronto"))

    def _initial_size(self):
        """Primeira abertura: 1280×800, ou 90% da tela se ela for menor."""
        w, h = 1280, 800
        try:
            scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            w, h = min(w, int(scr.width() * 0.9)), min(h, int(scr.height() * 0.9))
        except Exception:
            pass
        self.resize(max(w, 900), max(h, 600))

    def _fit_side_panel(self, keep_user_width: bool = False):
        """Largura mínima do painel lateral = conteúdo + barra de rolagem, nunca menos."""
        try:
            self.side_widget.adjustSize()
            content = max(self.side_widget.minimumSizeHint().width(), 320)
            bar = self.side_scroll.verticalScrollBar().sizeHint().width()
            need = content + bar + 4
            self.side_scroll.setMinimumWidth(need)
            sizes = self.body_split.sizes()
            if not keep_user_width or not sizes or sizes[0] < need:
                total = max(sum(sizes), self.width() - 20)
                self.body_split.setSizes([need, max(total - need, 400)])
        except Exception:
            pass

    def _build_device_box(self):
        box = QGroupBox(tr("Gravador"))
        g = QGridLayout(box)
        self.cb_backend = QComboBox()
        for key, label in BACKENDS.items():
            self.cb_backend.addItem(tr(label), key)
        self.cb_backend.setToolTip(
            tr("libusb: Linux ou Windows com driver WinUSB (Zadig).\n"
            "CH341DLL: Windows com o driver oficial da WCH (CH341PAR).\n"
            "Automático: tenta a DLL oficial e depois libusb."))
        self.btn_connect = QPushButton(tr("Conectar"))
        self.btn_connect.setObjectName("primary")
        self.btn_connect.clicked.connect(lambda: self.toggle_connection())
        self.lbl_dev = QLabel(tr("—"))
        self.lbl_dev.setObjectName("muted")
        self.lbl_dev.setWordWrap(True)
        g.addWidget(QLabel(tr("Driver")), 0, 0)
        g.addWidget(self.cb_backend, 0, 1)
        g.addWidget(self.btn_connect, 1, 0, 1, 2)
        g.addWidget(self.lbl_dev, 2, 0, 1, 2)
        return box

    def _build_chip_box(self):
        box = QGroupBox(tr("Memória"))
        v = QVBoxLayout(box)
        self.cb_mode = QComboBox()
        self.cb_mode.addItem(tr("SPI Flash (25xx / BIOS)"), MODE_SPI)
        self.cb_mode.addItem(tr("EEPROM I²C (24xx)"), MODE_I2C)
        v.addWidget(self.cb_mode)

        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText(tr("Buscar modelo (ex.: W25Q64, MX25L)…"))
        v.addWidget(self.ed_search)
        self.cb_chip = QComboBox()
        self.cb_chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        v.addWidget(self.cb_chip)

        row = QHBoxLayout()
        self.btn_detect = QPushButton(tr("Detectar (F5)"))
        self.btn_detect.setObjectName("primary")
        self.btn_detect.clicked.connect(lambda: self.op_detect())
        row.addWidget(self.btn_detect)
        self.cb_i2c_speed = QComboBox()
        for k in (20, 100, 400, 750):
            self.cb_i2c_speed.addItem(f"I²C {k} kHz", k)
        self.cb_i2c_speed.setCurrentIndex(1)
        row.addWidget(self.cb_i2c_speed)
        v.addLayout(row)

        g = QGridLayout()
        g.setVerticalSpacing(3)
        self.info = {}
        for i, (key, label) in enumerate([("vendor", "Fabricante"), ("model", "Modelo"),
                                          ("id", "JEDEC ID"), ("size", "Tamanho"),
                                          ("volt", "Tensão"), ("sr", "Status"),
                                          ("origin", "Fonte")]):
            a = QLabel(tr(label))
            a.setObjectName("muted")
            b = QLabel(tr("—"))
            b.setObjectName("value")
            b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(a, i, 0)
            g.addWidget(b, i, 1)
            self.info[key] = b
        v.addLayout(g)
        self.lbl_warn = QLabel("")
        self.lbl_warn.setObjectName("warn")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.hide()
        v.addWidget(self.lbl_warn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{C['border']}")
        v.addWidget(sep)
        self.lbl_db = QLabel(tr("Banco de chips: —"))
        self.lbl_db.setObjectName("muted")
        self.lbl_db.setWordWrap(True)
        v.addWidget(self.lbl_db)
        row2 = QHBoxLayout()
        self.ck_autoupd = QCheckBox(tr("Atualizar sozinho (1x/dia)"))
        self.ck_autoupd.setChecked(True)
        self.ck_autoupd.setToolTip(tr("Ao abrir o programa, procura chips novos no flashrom, "
                                   "IMSProg e AsProgrammer (no máximo uma vez por dia)."))
        self.btn_upd = QPushButton(tr("Atualizar agora"))
        self.btn_upd.clicked.connect(lambda: self.update_chip_db())
        row2.addWidget(self.ck_autoupd, 1)
        row2.addWidget(self.btn_upd)
        v.addLayout(row2)
        return box

    def _build_ops_box(self):
        box = QGroupBox(tr("Operações"))
        v = QVBoxLayout(box)
        g = QGridLayout()
        self.btn_read = QPushButton(tr("Ler (F6)"))
        self.btn_write = QPushButton(tr("Gravar (F7)"))
        self.btn_verify = QPushButton(tr("Verificar (F8)"))
        self.btn_erase = QPushButton(tr("Apagar"))
        self.btn_erase.setObjectName("danger")
        self.btn_blank = QPushButton(tr("Teste de branco"))
        self.btn_unprotect = QPushButton(tr("Desproteger"))
        self.btn_auto = QPushButton(tr("Auto: apagar → gravar → verificar"))
        self.btn_auto.setObjectName("primary")
        self.btn_read.clicked.connect(lambda: self.op_read())
        self.btn_write.clicked.connect(lambda: self.op_write())
        self.btn_verify.clicked.connect(lambda: self.op_verify())
        self.btn_erase.clicked.connect(lambda: self.op_erase())
        self.btn_blank.clicked.connect(lambda: self.op_blank())
        self.btn_unprotect.clicked.connect(lambda: self.op_unprotect())
        self.btn_auto.clicked.connect(lambda: self.op_write(full=True))
        g.addWidget(self.btn_read, 0, 0)
        g.addWidget(self.btn_write, 0, 1)
        g.addWidget(self.btn_verify, 1, 0)
        g.addWidget(self.btn_erase, 1, 1)
        g.addWidget(self.btn_blank, 2, 0)
        g.addWidget(self.btn_unprotect, 2, 1)
        g.addWidget(self.btn_auto, 3, 0, 1, 2)
        v.addLayout(g)
        self.ck_smart = QCheckBox(tr("Gravação inteligente (só setores alterados)"))
        self.ck_smart.setChecked(True)
        self.ck_smart.setToolTip(tr("Lê o chip, compara e apaga/grava apenas o que mudou.\n"
                                 "Muito mais rápido para pequenas alterações."))
        self.ck_verify = QCheckBox(tr("Verificar após gravar"))
        self.ck_verify.setChecked(True)
        self.ck_unprot = QCheckBox(tr("Desproteger automaticamente"))
        self.ck_double = QCheckBox(tr("Ler duas vezes e comparar (detecta mau contato)"))
        self.ck_double.setChecked(True)
        self.ck_turbo = QCheckBox(tr("Gravação turbo (libusb, conferida)"))
        self.ck_turbo.setChecked(True)
        self.ck_turbo.setToolTip(tr("Grava várias páginas por vez sem consultar o chip a cada página e "
                                 "confere cada lote; páginas que falharem são regravadas do jeito normal.\n"
                                 "Só funciona no driver libusb."))
        self.ck_alerts = QCheckBox(tr("Avisar quando terminar (janela no centro da tela)"))
        self.ck_alerts.setChecked(True)
        self.ck_alerts.setToolTip(tr("Mostra uma janela de aviso ao terminar de ler, gravar, verificar, "
                                     "apagar etc. Útil quando você não está olhando para o computador."))
        self.ck_backup_write = QCheckBox(tr("Backup do chip antes de gravar"))
        self.ck_backup_write.setToolTip(tr("Lê e salva o conteúdo atual do chip numa pasta escolhida por você "
                                        "antes de cada gravação."))
        for w in (self.ck_smart, self.ck_verify, self.ck_unprot, self.ck_double, self.ck_turbo,
                  self.ck_backup_write, self.ck_alerts):
            v.addWidget(w)
        self.ed_job = QLineEdit()
        self.ed_job.setPlaceholderText(tr("Serviço / cliente / placa (vai para o histórico)"))
        v.addWidget(self.ed_job)
        return box

    def _build_actions(self):
        def act(text, slot, key=None, tip=None):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            if tip:
                a.setToolTip(tip)
            a.triggered.connect(lambda *_: slot())
            self.addAction(a)
            return a

        self.a_open = act(tr("Abrir"), self.open_file, "Ctrl+O", tr("Abrir arquivo .bin/.hex (Ctrl+O)"))
        self.a_save = act(tr("Salvar"), self.save_file, "Ctrl+S", tr("Salvar buffer (Ctrl+S)"))
        self.a_saveas = act(tr("Salvar como…"), lambda: self.save_file(True), "Ctrl+Shift+S")
        self.a_compare = act(tr("Comparar com arquivo"), self.compare_file, None,
                             tr("Destaca em vermelho os bytes diferentes"))
        self.a_clearcmp = act(tr("Limpar comparação"), lambda: self.hex.set_mask(None))
        self.a_goto = act(tr("Ir para…"), self.goto_dialog, "Ctrl+G", tr("Ir para endereço (Ctrl+G)"))
        self.a_find = act(tr("Pesquisar"), self.find_dialog, "Ctrl+F",
                          tr("Pesquisar texto ou hex no buffer, ex.: version (Ctrl+F)"))
        self.a_findnext = act(tr("Buscar próximo"), lambda: self.find_next(), "F3")
        self._search_dlg = None
        self.a_new = act(tr("Novo buffer (0xFF)"), self.new_buffer, None,
                         tr("Cria um buffer em branco do tamanho da memória selecionada"))
        self.a_about = act(tr("Sobre"), self.about)
        for a in (self.a_open, self.a_save, self.a_saveas, None, self.a_compare, self.a_clearcmp,
                  None, self.a_goto, self.a_find, None, self.a_new):
            if a is None:
                self.toolbar.addSeparator()
            else:
                self.toolbar.addAction(a)
        self.toolbar.addSeparator()
        self.tools_menu = QMenu(tr("Ferramentas"), self)
        self.tool_actions = {}
        for key, text, fn in (
                ("clip", "Testar o clipe (estabilidade do contato)", self.tool_clip_test),
                ("analyze", "Analisar dump (saúde e conteúdo)", self.tool_analyze),
                (None, None, None),
                ("regions", "Regiões Intel (BIOS / ME / GbE)…", self.tool_regions),
                ("cap", "Remover cabeçalho .CAP", self.tool_strip_cap),
                ("transplant", "Transplante de dados da placa…", self.tool_transplant),
                (None, None, None),
                ("edid", "Editor de EDID…", self.tool_edid),
                ("map", "Comparação visual com arquivo…", self.tool_compare_map),
                (None, None, None),
                ("bench", "Benchmark do gravador", self.tool_benchmark),
                ("history", "Histórico de serviços…", self.tool_history),
                (None, None, None),
                ("lang", "Idioma: English / Português", self.tool_language)):
            if key is None:
                self.tools_menu.addSeparator()
                continue
            a = QAction(tr(text), self)
            a.triggered.connect(lambda *_, f=fn: f())
            self.tools_menu.addAction(a)
            self.tool_actions[key] = a
        tb = QToolButton()
        tb.setText(tr("Ferramentas") + tr(" ▾"))
        tb.setMenu(self.tools_menu)
        tb.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.toolbar.addWidget(tb)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.a_about)
        # menu no topo da janela: sempre visível, mesmo com a janela estreita
        mb = self.menuBar()
        m_file = mb.addMenu(tr("Arquivo"))
        for act_ in (self.a_open, self.a_save, self.a_saveas, self.a_new):
            m_file.addAction(act_)
        mb.addMenu(self.tools_menu)
        m_help = mb.addMenu(tr("Ajuda"))
        m_help.addAction(self.a_about)
        act(tr("Detectar"), lambda: self.op_detect(), "F5")
        act(tr("Ler"), lambda: self._if_ready(self.btn_read, self.op_read), "F6")
        act(tr("Gravar"), lambda: self._if_ready(self.btn_write, self.op_write), "F7")
        act(tr("Verificar"), lambda: self._if_ready(self.btn_verify, self.op_verify), "F8")
        self._find_pattern = b""

    def _if_ready(self, button, fn):
        if button.isEnabled():
            fn()

    # ======================================================== settings
    def _load_settings(self):
        s = self.settings
        idx = self.cb_backend.findData(s.value("backend", "auto"))
        self.cb_backend.setCurrentIndex(max(0, idx))
        midx = self.cb_mode.findData(s.value("mode", MODE_SPI))
        self.cb_mode.setCurrentIndex(max(0, midx))
        self.mode = self.cb_mode.currentData()
        for ck, key in ((self.ck_smart, "smart"), (self.ck_verify, "verify"),
                        (self.ck_unprot, "unprot")):
            ck.setChecked(s.value(key, ck.isChecked(), type=bool))
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        split_state = s.value("body_split")
        if split_state is not None:
            self.body_split.restoreState(split_state)
        self._fit_side_panel(keep_user_width=True)
        self._last_chip = s.value("chip", "")
        self.ck_autoupd.setChecked(s.value("autoupd", True, type=bool))
        for ck, key in ((self.ck_double, "double"), (self.ck_turbo, "turbo"),
                        (self.ck_backup_write, "backup_write"), (self.ck_alerts, "alerts")):
            ck.setChecked(s.value(key, ck.isChecked(), type=bool))

    def closeEvent(self, e):
        if self.busy:
            self._warn( tr("Aguarde a operação terminar (ou cancele) antes de sair."))
            e.ignore()
            return
        if self.dirty and not self._confirm(tr("O buffer tem alterações não salvas. Sair mesmo assim?")):
            e.ignore()
            return
        s = self.settings
        s.setValue("backend", self.cb_backend.currentData())
        s.setValue("mode", self.mode)
        s.setValue("smart", self.ck_smart.isChecked())
        s.setValue("verify", self.ck_verify.isChecked())
        s.setValue("unprot", self.ck_unprot.isChecked())
        s.setValue("autoupd", self.ck_autoupd.isChecked())
        s.setValue("double", self.ck_double.isChecked())
        s.setValue("turbo", self.ck_turbo.isChecked())
        s.setValue("backup_write", self.ck_backup_write.isChecked())
        s.setValue("alerts", self.ck_alerts.isChecked())
        s.setValue("geometry", self.saveGeometry())
        s.setValue("body_split", self.body_split.saveState())
        if self.chip is not None:
            s.setValue("chip", self.chip.name)
        self._disconnect()
        e.accept()

    # =========================================================== estado
    def log(self, msg: str, level: str = "info"):
        color = {"info": C["text"], "ok": C["ok"], "warn": C["warn"], "err": C["danger"],
                 "muted": C["muted"]}[level]
        ts = datetime.now().strftime("%H:%M:%S")
        safe = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        self.logbox.appendHtml(f'<span style="color:{C["dim"]}">{ts}</span> '
                               f'<span style="color:{color}">{safe}</span>')

    @Slot(str)
    def _on_worker_log(self, msg):
        self.log(msg, "muted" if msg.startswith("Traceback") else "info")

    def _refresh_state(self):
        connected = self.backend is not None
        idle = not self.busy
        spi = self.mode == MODE_SPI
        has_chip = self.chip is not None
        has_buf = bool(self.buffer)
        self.btn_connect.setText(tr("Desconectar") if connected else tr("Conectar"))
        self.btn_connect.setEnabled(idle)
        self.cb_backend.setEnabled(idle and not connected)
        self.cb_mode.setEnabled(idle)
        self.cb_chip.setEnabled(idle)
        self.ed_search.setEnabled(idle)
        self.btn_detect.setEnabled(idle and connected and spi)
        self.btn_detect.setVisible(spi)
        self.cb_i2c_speed.setVisible(not spi)
        self.cb_i2c_speed.setEnabled(idle)
        can = idle and connected and has_chip
        self.btn_read.setEnabled(can)
        self.btn_erase.setEnabled(can)
        self.btn_blank.setEnabled(can)
        self.btn_write.setEnabled(can and has_buf)
        self.btn_verify.setEnabled(can and has_buf)
        self.btn_auto.setEnabled(can and has_buf and spi)
        self.btn_unprotect.setEnabled(can and spi)
        self.ck_smart.setEnabled(spi)
        self.ck_unprot.setEnabled(spi)
        self.btn_cancel.setEnabled(self.busy)
        for a in (self.a_open, self.a_new, self.a_compare):
            a.setEnabled(idle)
        self.a_save.setEnabled(has_buf)
        self.a_saveas.setEnabled(has_buf)
        if connected:
            self.pill.setText(tr("● Conectado — {d}", d=self.backend.describe()))
            self.pill.setStyleSheet(f"background:#12341F; color:{C['ok']};")
        else:
            self.pill.setText(tr("● Desconectado"))
            self.pill.setStyleSheet(f"background:{C['panel2']}; color:{C['muted']};")
        title = APP_NAME
        if self.file_path:
            title += f" — {os.path.basename(self.file_path)}"
        elif self.buffer:
            title += " — (leitura do chip)"
        if self.dirty:
            title += " *"
        self.setWindowTitle(title)

    def _update_buffer_info(self):
        if self.buffer:
            self.lbl_file.setText(os.path.basename(self.file_path) if self.file_path else tr("Leitura do chip"))
            self.lbl_size.setText(f"{_n(len(self.buffer))} bytes")
            self.lbl_crc.setText(f"CRC32 {fileio.crc32(self.buffer):08X}  •  Soma {fileio.checksum16(self.buffer):04X}")
        else:
            self.lbl_file.setText(tr("Sem arquivo"))
            self.lbl_size.setText("")
            self.lbl_crc.setText("")

    def _set_buffer(self, data: bytes, path: str | None, dirty=False):
        self.buffer = bytearray(data)
        self.file_path = path
        self.dirty = dirty
        self.hex.set_data(self.buffer)
        self._update_buffer_info()
        self._refresh_state()

    @Slot(int)
    def _on_cursor(self, off):
        if self.buffer:
            b = self.buffer[off]
            self.lbl_cursor.setText(tr("Offset 0x{o} ({d})   Valor 0x{v} = {b}", o=f"{off:08X}", d=off, v=f"{b:02X}", b=b))

    @Slot()
    def _on_edited(self):
        if not self.dirty:
            self.dirty = True
            self._refresh_state()
        if self._crc_timer is None:
            self._crc_timer = QTimer(self)
            self._crc_timer.setSingleShot(True)
            self._crc_timer.timeout.connect(self._update_buffer_info)
        self._crc_timer.start(400)

    def _confirm(self, text: str) -> bool:
        r = QMessageBox.question(self, APP_NAME, text,
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes

    # ========================================================= chips
    def _on_mode_changed(self):
        self.mode = self.cb_mode.currentData()
        self.chip = None
        self.detected_id = None
        self._populate_chips()
        self._refresh_state()

    def _chip_list(self):
        if self.mode == MODE_SPI:
            return self._extra_chips + chipdb.spi_chips()
        return chipdb.eeprom_chips()

    def _populate_chips(self):
        q = self.ed_search.text().strip().upper()
        current = self.chip.name if self.chip else (getattr(self, "_last_chip", "") or "")
        self.cb_chip.blockSignals(True)
        self.cb_chip.clear()
        self.cb_chip.addItem(tr("— selecione ou detecte —"), None)
        sel = 0
        for c in self._chip_list():
            names = " ".join(getattr(c, "aliases", [c.name]))
            hay = f"{c.name} {names} {getattr(c, 'vendor', '')}".upper()
            if q and q not in hay:
                continue
            if self.mode == MODE_SPI:
                text = f"{c.name}  •  {c.vendor}  •  {chipdb.human_size(c.size)}"
                if c.is_18v:
                    text += "  •  1,8 V"
            else:
                text = f"{c.name}  •  {chipdb.human_size(c.size)}"
            self.cb_chip.addItem(text, c)
            if c.name == current:
                sel = self.cb_chip.count() - 1
        if sel == 0 and q:
            qn = "".join(ch for ch in q if ch.isalnum())
            for i in range(1, self.cb_chip.count()):
                c = self.cb_chip.itemData(i)
                if any("".join(ch for ch in n.upper() if ch.isalnum()) == qn
                       for n in getattr(c, "aliases", [c.name])):
                    sel = i          # nome digitado igual ao de um chip
                    break
            else:
                if self.cb_chip.count() == 2:
                    sel = 1          # a busca deixou um único modelo
        self.cb_chip.setCurrentIndex(sel)
        self.cb_chip.blockSignals(False)
        self._on_chip_selected()

    def _on_chip_selected(self):
        self.chip = self.cb_chip.currentData()
        self._show_chip_info()
        self._refresh_state()

    def _show_chip_info(self, sr_text: str | None = None):
        c = self.chip
        warn = ""
        if c is None:
            for k in self.info:
                self.info[k].setText(tr("—"))
        elif self.mode == MODE_SPI:
            self.info["vendor"].setText(c.vendor)
            self.info["model"].setText(c.name)
            idv = self.detected_id if self.detected_id is not None else c.jedec
            self.info["id"].setText(f"{idv:06X}" + ("" if self.detected_id is not None else " (banco)"))
            self.info["size"].setText(chipdb.human_size(c.size))
            self.info["volt"].setText(f"{c.voltage.replace('.', ',')} V")
            self.info["origin"].setText(c.origin if not c.generic else tr("perfil genérico"))
            if c.is_18v:
                warn = ("Memória de 1,8 V! O CH341A trabalha em 3,3 V — use o adaptador de "
                        "1,8 V, senão o chip pode ser danificado.")
            if c.generic:
                warn = (warn + "\n" if warn else "") + (
                    "Chip não está no banco: usando parâmetros genéricos (página 256 B, "
                    "setor 4 KiB). Confira o modelo impresso no chip.")
        else:
            self.info["vendor"].setText(tr("—"))
            self.info["model"].setText(c.name)
            self.info["id"].setText(tr("— (I²C não tem ID)"))
            self.info["size"].setText(chipdb.human_size(c.size))
            self.info["volt"].setText(tr("1,8–5 V (conforme o chip)"))
            self.info["origin"].setText(tr("—"))
        if sr_text is not None:
            self.info["sr"].setText(sr_text)
        elif c is None or self.mode == MODE_I2C:
            self.info["sr"].setText(tr("—"))
        self.lbl_warn.setText(warn)
        self.lbl_warn.setVisible(bool(warn))

    # ===================================================== conexão
    def toggle_connection(self):
        if self.backend is not None:
            self._disconnect()
            self.log(tr("Gravador desconectado."))
            self._refresh_state()
            return
        kind = self.cb_backend.currentData()
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            self.backend = create_backend(kind)
        except CH341Error as e:
            self.backend = None
            self.log(str(e), "err")
            self._warn( str(e))
        except Exception as e:   # erro inesperado do driver: mostra em vez de fechar
            self.backend = None
            self.log(f"Erro ao abrir o driver: {e!r}", "err")
            self._warn( f"Erro ao abrir o driver:\n{e}")
        finally:
            self.unsetCursor()
        if self.backend:
            self.lbl_dev.setText(self.backend.describe())
            self.log(tr("Conectado via {d}.", d=self.backend.describe()), "ok")
            if self.mode == MODE_SPI:
                QTimer.singleShot(0, self.op_detect)
        self._refresh_state()

    def _disconnect(self):
        if self.backend is not None:
            try:
                self.backend.close()
            except Exception:
                pass
        self.backend = None
        self.lbl_dev.setText(tr("—"))

    # ============================================== execução de tarefas
    def _run(self, title: str, fn, on_done, retry=None, alert: str | None = None):
        if self.busy:
            return
        self.busy = True
        self._alert_default = alert
        self._pending_retry = retry
        self._stage = None
        self.progress.setValue(0)
        self.lbl_stage.setText(title)
        self.lbl_speed.setText("")
        self.statusBar().showMessage(title + tr("…"))
        self._op_t0 = time.monotonic()
        self._refresh_state()

        def wrapped(w):
            return fn(w)

        self._on_done_cb = on_done
        self._thread, self._worker = W.start(self, wrapped, self._task_done, self._task_failed,
                                             self._task_progress, self._on_worker_log)

    def cancel_operation(self):
        if self._worker:
            self._worker.cancel()
            self.lbl_stage.setText(tr("Cancelando…"))

    @Slot(object, object, str)
    def _task_progress(self, done, total, stage):
        now = time.monotonic()
        if stage != self._stage:
            self._stage = stage
            self._stage_t0 = now
            self.lbl_stage.setText(stage)
        total = max(int(total), 1)
        done = int(done)
        self.progress.setValue(int(done * 1000 / total))
        el = now - self._stage_t0
        if stage.startswith("Apagando chip"):
            self.lbl_speed.setText(f"{_fmt_eta(el)} decorrido")
        elif el > 0.3 and done > 0:
            rate = done / el
            eta = (total - done) / rate if rate else 0
            kib = f"{rate / 1024:.1f}".replace(".", ",")
            self.lbl_speed.setText(f"{kib} KiB/s • falta {_fmt_eta(eta)}")

    def _finish(self):
        self.busy = False
        self._worker = None
        self._thread = None
        self._refresh_state()
        if self._db_pending_reload:
            QTimer.singleShot(0, self._apply_db_reload)

    @Slot(object)
    def _task_done(self, result):
        elapsed = time.monotonic() - self._op_t0
        self.progress.setValue(1000)
        self.lbl_speed.setText(tr("concluído em {t}", t=_fmt_eta(elapsed)))
        self.statusBar().showMessage(tr("Pronto"))
        cb = self._on_done_cb
        alert = self._alert_default
        self._finish()
        self._box_shown = False
        self._alert_text = None
        self._alert_ok = True
        try:
            cb(result)
        except Exception as e:
            self.log(f"Erro ao processar o resultado: {e!r}", "err")
        # aviso de conclusão (só se nenhum aviso de problema já apareceu)
        if alert and self.ck_alerts.isChecked() and not self._box_shown:
            self._completion_box(self._alert_text or alert, elapsed, self._alert_ok)

    def _warn(self, text: str):
        """Aviso de problema (marca que já houve uma janela, para não duplicar com o de conclusão)."""
        self._box_shown = True
        QMessageBox.warning(self, APP_NAME, text)

    def _completion_box(self, text: str, elapsed: float, ok: bool = True):
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Information if ok else QMessageBox.Icon.Warning)
        chip = self.chip.name if self.chip else ""
        box.setText(f"<h3>{text}</h3>")
        box.setInformativeText(" • ".join(x for x in (chip, tr("tempo: {t}", t=_fmt_eta(elapsed))) if x))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        try:
            QApplication.beep()
            QApplication.alert(self)          # pisca na barra de tarefas se estiver minimizado
            box.adjustSize()
            scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            box.move(scr.center() - box.rect().center())
        except Exception:
            pass
        box.exec()

    @Slot(object)
    def _task_failed(self, exc):
        retry = self._pending_retry
        self._finish()
        self.progress.setValue(0)
        self.lbl_speed.setText("")
        if isinstance(exc, Cancelled):
            self.lbl_stage.setText(tr("Cancelado"))
            self.log(tr("Operação cancelada pelo usuário."), "warn")
            self.statusBar().showMessage(tr("Cancelado"))
            return
        self.lbl_stage.setText(tr("Erro"))
        self.statusBar().showMessage(tr("Erro"))
        self.log(str(exc) or repr(exc), "err")
        if isinstance(exc, ProtectedError) and retry:
            if self._confirm(f"{exc}\n\n" + tr("Remover a proteção e continuar?")):
                retry()
            return
        if isinstance(exc, CH341Error):
            self._warn( str(exc))
        else:
            QMessageBox.critical(self, APP_NAME, f"Erro inesperado: {exc!r}\nDetalhes no log.")

    # ---------- fábricas de dispositivo (rodam na thread de trabalho)
    def _spi(self, w, chip=None):
        f = SpiFlash(self.backend, chip if chip is not None else self.chip,
                     progress=w.report, cancel=w.is_cancelled, log=w.log.emit)
        f.turbo = self.ck_turbo.isChecked()
        return f

    def _eeprom(self, w):
        return Eeprom24(self.backend, self.chip, progress=w.report, cancel=w.is_cancelled,
                        log=w.log.emit)

    def _device(self, w):
        return self._spi(w) if self.mode == MODE_SPI else self._eeprom(w)

    def _sr_text(self, info) -> str:
        t = f"SR1=0x{info['sr1']:02X}"
        if info.get("sr2") is not None:
            t += f" SR2=0x{info['sr2']:02X}"
        return t + ("  🔒 " + tr("protegido") if info["protected"] else "  " + tr("livre"))

    # ------------------------------------------------------------ histórico
    def _hist(self, op: str, data: bytes | None = None, result: str = "", file: str = "",
              size: int | None = None) -> int | None:
        if self.history is None:
            return None
        try:
            chip = self.chip
            return self.history.add(
                op, chip.name if chip else "",
                f"{self.detected_id:06X}" if self.detected_id is not None and self.mode == MODE_SPI else "",
                size if size is not None else (len(data) if data is not None else (chip.size if chip else 0)),
                f"{fileio.crc32(data):08X}" if data is not None else "",
                fileio.sha256(data) if data is not None else "",
                file, result, self.ed_job.text().strip(), "")
        except Exception as e:      # o histórico nunca pode atrapalhar a operação
            self.log(tr("Não foi possível registrar no histórico: {e}", e=e), "muted")
            return None

    def _log_health(self, data: bytes):
        rep = health.analyze_dump(data, self.chip.size if self.chip else None)
        for f in rep.findings:
            if f.level in (health.WARN, health.BAD):
                self.log(tr(f.text), "err" if f.level == health.BAD else "warn")
        ident = identify.identify(data)
        if ident.kind != "Desconhecido":
            self.log(tr("Conteúdo: {s}", s=ident.summary()), "muted")
        return rep

    # ============================================================ operações
    def op_detect(self):
        if not self.backend or self.busy or self.mode != MODE_SPI:
            return

        def job(w):
            f = self._spi(w, chip=None)
            jedec, cands = f.detect()
            info = None
            if cands:
                f.chip = cands[0]
                info = f.protection_info()
            return jedec, cands, info

        def done(res):
            jedec, cands, info = res
            self.detected_id = jedec
            mfr = chipdb.vendor_name(jedec >> 16)
            if not cands:
                self.log(tr("ID {id} ({mfr}) não reconhecido. Selecione o modelo manualmente.",
                            id=f"{jedec:06X}", mfr=mfr), "warn")
                self.chip = None
                self._show_chip_info()
                self.info["id"].setText(f"{jedec:06X}")
                self.info["vendor"].setText(mfr)
                return
            chip = cands[0]
            if chip.generic and chip not in self._extra_chips:
                self._extra_chips = [chip]
            self.ed_search.blockSignals(True)
            self.ed_search.clear()
            self.ed_search.blockSignals(False)
            self.chip = chip
            self._populate_chips()
            self.detected_id = jedec
            self._show_chip_info(self._sr_text(info) if info else None)
            names = " / ".join(c.name for c in cands[:6]) + (" …" if len(cands) > 6 else "")
            self.log(tr("Detectado {names} — ID {id}, {size}.", names=names, id=f"{jedec:06X}",
                        size=chipdb.human_size(chip.size)), "ok")
            if len(cands) > 1:
                self.log(tr("Mais de um modelo usa este ID; confira a marcação do chip."), "warn")
            if chip.is_18v:
                self.log(tr("ATENÇÃO: memória de 1,8 V — use o adaptador!"), "err")
            if info and info["protected"]:
                self.log(tr("A memória está com proteção de escrita ativa."), "warn")

        self._run(tr("Detectando"), job, done)

    def op_read(self):
        if self.dirty and not self._confirm(tr("O buffer tem alterações não salvas que serão "
                                               "substituídas pela leitura. Continuar?")):
            return
        speed = self.cb_i2c_speed.currentData()
        double = self.ck_double.isChecked()

        def job(w):
            if self.mode == MODE_I2C:
                self.backend.set_i2c_speed(speed)
            dev = self._device(w)
            data = dev.read()
            second = dev.read(stage=tr("Segunda leitura (conferência)")) if double else None
            return data, second

        def done(res):
            data, second = res
            self._set_buffer(data, None)
            self.log(tr("Leitura concluída: {n} bytes, CRC32 {crc}.", n=_n(len(data)),
                        crc=f"{fileio.crc32(data):08X}"), "ok")
            result = "ok"
            if second is not None:
                diff, blocks = health.compare_reads(data, second)
                if diff:
                    result = tr("leituras diferentes")
                    self.log(tr("As duas leituras são DIFERENTES ({n} bytes, a partir de 0x{a}). "
                                "Mau contato: reposicione o clipe e leia de novo antes de usar este dump.",
                                n=_n(diff), a=f"{blocks[0]:X}"), "err")
                    mask = bytearray(len(data))
                    for off in blocks:
                        for i in range(off, min(off + 4096, len(data))):
                            if data[i] != second[i]:
                                mask[i] = 1
                    self.hex.set_mask(mask)
                    self.hex.goto(blocks[0])
                    self._warn( tr("As duas leituras não bateram: há mau contato. "
                                                           "Não use este dump."))
                else:
                    self.log(tr("Leitura dupla conferida: as duas leituras são idênticas."), "ok")
            rep = self._log_health(data)
            if rep.worst == health.BAD and result == "ok":
                result = tr("suspeito")
                self._alert_text = tr("Leitura concluída, mas o conteúdo parece suspeito (veja o log).")
                self._alert_ok = False
            self._last_read_hist = self._hist("Leitura", data, result)
            self.log(tr("Os dados estão no buffer. Use Salvar (Ctrl+S) para gravar em arquivo."), "muted")

        self._run(tr("Lendo"), job, done, alert=tr("Leitura concluída!"))

    def _check_size_for_write(self) -> bool:
        size = len(self.buffer)
        if size > self.chip.size:
            self._warn(
                                tr("O arquivo ({a} bytes) é maior que a memória ({b} bytes).",
                                   a=_n(size), b=_n(self.chip.size)))
            return False
        if size < self.chip.size:
            return self._confirm(
                tr("O arquivo tem {a} bytes e a memória {b} bytes.\n"
                   "Gravar a partir do endereço 0 mesmo assim?", a=_n(size), b=_n(self.chip.size)))
        return True

    def _backup_dir(self) -> str | None:
        """Pasta do backup antes de gravar (pergunta na primeira vez)."""
        if not self.ck_backup_write.isChecked():
            return None
        folder = self.settings.value("backup_dir", "")
        if not folder or not os.path.isdir(folder):
            folder = QFileDialog.getExistingDirectory(self, tr("Pasta para os backups antes de gravar"))
            if not folder:
                return ""
            self.settings.setValue("backup_dir", folder)
        return folder

    def op_write(self, full: bool = False, force_unprotect: bool = False, region=None):
        if not self.buffer or not self.chip:
            return
        if region is None and not self._check_size_for_write():
            return
        smart = self.ck_smart.isChecked() and not full
        verify = self.ck_verify.isChecked() or full
        unprot = self.ck_unprot.isChecked() or force_unprotect
        how = tr("apagar e gravar tudo") if not smart else tr("gravação inteligente")
        if region is not None:
            addr, data = region.base, bytes(self.buffer[region.base:region.limit + 1])
            what = tr("só a região {name} ({n} bytes)", name=region.name, n=_n(len(data)))
        else:
            addr, data = 0, bytes(self.buffer)
            what = tr("{n} bytes", n=_n(len(data)))
        if not force_unprotect and not self._confirm(
                tr("Gravar {what} em {chip} ({how})?\nO conteúdo atual da memória será substituído.",
                   what=what, chip=self.chip.name, how=how)):
            return
        backup_dir = self._backup_dir()
        if backup_dir == "":
            return
        speed = self.cb_i2c_speed.currentData()
        chip_name = self.chip.name

        def job(w):
            backup_path = None
            if backup_dir:
                dev = self._device(w)
                if self.mode == MODE_I2C:
                    self.backend.set_i2c_speed(speed)
                old = dev.read(stage=tr("Backup antes de gravar"))
                safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in chip_name)
                backup_path = os.path.join(backup_dir, f"{safe}_{datetime.now():%Y%m%d_%H%M%S}_antes.bin")
                fileio.save_file(backup_path, old)
            if self.mode == MODE_SPI:
                f = self._spi(w)
                stats, vr = f.write(data, addr=addr, smart=smart, verify=verify, auto_unprotect=unprot)
                return stats, vr, backup_path, f.turbo_retries
            self.backend.set_i2c_speed(speed)
            return None, self._eeprom(w).write(data, verify=verify), backup_path, 0

        def done(res):
            stats, vr, backup_path, retries = res
            if backup_path:
                self.log(tr("Backup do conteúdo anterior salvo em {p}", p=backup_path), "muted")
            if stats:
                self.log(tr("Gravação concluída: {s} setor(es) e {b} bloco(s) apagados, {p} página(s) gravadas.",
                            s=stats.erased_sectors, b=stats.erased_blocks, p=stats.programmed_pages), "ok")
                if retries:
                    self.log(tr("Turbo: {n} página(s) precisaram ser regravadas (conferidas).", n=retries),
                             "muted")
            else:
                self.log(tr("Gravação concluída."), "ok")
            self._report_verify(vr)
            result = "ok" if vr is None or vr.ok else tr("falhou na verificação")
            self._hist("Gravação" if region is None else f"Gravação ({region.name})", data, result,
                       file=self.file_path or "")
            if backup_path:
                self._hist("Backup antes de gravar", None, "ok", file=backup_path)

        self._run(tr("Gravando"), job, done, alert=tr("Gravação concluída!"),
                  retry=lambda: self.op_write(full=full, force_unprotect=True, region=region))

    def _report_verify(self, vr):
        if vr is None:
            return
        if vr.ok:
            self.log(tr(vr.summary()), "ok")
        else:
            self.log(vr.summary(), "err")
            mask = bytearray(len(self.buffer))
            for a in vr.first:
                if a < len(mask):
                    mask[a] = 1
            self.hex.set_mask(mask)
            if vr.first:
                self.hex.goto(vr.first[0])
            self._warn( vr.summary())

    def op_verify(self):
        if not self.buffer or not self.chip:
            return
        if len(self.buffer) > self.chip.size:
            self._warn( tr("O buffer é maior que a memória."))
            return
        data = bytes(self.buffer)

        def job(w):
            return self._device(w).verify(data)

        def done(vr):
            self._report_verify(vr)
            self._hist("Verificação", data, "ok" if vr.ok else tr("diferente"))

        self._run(tr("Verificando"), job, done, alert=tr("Verificação concluída: conteúdo idêntico!"))

    def op_erase(self):
        if not self._confirm(tr("Apagar TODO o conteúdo de {chip}?\nEssa ação não pode ser desfeita.",
                                chip=self.chip.name)):
            return
        self._do_erase(self.ck_unprot.isChecked())

    def _do_erase(self, unprot):
        def job(w):
            if self.mode == MODE_SPI:
                self._spi(w).erase_chip(auto_unprotect=unprot)
                return None
            return self._eeprom(w).erase()

        def done(res):
            self.log(tr("Memória apagada."), "ok")
            self._hist("Apagar", None, "ok")

        self._run(tr("Apagando"), job, done, retry=lambda: self._do_erase(True),
                  alert=tr("Memória apagada!"))

    def op_blank(self):
        def job(w):
            return self._device(w).blank_check()

        def done(res):
            ok, first = res
            if ok:
                self.log(tr("Memória em branco (tudo 0xFF)."), "ok")
                self._alert_text = tr("Memória em branco (tudo 0xFF).")
            else:
                self.log(tr("A memória NÃO está em branco: primeiro byte diferente de 0xFF em 0x{a}.",
                            a=f"{first:06X}"), "warn")
                self._alert_text = tr("A memória NÃO está em branco.")
                self._alert_ok = False

        self._run(tr("Teste de branco"), job, done, alert=tr("Teste de branco concluído"))

    def op_unprotect(self):
        def job(w):
            f = self._spi(w)
            f.unprotect()
            return f.protection_info()

        def done(info):
            self._show_chip_info(self._sr_text(info))
            self.log(tr("Proteção de escrita removida."), "ok")

        self._run(tr("Desprotegendo"), job, done, alert=tr("Proteção removida!"))

    # ============================================================ ferramentas
    def tool_clip_test(self):
        if not self.backend or self.mode != MODE_SPI:
            QMessageBox.information(self, APP_NAME, tr("Conecte o gravador (modo SPI) para testar o clipe."))
            return

        def job(w):
            f = self._spi(w, chip=None)
            f.chip = self.chip
            return health.clip_test(f, rounds=20, progress=w.report)

        def done(res):
            ok = res.stable
            self.log(tr(res.summary()) if ok else res.summary(), "ok" if ok else "err")
            if not ok:
                self._warn( res.summary())

        self._run(tr("Testando o clipe"), job, done, alert=tr("Contato estável!"))

    def tool_analyze(self):
        if not self.buffer:
            QMessageBox.information(self, APP_NAME, tr("Leia um chip ou abra um arquivo primeiro."))
            return
        data = bytes(self.buffer)
        rep = health.analyze_dump(data, self.chip.size if self.chip else None)
        ident = identify.identify(data)
        tools.ReportDialog(self, tr("Análise do dump"), tools.health_html(rep, ident)).exec()

    def tool_edid(self):
        if not self.buffer:
            QMessageBox.information(self, APP_NAME, tr("Leia a EEPROM (24C02) ou abra um arquivo de EDID."))
            return
        try:
            dlg = tools.EdidDialog(self, self.buffer)
        except ValueError as e:
            self._warn( tr(str(e)))
            return
        dlg.applied.connect(lambda: self._buffer_changed(tr("EDID atualizado no buffer (checksum corrigido).")))
        dlg.exec()

    def _buffer_changed(self, msg: str):
        self.dirty = True
        self.hex.set_data(self.buffer, keep_position=True)
        self._update_buffer_info()
        self._refresh_state()
        self.log(msg, "ok")

    def tool_regions(self):
        if not self.buffer:
            QMessageBox.information(self, APP_NAME, tr("Leia o chip ou abra uma imagem completa primeiro."))
            return
        tools.RegionsDialog(self, self.buffer, self._write_region, self._buffer_changed).exec()

    def _write_region(self, region):
        if not self.backend or not self.chip:
            QMessageBox.information(self, APP_NAME, tr("Conecte o gravador e selecione a memória."))
            return
        if region.limit >= self.chip.size:
            self._warn( tr("A região não cabe na memória selecionada."))
            return
        self.op_write(region=region)

    def tool_strip_cap(self):
        if not self.buffer or not self.chip:
            QMessageBox.information(self, APP_NAME, tr("Abra o arquivo .CAP e selecione a memória primeiro."))
            return
        res = intel.strip_capsule(bytes(self.buffer), self.chip.size)
        if not res:
            QMessageBox.information(self, APP_NAME,
                                    tr("Não reconheci um cabeçalho para remover (o arquivo já tem o "
                                       "tamanho do chip, ou o formato é diferente)."))
            return
        self.buffer[:] = res[0]
        self._buffer_changed(tr(res[1]) + " " + tr("Agora o buffer tem o tamanho do chip."))

    def tool_compare_map(self):
        if not self.buffer:
            QMessageBox.information(self, APP_NAME, tr("Leia o chip ou abra um arquivo primeiro."))
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Comparar com"), self.settings.value("lastdir", ""),
                                              "Imagens (*.bin *.rom *.hex *.cap *.fd *.img);;Todos (*)")
        if not path:
            return
        other = fileio.load_file(path)
        tools.CompareDialog(self, bytes(self.buffer), other, os.path.basename(path),
                            lambda off: self.hex.goto(off)).exec()

    def tool_transplant(self):
        def result(new, path, done):
            self._set_buffer(new, path)
            self.log(tr("BIOS gerada com: {items}. Salva em {p}", items=", ".join(tr(x) for x in done), p=path),
                     "ok")
            self._hist("Transplante", new, "ok", file=path)

        tools.TransplantDialog(self, bytes(self.buffer), result).exec()

    def tool_history(self):
        if self.history is None:
            self._warn( tr("Histórico indisponível."))
            return
        tools.HistoryDialog(self, self.history, lambda p: self.open_file(p)).exec()

    def tool_benchmark(self):
        if not self.backend or not self.chip or self.mode != MODE_SPI:
            QMessageBox.information(self, APP_NAME, tr("Conecte o gravador e selecione a memória SPI."))
            return
        r = QMessageBox.question(
            self, APP_NAME,
            tr("Incluir teste de GRAVAÇÃO?\n\nSim: grava numa área de 64 KiB no fim do chip, comparando "
               "o modo normal com o turbo. O conteúdo original dessa área é copiado antes e restaurado "
               "e conferido no final.\nNão: mede só a leitura."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Cancel:
            return
        include_write = r == QMessageBox.StandardButton.Yes
        unprot = self.ck_unprot.isChecked()

        def job(w):
            f = self._spi(w)
            if include_write and unprot:
                f._prepare_write(auto_unprotect=True)
            return benchmark.run(f, include_write=include_write, log=w.log.emit)

        def done(res):
            self.log(tr("Benchmark: ") + res.summary(), "ok")
            self._alert_text = res.summary()
            self._hist("Benchmark", None, res.summary())

        self._run(tr("Benchmark"), job, done, alert=tr("Benchmark concluído"))

    def tool_language(self):
        cur = self.settings.value("lang", "pt")
        new = "en" if cur == "pt" else "pt"
        self.settings.setValue("lang", new)
        QMessageBox.information(self, APP_NAME,
                                tr("Reinicie o programa para mudar o idioma.\n"
                                "Restart the program to change the language."))

    # ===================================================== banco de chips
    def _update_db_label(self):
        auto = chipupdate.load_auto()
        n_spi, n_i2c = len(chipdb.spi_chips()), len(chipdb.eeprom_chips())
        when = ""
        if auto.get("updated"):
            try:
                dt = datetime.fromisoformat(auto["updated"]).astimezone()
                when = " • " + tr("atualizado {d}", d=f"{dt:%d/%m/%Y %H:%M}")
            except ValueError:
                pass
        else:
            when = " • " + tr("só o banco embutido")
        self.lbl_db.setText(tr("Banco de chips: {a} SPI, {b} I²C", a=_n(n_spi), b=_n(n_i2c)) + when)

    def update_chip_db(self, silent: bool = False):
        if self._upd_running:
            return
        self._upd_running = True
        self.btn_upd.setEnabled(False)
        self.btn_upd.setText(tr("Atualizando…"))
        if not silent:
            self.log(tr("Procurando chips novos no flashrom, IMSProg e AsProgrammer…"))

        def job(w):
            return chipupdate.update(log=w.log.emit if not silent else None, progress=w.report)

        self._upd_silent = silent
        self._upd_thread, self._upd_worker = W.start(
            self, job, self._db_update_done, self._db_update_failed,
            self._db_update_progress, self._on_worker_log)

    @Slot(object, object, str)
    def _db_update_progress(self, done, total, stage):
        self.btn_upd.setText(f"{stage}…" if int(done) < int(total) else tr("Atualizando…"))

    def _db_update_end(self):
        self._upd_running = False
        self._upd_thread = None
        self._upd_worker = None
        self.btn_upd.setEnabled(True)
        self.btn_upd.setText(tr("Atualizar agora"))

    @Slot(object)
    def _db_update_done(self, rep):
        self._db_update_end()
        if rep.new_spi or rep.new_i2c or rep.changed:
            self._db_pending_reload = True
            if not self.busy:
                self._apply_db_reload()
        else:
            self._update_db_label()
        new = rep.new_spi + rep.new_i2c
        if new or not self._upd_silent:
            self.log(tr("Banco de chips: {s}.", s=rep.summary()), "ok" if not rep.failed else "warn")
        if new and not rep.first_time:
            names = ", ".join(c["name"] for c in new[:25])
            more = f" e mais {len(new) - 25}" if len(new) > 25 else ""
            self.log(f"Novos: {names}{more}.", "muted")

    @Slot(object)
    def _db_update_failed(self, exc):
        self._db_update_end()
        if not self._upd_silent:
            self.log(tr("Não foi possível atualizar o banco de chips: {e}", e=exc), "warn")

    def _apply_db_reload(self):
        if self.busy:
            return
        self._db_pending_reload = False
        chipdb.reload()
        self._extra_chips = []
        keep = self.chip
        self._populate_chips()
        if keep is not None and self.chip is None:
            # o chip selecionado era genérico/sumiu: tenta achar pelo nome
            self.ed_search.clear()
        self._update_db_label()

    # ============================================================ arquivos
    def open_file(self, path: str | None = None):
        if self.busy:
            return
        if self.dirty and not self._confirm(tr("Descartar as alterações do buffer atual?")):
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Abrir arquivo", self.settings.value("lastdir", ""),
                "Imagens (*.bin *.rom *.hex *.ihx *.cap *.fd *.img);;Todos (*)")
        if not path:
            return
        try:
            data = fileio.load_file(path)
        except (OSError, ValueError) as e:
            self._warn( tr("Não foi possível abrir: {e}", e=e))
            return
        self.settings.setValue("lastdir", os.path.dirname(path))
        if self.chip and len(data) > self.chip.size and self.mode == MODE_SPI:
            stripped = intel.strip_capsule(data, self.chip.size)
            if stripped and self._confirm(
                    tr("Este arquivo é maior que o chip e parece ter um cabeçalho de atualização "
                       "(.CAP). {why}\nRemover o cabeçalho e deixar só a imagem do chip?", why=tr(stripped[1]))):
                data = stripped[0]
                self.log(tr(stripped[1]), "ok")
        self._set_buffer(data, path)
        self._last_read_hist = None
        self.log(tr("Arquivo aberto: {p} ({n} bytes, CRC32 {c})", p=path, n=_n(len(data)), c=f"{fileio.crc32(data):08X}"))
        if self.chip and len(data) != self.chip.size:
            self.log(tr("Atenção: o arquivo tem {a} bytes e a memória selecionada {b} bytes.",
                        a=_n(len(data)), b=_n(self.chip.size)), "warn")

    def save_file(self, ask: bool = True):
        """Sempre abre a janela para escolher pasta e nome do arquivo."""
        if not self.buffer:
            return
        if self.file_path:
            suggestion = self.file_path
        else:
            folder = self.settings.value("lastdir", "") or os.path.join(os.path.expanduser("~"), "Documents")
            name = (self.chip.name if self.chip else "dump")
            name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
            suggestion = os.path.join(folder, f"{name}_{datetime.now():%Y%m%d_%H%M}.bin")
        path, chosen = QFileDialog.getSaveFileName(
            self, "Salvar como", suggestion, "Binário (*.bin);;Intel HEX (*.hex);;Todos os arquivos (*)")
        if not path:
            return
        if not os.path.splitext(path)[1]:      # nome digitado sem extensão
            path += ".hex" if "hex" in (chosen or "").lower() else ".bin"
        try:
            fileio.save_file(path, bytes(self.buffer))
        except OSError as e:
            self._warn( tr("Não foi possível salvar: {e}", e=e))
            return
        self.settings.setValue("lastdir", os.path.dirname(path))
        self.file_path = path
        self.dirty = False
        self._update_buffer_info()
        self._refresh_state()
        self.log(tr("Salvo em {p}", p=path), "ok")
        if self._last_read_hist and self.history is not None:
            try:
                self.history.set_file(self._last_read_hist, path)
            except Exception:
                pass
            self._last_read_hist = None

    def new_buffer(self):
        if not self.chip:
            QMessageBox.information(self, APP_NAME, tr("Selecione a memória primeiro."))
            return
        if self.dirty and not self._confirm(tr("Descartar as alterações do buffer atual?")):
            return
        self._set_buffer(b"\xFF" * self.chip.size, None, dirty=True)

    def compare_file(self):
        if not self.buffer:
            QMessageBox.information(self, APP_NAME, tr("Leia o chip ou abra um arquivo primeiro."))
            return
        path, _ = QFileDialog.getOpenFileName(self, "Comparar com", self.settings.value("lastdir", ""),
                                              "Imagens (*.bin *.rom *.hex *.cap *.fd *.img);;Todos (*)")
        if not path:
            return
        try:
            other = fileio.load_file(path)
        except (OSError, ValueError) as e:
            self._warn( str(e))
            return
        mask, count = fileio.diff_mask(self.buffer, other)
        if len(mask) > len(self.buffer):
            mask = mask[:len(self.buffer)]
        self.hex.set_mask(mask)
        if count == 0:
            self.log(tr("Comparação com {f}: idênticos.", f=os.path.basename(path)), "ok")
            return
        ranges = fileio.diff_ranges(mask, 12)
        txt = ", ".join(f"0x{a:X}–0x{b - 1:X}" for a, b in ranges)
        self.log(tr("Comparação com {f}: {n} byte(s) diferentes. Faixas: {r}", f=os.path.basename(path),
                    n=_n(count), r=txt + (" …" if len(ranges) == 12 else "")), "warn")
        if len(other) != len(self.buffer):
            self.log(tr("Tamanhos diferentes: buffer {a} × arquivo {b} bytes.", a=len(self.buffer), b=len(other)), "warn")
        if ranges:
            self.hex.goto(ranges[0][0])

    def goto_dialog(self):
        if not self.buffer:
            return
        text, ok = QInputDialog.getText(self, "Ir para", "Endereço (hex, ex.: 1F000 ou 0x1F000):")
        if ok and text.strip():
            try:
                self.hex.goto(int(text.strip().lower().replace("0x", ""), 16))
                self.hex.setFocus()
            except ValueError:
                self._warn( tr("Endereço inválido."))

    def find_dialog(self, query: str | None = None):
        if self._search_dlg is None:
            self._search_dlg = tools.SearchDialog(self, lambda: self.buffer,
                                                  lambda off, ln: self.hex.highlight(off, ln))
        self._search_dlg.show()
        try:
            self._search_dlg.raise_()
            self._search_dlg.activateWindow()
            self._search_dlg.ed.setFocus()
            self._search_dlg.ed.selectAll()
        except Exception:
            pass
        if query is not None:
            return self._search_dlg.run(query)

    def find_next(self, start: int | None = None):
        """F3: próxima ocorrência da última pesquisa."""
        if self._search_dlg is not None and self._search_dlg.matches:
            self._search_dlg.step(1)
        else:
            self.find_dialog()

    # ======================================================== arrastar/soltar
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.open_file(urls[0].toLocalFile())

    # ================================================================ sobre
    def about(self):
        links = "<br>".join(f'<a href="{u}" style="color:{C["accent"]}">{n}</a>' for n, u in LINKS.items())
        box = QMessageBox(self)
        box.setWindowTitle(f"Sobre o {APP_NAME}")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<h2>{APP_NAME} {__version__}</h2>"
            f"<p>Software moderno e open source para o gravador <b>CH341A</b>.<br>"
            f"Memórias SPI 25xx e EEPROM I²C 24xx • Windows e Linux • drivers libusb e CH341DLL.</p>"
            f"<p><b>Criado por {AUTHOR}</b><br>{links}</p>")
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        for lbl in box.findChildren(QLabel):
            lbl.setOpenExternalLinks(True)
        box.exec()
