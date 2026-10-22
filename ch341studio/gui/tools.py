"""Diálogos das ferramentas: EDID, regiões Intel, transplante, comparação visual,
histórico e relatórios."""

import os
from datetime import datetime

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import edid as edidmod
from .. import fileio, intel
from .. import transplant as T
from ..i18n import tr
from .theme import C


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ReportDialog(QDialog):
    """Janela simples com um relatório em texto rico."""

    def __init__(self, parent, title: str, html: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 480)
        v = QVBoxLayout(self)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.appendHtml(html)
        v.addWidget(box)
        btn = QPushButton(tr("Fechar"))
        btn.clicked.connect(lambda: self.accept())
        v.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)


def health_html(rep, ident) -> str:
    colors = {"ok": C["ok"], "info": C["muted"], "warn": C["warn"], "bad": C["danger"]}
    icons = {"ok": "✔", "info": "•", "warn": "⚠", "bad": "✖"}
    out = [f"<b>{tr('Conteúdo')}:</b> {_html_escape(ident.summary())}<br>"]
    if ident.strings:
        out.append(f"<b>{tr('Textos encontrados')}:</b><br>" +
                   "<br>".join("&nbsp;&nbsp;" + _html_escape(s) for s in ident.strings) + "<br>")
    out.append(f"<br><b>{tr('Saúde do dump')}:</b><br>")
    for f in rep.findings:
        out.append(f'<span style="color:{colors[f.level]}">{icons[f.level]} '
                   f'{_html_escape(f.text)}</span><br>')
    return "".join(out)


# =================================================================== EDID
class EdidDialog(QDialog):
    applied = Signal()

    def __init__(self, parent, buffer: bytearray):
        super().__init__(parent)
        self.setWindowTitle(tr("Editor de EDID"))
        self.buffer = buffer
        self.e = edidmod.Edid.from_buffer(bytes(buffer))
        v = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        form = QFormLayout()
        self.f_mfr = QLineEdit(self.e.manufacturer)
        self.f_prod = QLineEdit(f"{self.e.product:04X}")
        self.f_serial = QLineEdit(str(self.e.serial))
        self.f_name = QLineEdit(self.e.name)
        self.f_sertxt = QLineEdit(self.e.serial_text)
        self.f_week = QLineEdit(str(self.e.week))
        self.f_year = QLineEdit(str(self.e.year))
        for label, w in ((tr("Fabricante (3 letras)"), self.f_mfr), (tr("Produto (hex)"), self.f_prod),
                         (tr("Nº de série (número)"), self.f_serial), (tr("Nome do monitor"), self.f_name),
                         (tr("Nº de série (texto)"), self.f_sertxt), (tr("Semana"), self.f_week),
                         (tr("Ano"), self.f_year)):
            form.addRow(label, w)
        v.addLayout(form)
        row = QHBoxLayout()
        b_apply = QPushButton(tr("Aplicar ao buffer (corrige o checksum)"))
        b_apply.setObjectName("primary")
        b_apply.clicked.connect(lambda: self.apply())
        b_fix = QPushButton(tr("Só corrigir checksum"))
        b_fix.clicked.connect(lambda: self.fix_only())
        b_close = QPushButton(tr("Fechar"))
        b_close.clicked.connect(lambda: self.reject())
        for b in (b_apply, b_fix, b_close):
            row.addWidget(b)
        v.addLayout(row)
        self._refresh()

    def _refresh(self):
        s = self.e.summary()
        self.info.setText("<br>".join(f"<b>{tr(k)}:</b> {_html_escape(str(val))}" for k, val in s.items()))

    def collect(self):
        self.e.manufacturer = self.f_mfr.text().strip()
        self.e.product = int(self.f_prod.text().strip() or "0", 16)
        self.e.serial = int(self.f_serial.text().strip() or "0")
        self.e.week = int(self.f_week.text().strip() or "0")
        self.e.year = int(self.f_year.text().strip() or "1990")
        if self.f_name.text().strip() != self.e.name:
            self.e.set_text(edidmod.TAG_NAME, self.f_name.text().strip())
        if self.f_sertxt.text().strip() != self.e.serial_text and self.f_sertxt.text().strip():
            self.e.set_text(edidmod.TAG_SERIAL, self.f_sertxt.text().strip())

    def apply(self):
        try:
            self.collect()
        except ValueError as ex:
            QMessageBox.warning(self, tr("Editor de EDID"), str(ex))
            return False
        self.e.fix_checksums()
        self.e.apply_to(self.buffer)
        self._refresh()
        self.applied.emit()
        return True

    def fix_only(self):
        self.e.fix_checksums()
        self.e.apply_to(self.buffer)
        self._refresh()
        self.applied.emit()


# =================================================================== regiões
class RegionsDialog(QDialog):
    """Lista as regiões do descritor Intel do buffer e oferece ações por região."""

    def __init__(self, parent, buffer: bytearray, on_write_region, on_buffer_changed):
        super().__init__(parent)
        self.setWindowTitle(tr("Regiões Intel"))
        self.resize(620, 360)
        self.buffer = buffer
        self.on_write_region = on_write_region
        self.on_buffer_changed = on_buffer_changed
        self.desc = intel.parse_descriptor(bytes(buffer))
        v = QVBoxLayout(self)
        if not self.desc:
            v.addWidget(QLabel(tr("Este buffer não tem descritor Intel (assinatura 0FF0A55A).")))
            self.table = None
            return
        self.table = QTableWidget(len(self.desc.regions), 4)
        self.table.setHorizontalHeaderLabels([tr("Região"), tr("Início"), tr("Fim"), tr("Tamanho")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.regions = sorted(self.desc.regions, key=lambda r: r.base)
        for i, r in enumerate(self.regions):
            for j, val in enumerate((r.name, f"0x{r.base:06X}", f"0x{r.limit:06X}", f"{r.size // 1024} KiB")):
                self.table.setItem(i, j, QTableWidgetItem(val))
        self.table.selectRow(self.regions.index(self.desc.region("BIOS")) if self.desc.region("BIOS") else 0)
        v.addWidget(self.table)
        hint = QLabel(tr("Dica: para atualizar a BIOS mantendo o ME e o MAC, substitua a região BIOS "
                         "pelo arquivo novo e grave só a região BIOS."))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        v.addWidget(hint)
        row = QHBoxLayout()
        for text, fn in ((tr("Extrair para arquivo"), self.extract),
                         (tr("Substituir pelo arquivo…"), self.replace),
                         (tr("Gravar só esta região no chip"), self.write_region)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        v.addLayout(row)

    def selected(self):
        if self.table is None:
            return None
        rows = self.table.selectionModel().selectedRows()
        idx = rows[0].row() if rows else 0
        return self.regions[idx]

    def extract(self, path=None):
        r = self.selected()
        if r is None:
            return
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, tr("Salvar região"), f"{r.name}.bin",
                                                  "Binário (*.bin);;Todos (*)")
        if path:
            fileio.save_file(path, bytes(self.buffer[r.base:r.limit + 1]))
            return path

    def replace(self, path=None):
        r = self.selected()
        if r is None:
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("Arquivo para a região {name}", name=r.name), "",
                                                  "Imagens (*.bin *.rom *.cap *.fd);;Todos (*)")
        if not path:
            return
        data = fileio.load_file(path)
        if len(data) != r.size:
            # arquivo de atualização pode vir com cabeçalho de cápsula
            stripped = intel.strip_capsule(data, r.size)
            if stripped:
                data = stripped[0]
        try:
            new = intel.replace_region(bytes(self.buffer), r, data)
        except ValueError as ex:
            QMessageBox.warning(self, tr("Regiões Intel"), str(ex))
            return
        self.buffer[:] = new
        self.on_buffer_changed(tr("Região {name} substituída por {file}.", name=r.name,
                                  file=os.path.basename(path)))
        return True

    def write_region(self):
        r = self.selected()
        if r is not None:
            self.accept()
            self.on_write_region(r)


# =================================================================== transplante
class TransplantDialog(QDialog):
    """Assistente: dados da placa (original) -> BIOS limpa (nova)."""

    def __init__(self, parent, current_buffer: bytes, on_result):
        super().__init__(parent)
        self.setWindowTitle(tr("Transplante de dados da placa"))
        self.resize(760, 560)
        self.on_result = on_result
        self.original = bytes(current_buffer) if current_buffer else b""
        self.original_name = tr("buffer atual") if current_buffer else ""
        self.target = b""
        self.target_name = ""
        self.items = []
        self.checks = {}
        v = QVBoxLayout(self)
        intro = QLabel(tr("1) Original = dump lido da placa (tem serial, MAC, chave). "
                          "2) BIOS nova = arquivo limpo do fabricante. O resultado é um arquivo NOVO; "
                          "nada é gravado no chip."))
        intro.setWordWrap(True)
        v.addWidget(intro)
        row = QHBoxLayout()
        self.lbl_o = QLabel()
        self.lbl_t = QLabel()
        b_o = QPushButton(tr("Escolher original…"))
        b_o.clicked.connect(lambda: self.pick("o"))
        b_t = QPushButton(tr("Escolher BIOS nova…"))
        b_t.clicked.connect(lambda: self.pick("t"))
        row.addWidget(b_o)
        row.addWidget(self.lbl_o, 1)
        row.addWidget(b_t)
        row.addWidget(self.lbl_t, 1)
        v.addLayout(row)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        scroll = QScrollArea()
        scroll.setWidget(self.list_host)
        scroll.setWidgetResizable(True)
        v.addWidget(scroll, 1)
        srow = QHBoxLayout()
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText(tr("Procurar texto nos dois (ex.: serial da etiqueta)"))
        b_find = QPushButton(tr("Procurar"))
        b_find.clicked.connect(lambda: self.find_text())
        srow.addWidget(self.ed_find, 1)
        srow.addWidget(b_find)
        v.addLayout(srow)
        self.lbl_find = QLabel()
        self.lbl_find.setWordWrap(True)
        self.lbl_find.setObjectName("muted")
        v.addWidget(self.lbl_find)
        brow = QHBoxLayout()
        self.b_go = QPushButton(tr("Gerar BIOS com os itens marcados…"))
        self.b_go.setObjectName("primary")
        self.b_go.clicked.connect(lambda: self.generate())
        b_close = QPushButton(tr("Fechar"))
        b_close.clicked.connect(lambda: self.reject())
        brow.addStretch(1)
        brow.addWidget(self.b_go)
        brow.addWidget(b_close)
        v.addLayout(brow)
        self._labels()
        self.analyze()

    def _labels(self):
        size = lambda d: f" ({len(d) // 1024} KiB)" if d else ""
        self.lbl_o.setText(tr("Original: {name}", name=self.original_name or "—") + size(self.original))
        self.lbl_t.setText(tr("BIOS nova: {name}", name=self.target_name or "—") + size(self.target))

    def pick(self, which, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("Escolher arquivo"), "",
                                                  "Imagens (*.bin *.rom *.cap *.fd);;Todos (*)")
        if not path:
            return
        data = fileio.load_file(path)
        if which == "t" and self.original and len(data) != len(self.original):
            stripped = intel.strip_capsule(data, len(self.original))
            if stripped:
                data = stripped[0]
                self.lbl_find.setText(stripped[1])
        if which == "o":
            self.original, self.original_name = data, os.path.basename(path)
        else:
            self.target, self.target_name = data, os.path.basename(path)
        self._labels()
        self.analyze()

    def analyze(self):
        while self.list_layout.count():
            w = self.list_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.checks = {}
        self.items = []
        if not (self.original and self.target):
            self.list_layout.addWidget(QLabel(tr("Escolha os dois arquivos para analisar.")))
            self.b_go.setEnabled(False)
            return
        self.items = T.analyze(self.original, self.target)
        if not self.items:
            self.list_layout.addWidget(QLabel(tr("Nenhum dado de placa reconhecido para transplantar.")))
        for it in self.items:
            ck = QCheckBox(f"{tr(it.title)}:  {it.original}  →  {it.target}"
                           + ("   ⚠ " + tr("arriscado") if it.risky else ""))
            ck.setChecked(it.available and it.default)
            ck.setEnabled(it.available)
            ck.setToolTip(tr(it.note))
            self.list_layout.addWidget(ck)
            if it.note:
                n = QLabel("     " + tr(it.note))
                n.setObjectName("muted")
                n.setWordWrap(True)
                self.list_layout.addWidget(n)
            self.checks[it.key] = ck
        cands = T.serial_candidates(self.original, self.target)
        if cands:
            lab = QLabel(tr("Textos que só existem no original (possíveis seriais): ") + ", ".join(cands))
            lab.setWordWrap(True)
            lab.setObjectName("muted")
            self.list_layout.addWidget(lab)
        self.list_layout.addStretch(1)
        self.b_go.setEnabled(any(it.available for it in self.items))

    def find_text(self):
        t = self.ed_find.text().strip()
        if not t:
            return
        fo = T.find_text(self.original, t) if self.original else []
        ft = T.find_text(self.target, t) if self.target else []
        fmt = lambda xs: ", ".join(f"0x{x:X}" for x in xs[:8]) or tr("não encontrado")
        self.lbl_find.setText(tr("Original: {a}  |  BIOS nova: {b}", a=fmt(fo), b=fmt(ft)))

    def chosen(self) -> set:
        return {k for k, ck in self.checks.items() if ck.isChecked()}

    def generate(self, path=None):
        chosen = self.chosen()
        if not chosen:
            QMessageBox.information(self, self.windowTitle(), tr("Marque pelo menos um item."))
            return
        risky = [it.title for it in self.items if it.key in chosen and it.risky]
        if risky and path is None:
            r = QMessageBox.question(
                self, self.windowTitle(),
                tr("Itens arriscados marcados: {items}.\nSe a versão da BIOS nova for diferente, a placa "
                   "pode não ligar. Continuar?", items=", ".join(tr(x) for x in risky)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        new, done = T.apply(self.original, self.target, self.items, chosen)
        if not path:
            base = os.path.splitext(self.target_name or "bios")[0]
            path, _ = QFileDialog.getSaveFileName(self, tr("Salvar BIOS gerada"),
                                                  f"{base}_transplante_{datetime.now():%Y%m%d}.bin",
                                                  "Binário (*.bin);;Todos (*)")
        if not path:
            return
        fileio.save_file(path, new)
        self.on_result(new, path, done)
        self.accept()
        return path


# =================================================================== comparação visual
class DiffMap(QWidget):
    clicked = Signal(int)

    def __init__(self, a: bytes, b: bytes, block: int = 4096):
        super().__init__()
        self.block = block
        n = max(len(a), len(b))
        self.blocks = []
        for off in range(0, n, block):
            x, y = a[off:off + block], b[off:off + block]
            if x == y:
                self.blocks.append(0.0)
            else:
                d = sum(1 for i in range(min(len(x), len(y))) if x[i] != y[i]) + abs(len(x) - len(y))
                self.blocks.append(max(0.02, d / block))
        self.cols = 64
        self.cell = 10
        rows = (len(self.blocks) + self.cols - 1) // self.cols
        self.setMinimumSize(self.cols * self.cell + 2, rows * self.cell + 2)

    @property
    def changed(self) -> int:
        return sum(1 for v in self.blocks if v)

    def paintEvent(self, e):
        p = QPainter(self)
        same = QColor(C["panel2"])
        for i, v in enumerate(self.blocks):
            x = (i % self.cols) * self.cell
            y = (i // self.cols) * self.cell
            if v:
                col = QColor(C["danger"])
                col.setAlphaF(0.35 + 0.65 * min(1.0, v * 4))
            else:
                col = same
            p.fillRect(QRect(x + 1, y + 1, self.cell - 1, self.cell - 1), col)

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        i = (pos.y() // self.cell) * self.cols + pos.x() // self.cell
        if 0 <= i < len(self.blocks):
            self.clicked.emit(i * self.block)


class CompareDialog(QDialog):
    def __init__(self, parent, a: bytes, b: bytes, name: str, on_goto):
        super().__init__(parent)
        self.setWindowTitle(tr("Comparação visual"))
        self.resize(720, 520)
        v = QVBoxLayout(self)
        self.map = DiffMap(a, b)
        lab = QLabel(tr("Buffer × {name}: {n} de {t} blocos de 4 KiB diferentes. "
                        "Vermelho mais forte = mais bytes diferentes. Clique num bloco para ir até ele.",
                        name=name, n=self.map.changed, t=len(self.map.blocks)))
        lab.setWordWrap(True)
        v.addWidget(lab)
        scroll = QScrollArea()
        scroll.setWidget(self.map)
        v.addWidget(scroll, 1)
        self.map.clicked.connect(on_goto)
        b = QPushButton(tr("Fechar"))
        b.clicked.connect(lambda: self.accept())
        v.addWidget(b, 0, Qt.AlignmentFlag.AlignRight)


# =================================================================== histórico
class HistoryDialog(QDialog):
    COLS = ["Data", "Operação", "Chip", "ID", "Tamanho", "CRC32", "Resultado", "Serviço", "Arquivo", "Nota"]

    def __init__(self, parent, history, on_open_file):
        super().__init__(parent)
        self.setWindowTitle(tr("Histórico de serviços"))
        self.resize(1000, 520)
        self.h = history
        self.on_open_file = on_open_file
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.ed = QLineEdit()
        self.ed.setPlaceholderText(tr("Buscar por chip, serviço, CRC, arquivo, nota…"))
        self.ed.textChanged.connect(lambda *_: self.reload())
        row.addWidget(self.ed, 1)
        v.addLayout(row)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in self.COLS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(lambda r, c: self.open_row(r))
        self.table.cellChanged.connect(lambda r, c: self.note_changed(r, c))
        v.addWidget(self.table, 1)
        hint = QLabel(tr("Duplo clique abre o arquivo. A coluna Nota pode ser editada."))
        hint.setObjectName("muted")
        v.addWidget(hint)
        self.rows = []
        self.reload()

    def reload(self):
        self.rows = self.h.search(self.ed.text().strip())
        self._loading = True
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            vals = [datetime.fromtimestamp(r["ts"]).strftime("%d/%m/%Y %H:%M"), tr(r["op"]), r["chip"],
                    r["chip_id"], str(r["size"] or ""), r["crc32"], r["result"], r["job"],
                    os.path.basename(r["file"] or ""), r["note"]]
            for j, val in enumerate(vals):
                it = QTableWidgetItem(val or "")
                if j != len(vals) - 1:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, j, it)
        self._loading = False

    def note_changed(self, r, c):
        if getattr(self, "_loading", False) or c != len(self.COLS) - 1 or r >= len(self.rows):
            return
        self.h.set_note(self.rows[r]["id"], self.table.item(r, c).text())

    def open_row(self, r):
        if r < len(self.rows):
            path = self.rows[r]["file"]
            if path and os.path.isfile(path):
                self.on_open_file(path)
                self.accept()
            else:
                QMessageBox.information(self, self.windowTitle(),
                                        tr("Este registro não tem arquivo salvo (ou ele foi movido)."))


# =================================================================== pesquisa de texto
class SearchDialog(QDialog):
    """Pesquisa texto (ou hex) no buffer e lista todas as ocorrências com o texto ao redor."""

    def __init__(self, parent, get_buffer, on_goto):
        super().__init__(parent)
        from .. import textsearch
        self.ts = textsearch
        self.setWindowTitle(tr("Pesquisar no buffer"))
        self.resize(820, 480)
        self.setModal(False)
        self.get_buffer = get_buffer
        self.on_goto = on_goto
        self.matches = []
        self.current = -1
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.ed = QLineEdit()
        self.ed.setPlaceholderText(tr("Texto a procurar (ex.: version)"))
        self.ed.returnPressed.connect(lambda: self.run())
        self.cb_mode = QComboBox()
        self.cb_mode.addItem(tr("Texto"), "text")
        self.cb_mode.addItem(tr("Hex (ex.: 24 49 42)"), "hex")
        b = QPushButton(tr("Pesquisar"))
        b.setObjectName("primary")
        b.clicked.connect(lambda: self.run())
        row.addWidget(self.ed, 1)
        row.addWidget(self.cb_mode)
        row.addWidget(b)
        v.addLayout(row)
        opts = QHBoxLayout()
        self.ck_case = QCheckBox(tr("Diferenciar maiúsculas"))
        self.ck_utf16 = QCheckBox(tr("Incluir texto UTF-16"))
        self.ck_utf16.setChecked(True)
        self.ck_utf16.setToolTip(tr("Muitas BIOS guardam textos em UTF-16 (um 00 depois de cada letra)."))
        opts.addWidget(self.ck_case)
        opts.addWidget(self.ck_utf16)
        opts.addStretch(1)
        v.addLayout(opts)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("Endereço"), tr("Tipo"), tr("Texto encontrado")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(lambda r, c: self.select(r))
        self.table.cellDoubleClicked.connect(lambda r, c: self.select(r, copy=False, close=True))
        try:
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.verticalHeader().setVisible(False)
        except Exception:
            pass
        v.addWidget(self.table, 1)
        brow = QHBoxLayout()
        self.lbl = QLabel(tr("Digite o texto e pressione Enter."))
        self.lbl.setObjectName("muted")
        b_prev = QPushButton(tr("◀ Anterior"))
        b_prev.clicked.connect(lambda: self.step(-1))
        b_next = QPushButton(tr("Próximo ▶"))
        b_next.clicked.connect(lambda: self.step(1))
        b_copy = QPushButton(tr("Copiar texto"))
        b_copy.clicked.connect(lambda: self.copy_current())
        brow.addWidget(self.lbl, 1)
        for w in (b_prev, b_next, b_copy):
            brow.addWidget(w)
        v.addLayout(brow)

    def run(self, query: str | None = None):
        if query is not None:
            self.ed.setText(query)
        q = self.ed.text()
        data = bytes(self.get_buffer() or b"")
        if not q.strip() or not data:
            self.lbl.setText(tr("Buffer vazio — leia um chip ou abra um arquivo.") if not data
                             else tr("Digite o texto e pressione Enter."))
            return []
        try:
            if self.cb_mode.currentData() == "hex":
                self.matches = self.ts.search_hex(data, q)
            else:
                self.matches = self.ts.search(data, q, case_sensitive=self.ck_case.isChecked(),
                                              utf16=self.ck_utf16.isChecked())
        except ValueError:
            QMessageBox.warning(self, self.windowTitle(), tr("Padrão hex inválido."))
            return []
        self.table.setRowCount(len(self.matches))
        for i, m in enumerate(self.matches):
            for j, val in enumerate((f"0x{m.offset:08X}", m.encoding, m.context)):
                self.table.setItem(i, j, QTableWidgetItem(val))
        n = len(self.matches)
        self.lbl.setText(tr("Nenhuma ocorrência.") if n == 0 else
                         tr("{n} ocorrência(s). Clique numa linha para ir até ela.", n=n)
                         + (" " + tr("(mostrando as primeiras 1000)") if n >= 1000 else ""))
        self.current = -1
        if n:
            self.select(0)
        return self.matches

    def select(self, row: int, copy: bool = False, close: bool = False):
        if not 0 <= row < len(self.matches):
            return
        self.current = row
        m = self.matches[row]
        try:
            self.table.selectRow(row)
        except Exception:
            pass
        self.on_goto(m.offset, m.length)
        self.lbl.setText(tr("{i} de {n}: {t}", i=row + 1, n=len(self.matches), t=m.context.strip()))
        if close:
            self.accept()

    def step(self, d: int):
        if self.matches:
            self.select((self.current + d) % len(self.matches))

    def copy_current(self):
        if 0 <= self.current < len(self.matches):
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.matches[self.current].context.strip())
