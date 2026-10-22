"""Leitura e edição de EDID (EEPROM 24C02 de monitores e TVs)."""

from dataclasses import dataclass

HEADER = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
DESC_OFFSETS = (54, 72, 90, 108)
TAG_SERIAL, TAG_TEXT, TAG_RANGE, TAG_NAME = 0xFF, 0xFE, 0xFD, 0xFC


def find_edid(data: bytes) -> int | None:
    """Offset do primeiro bloco EDID no buffer (ou None)."""
    i = data.find(HEADER)
    return i if i >= 0 and i + 128 <= len(data) else None


def checksum_ok(block: bytes) -> bool:
    return sum(block[:128]) & 0xFF == 0


def fix_checksum(block: bytearray) -> int:
    block[127] = (-sum(block[:127])) & 0xFF
    return block[127]


def decode_mfr(code: int) -> str:
    return "".join(chr(((code >> s) & 0x1F) + 64) for s in (10, 5, 0))


def encode_mfr(text: str) -> int:
    t = text.strip().upper()
    if len(t) != 3 or not all("A" <= ch <= "Z" for ch in t):
        raise ValueError("O código do fabricante deve ter 3 letras (A-Z)")
    return ((ord(t[0]) - 64) << 10) | ((ord(t[1]) - 64) << 5) | (ord(t[2]) - 64)


@dataclass
class Edid:
    raw: bytearray            # bloco base de 128 bytes (ou mais, com extensões)
    offset: int = 0

    @classmethod
    def from_buffer(cls, data: bytes) -> "Edid":
        off = find_edid(data)
        if off is None:
            raise ValueError("Nenhum EDID encontrado (cabeçalho 00 FF FF FF FF FF FF 00)")
        ext = data[off + 126] if off + 127 < len(data) else 0
        size = min(128 * (1 + ext), len(data) - off)
        return cls(bytearray(data[off:off + size]), off)

    # ------------------------------------------------------------- campos
    @property
    def manufacturer(self) -> str:
        return decode_mfr(int.from_bytes(self.raw[8:10], "big"))

    @manufacturer.setter
    def manufacturer(self, v: str):
        self.raw[8:10] = encode_mfr(v).to_bytes(2, "big")

    @property
    def product(self) -> int:
        return int.from_bytes(self.raw[10:12], "little")

    @product.setter
    def product(self, v: int):
        self.raw[10:12] = (v & 0xFFFF).to_bytes(2, "little")

    @property
    def serial(self) -> int:
        return int.from_bytes(self.raw[12:16], "little")

    @serial.setter
    def serial(self, v: int):
        self.raw[12:16] = (v & 0xFFFFFFFF).to_bytes(4, "little")

    @property
    def week(self) -> int:
        return self.raw[16]

    @week.setter
    def week(self, v: int):
        self.raw[16] = v & 0xFF

    @property
    def year(self) -> int:
        return self.raw[17] + 1990

    @year.setter
    def year(self, v: int):
        self.raw[17] = max(0, min(255, v - 1990))

    @property
    def version(self) -> str:
        return f"{self.raw[18]}.{self.raw[19]}"

    @property
    def size_cm(self) -> tuple[int, int]:
        return self.raw[21], self.raw[22]

    @property
    def digital(self) -> bool:
        return bool(self.raw[20] & 0x80)

    def _desc(self, tag):
        for off in DESC_OFFSETS:
            d = self.raw[off:off + 18]
            if d[0] == 0 and d[1] == 0 and d[3] == tag:
                return off
        return None

    def text(self, tag) -> str:
        off = self._desc(tag)
        if off is None:
            return ""
        return bytes(self.raw[off + 5:off + 18]).split(b"\n")[0].decode("ascii", "replace").rstrip()

    def set_text(self, tag, value: str):
        off = self._desc(tag)
        if off is None:
            # usa um descritor livre (tipo 0x10 = vazio) se houver
            for o in DESC_OFFSETS[1:]:
                d = self.raw[o:o + 18]
                if d[0] == 0 and d[1] == 0 and d[3] == 0x10:
                    off = o
                    break
        if off is None:
            raise ValueError("Não há descritor livre para este texto")
        txt = value.encode("ascii", "replace")[:13]
        if len(txt) < 13:
            txt += b"\n" + b" " * (12 - len(txt))
        self.raw[off:off + 5] = bytes([0, 0, 0, tag, 0])
        self.raw[off + 5:off + 18] = txt

    @property
    def name(self) -> str:
        return self.text(TAG_NAME)

    @property
    def serial_text(self) -> str:
        return self.text(TAG_SERIAL)

    def preferred_mode(self) -> str:
        d = self.raw[54:72]
        clock = int.from_bytes(d[0:2], "little")
        if clock == 0:
            return "—"
        h = d[2] | ((d[4] & 0xF0) << 4)
        v = d[5] | ((d[7] & 0xF0) << 4)
        hblank = d[3] | ((d[4] & 0x0F) << 8)
        vblank = d[6] | ((d[7] & 0x0F) << 8)
        hz = clock * 10000 / ((h + hblank) * (v + vblank)) if h and v else 0
        return f"{h}×{v} @ {hz:.0f} Hz"

    def checksum_ok(self) -> bool:
        return checksum_ok(self.raw[:128])

    def fix_checksums(self):
        for b in range(0, len(self.raw) // 128):
            blk = self.raw[b * 128:(b + 1) * 128]
            fix_checksum(blk)
            self.raw[b * 128:(b + 1) * 128] = blk

    def summary(self) -> dict:
        return {
            "Fabricante": self.manufacturer, "Produto": f"0x{self.product:04X}",
            "Nº de série": str(self.serial), "Nome": self.name or "—",
            "Série (texto)": self.serial_text or "—",
            "Fabricação": f"semana {self.week}, {self.year}", "Versão EDID": self.version,
            "Entrada": "digital" if self.digital else "analógica",
            "Tamanho": "{} × {} cm".format(*self.size_cm),
            "Modo preferido": self.preferred_mode(),
            "Extensões": str(len(self.raw) // 128 - 1),
            "Checksum": "OK" if self.checksum_ok() else "INVÁLIDO",
        }

    def apply_to(self, buffer: bytearray):
        buffer[self.offset:self.offset + len(self.raw)] = self.raw
