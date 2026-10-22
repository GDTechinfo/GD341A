"""Visualizador/editor hexadecimal rápido (desenha só as linhas visíveis)."""

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPainter
from PySide6.QtWidgets import QAbstractScrollArea

from ..i18n import tr
from .theme import C

BPR = 16  # bytes por linha


class HexView(QAbstractScrollArea):
    cursorMoved = Signal(int)
    edited = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self.setFont(font)
        self.viewport().setFont(font)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.data = bytearray()
        self.mask: bytearray | None = None
        self.sel: tuple[int, int] | None = None     # trecho destacado (início, fim exclusivo)
        self.cursor = 0
        self.nibble = 0
        self.read_only = False
        self._colors = {k: QColor(v) for k, v in C.items()}
        self._update_metrics()

    # ----------------------------------------------------------- dados
    def set_data(self, data: bytearray, keep_position: bool = False):
        self.data = data
        self.mask = None
        self.sel = None
        if not keep_position:
            self.cursor = 0
            self.verticalScrollBar().setValue(0)
        self.cursor = min(self.cursor, max(len(data) - 1, 0))
        self._update_scroll()
        self.viewport().update()
        self.cursorMoved.emit(self.cursor)

    def highlight(self, start: int, length: int):
        """Destaca um trecho (resultado de pesquisa) e vai até ele."""
        self.sel = (start, start + max(1, length)) if length else None
        self.goto(start)

    def set_mask(self, mask: bytearray | None):
        self.mask = mask
        self.viewport().update()

    # -------------------------------------------------------- métricas
    def _update_metrics(self):
        fm = self.fontMetrics()
        self.cw = fm.horizontalAdvance("0")
        self.lh = fm.height() + 3
        self.asc = fm.ascent()
        self.x_addr = 10
        self.x_hex = self.x_addr + 10 * self.cw
        self.x_ascii = self.x_hex + (BPR * 3 + 2) * self.cw
        self.total_w = self.x_ascii + (BPR + 2) * self.cw

    def _rows(self):
        return (len(self.data) + BPR - 1) // BPR

    def _visible_rows(self):
        return max(1, (self.viewport().height() - self.lh) // self.lh)

    def _update_scroll(self):
        vs = self.verticalScrollBar()
        vs.setRange(0, max(0, self._rows() - self._visible_rows()))
        vs.setPageStep(self._visible_rows())
        hs = self.horizontalScrollBar()
        hs.setRange(0, max(0, self.total_w - self.viewport().width()))
        hs.setPageStep(self.viewport().width())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_scroll()

    def _hex_x(self, col):
        return self.x_hex + (col * 3 + (1 if col >= 8 else 0)) * self.cw

    # ---------------------------------------------------------- pintura
    def paintEvent(self, e):
        p = QPainter(self.viewport())
        col = self._colors
        p.fillRect(self.viewport().rect(), col["panel"])
        dx = -self.horizontalScrollBar().value()
        p.translate(dx, 0)
        # cabeçalho
        p.fillRect(QRect(-dx, 0, self.viewport().width(), self.lh), col["panel2"])
        p.setPen(col["muted"])
        base_y = self.asc + 2
        p.drawText(self.x_addr, base_y, tr("Endereço"))
        for i in range(BPR):
            p.drawText(self._hex_x(i), base_y, f"{i:02X}")
        p.drawText(self.x_ascii, base_y, "ASCII")
        if not self.data:
            p.setPen(col["dim"])
            p.drawText(self.x_hex, self.lh * 2 + self.asc,
                       tr("Buffer vazio — leia um chip ou abra/arraste um arquivo (.bin / .hex)"))
            return
        first = self.verticalScrollBar().value()
        rows = min(self._visible_rows() + 1, self._rows() - first)
        data, mask, cur = self.data, self.mask, self.cursor
        for r in range(rows):
            row = first + r
            off = row * BPR
            y = self.lh * (r + 1)
            ty = y + self.asc + 1
            p.setPen(col["muted"])
            p.drawText(self.x_addr, ty, f"{off:08X}")
            chunk = data[off:off + BPR]
            for i, b in enumerate(chunk):
                a = off + i
                x = self._hex_x(i)
                ax = self.x_ascii + i * self.cw
                is_diff = mask is not None and a < len(mask) and mask[a]
                in_sel = self.sel is not None and self.sel[0] <= a < self.sel[1]
                if in_sel and a != cur:
                    p.fillRect(QRect(x - 2, y, self.cw * 2 + 4, self.lh), col["sel_bg"])
                    p.fillRect(QRect(ax, y, self.cw, self.lh), col["sel_bg"])
                if a == cur:
                    p.fillRect(QRect(x - 2, y, self.cw * 2 + 4, self.lh), col["accent2"])
                    p.fillRect(QRect(ax, y, self.cw, self.lh), col["accent2"])
                    if self.hasFocus():
                        p.fillRect(QRect(x + self.nibble * self.cw, y + self.lh - 3, self.cw, 2), col["text"])
                elif is_diff:
                    p.fillRect(QRect(x - 2, y, self.cw * 2 + 4, self.lh), col["diff_bg"])
                    p.fillRect(QRect(ax, y, self.cw, self.lh), col["diff_bg"])
                if is_diff and a != cur:
                    fg = col["diff_fg"]
                elif b == 0x00:
                    fg = col["dim"]
                elif b == 0xFF:
                    fg = col["muted"]
                else:
                    fg = col["text"]
                p.setPen(fg)
                p.drawText(x, ty, f"{b:02X}")
                p.drawText(ax, ty, chr(b) if 32 <= b < 127 else "·")
        p.setPen(col["border"])
        sep = self.x_ascii - self.cw
        p.drawLine(sep, 0, sep, self.viewport().height())

    # ---------------------------------------------------------- cursor
    def goto(self, offset: int):
        if not self.data:
            return
        self.cursor = max(0, min(offset, len(self.data) - 1))
        self.nibble = 0
        row = self.cursor // BPR
        vs = self.verticalScrollBar()
        if row < vs.value() or row >= vs.value() + self._visible_rows():
            vs.setValue(max(0, row - self._visible_rows() // 3))
        self.viewport().update()
        self.cursorMoved.emit(self.cursor)

    def _offset_at(self, pos):
        x = pos.x() + self.horizontalScrollBar().value()
        y = pos.y()
        if y < self.lh:
            return None
        row = self.verticalScrollBar().value() + (y - self.lh) // self.lh
        if self.x_hex <= x < self.x_ascii - self.cw:
            rel = x - self.x_hex
            col = rel // (3 * self.cw)
            if col >= 8:
                col = (rel - self.cw) // (3 * self.cw)
            col = max(0, min(BPR - 1, col))
        elif x >= self.x_ascii:
            col = min(BPR - 1, (x - self.x_ascii) // self.cw)
        else:
            return None
        off = row * BPR + col
        return off if off < len(self.data) else None

    def mousePressEvent(self, e):
        off = self._offset_at(e.position().toPoint())
        if off is not None:
            self.goto(off)

    def wheelEvent(self, e):
        steps = -e.angleDelta().y() // 40
        vs = self.verticalScrollBar()
        vs.setValue(vs.value() + steps)

    def keyPressEvent(self, e):
        if not self.data:
            return super().keyPressEvent(e)
        k = e.key()
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        page = self._visible_rows() * BPR
        moves = {
            Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -BPR, Qt.Key.Key_Down: BPR,
            Qt.Key.Key_PageUp: -page, Qt.Key.Key_PageDown: page,
        }
        if k in moves:
            self.goto(self.cursor + moves[k])
            return
        if k == Qt.Key.Key_Home:
            self.goto(0 if ctrl else self.cursor - self.cursor % BPR)
            return
        if k == Qt.Key.Key_End:
            self.goto(len(self.data) - 1 if ctrl else self.cursor - self.cursor % BPR + BPR - 1)
            return
        t = e.text().lower()
        if not self.read_only and t and t in "0123456789abcdef" and not ctrl:
            v = int(t, 16)
            b = self.data[self.cursor]
            if self.nibble == 0:
                b = (v << 4) | (b & 0x0F)
                self.nibble = 1
                self.data[self.cursor] = b
                self.viewport().update()
            else:
                b = (b & 0xF0) | v
                self.data[self.cursor] = b
                self.nibble = 0
                self.goto(self.cursor + 1)
            self.edited.emit()
            return
        super().keyPressEvent(e)
