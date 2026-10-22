"""Banco de dados de memórias (SPI 25xx e I2C 24xx)."""

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
CHIP_DIR = os.path.join(HERE, "chips")

VENDORS = {
    0xEF: "Winbond", 0xC2: "Macronix", 0xC8: "GigaDevice", 0x1C: "EON", 0x20: "Micron/ST/XMC",
    0x01: "Spansion/Cypress", 0xBF: "SST/Microchip", 0x1F: "Atmel/Adesto", 0x68: "Boya",
    0x5E: "Zbit", 0xA1: "Fudan", 0x85: "Puya", 0x9D: "ISSI", 0x8C: "ESMT", 0x0B: "XTX",
    0xC4: "GigaDevice (old)", 0x62: "ON Semi", 0x37: "AMIC",
}


def portable_dir() -> str | None:
    """Modo portátil: com um arquivo portable.txt ao lado do programa, tudo fica em ./dados."""
    import sys
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(HERE))
    if os.path.isfile(os.path.join(base, "portable.txt")):
        return os.path.join(base, "dados")
    return None


def config_dir() -> str:
    p = portable_dir()
    if p:
        return p
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "CH341Studio")
    return os.path.join(os.path.expanduser("~"), ".config", "ch341studio")


@dataclass
class SpiChip:
    name: str
    vendor: str
    jedec: int
    size: int                     # bytes
    voltage: str = "3.3"
    flags: list = field(default_factory=list)
    page: int = 256
    sector: int = 4096
    block: int = 65536
    generic: bool = False
    origin: str = "embutido"          # embutido | usuário | auto (fontes)

    @property
    def addr4(self) -> bool:
        return self.size > 16 * 1024 * 1024

    @property
    def is_18v(self) -> bool:
        return self.voltage == "1.8"

    def has(self, flag: str) -> bool:
        return flag in self.flags

    @property
    def label(self) -> str:
        return f"{self.name} ({self.vendor}, {human_size(self.size)})"

    @property
    def aliases(self) -> list[str]:
        """Expande nomes agrupados: 'MX25L3205/3206/3233' -> MX25L3205, MX25L3206, MX25L3233."""
        parts = [p.strip() for p in self.name.split("/")]
        first = parts[0]
        out = [first]
        for p in parts[1:]:
            if p and (p[0].isdigit() or len(p) <= 2) and len(p) < len(first):
                out.append(first[:len(first) - len(p)] + p)
            elif p:
                out.append(p)
        return out


@dataclass
class EepromChip:
    name: str
    size: int
    page: int
    addr_bytes: int

    @property
    def label(self) -> str:
        return f"{self.name} ({human_size(self.size)})"


def human_size(n: int) -> str:
    if n >= 1024 * 1024 and n % (1024 * 1024) == 0:
        return f"{n // (1024 * 1024)} MiB ({n * 8 // (1024 * 1024)} Mbit)"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024} KiB"
    return f"{n} B"


ORIGIN_RANK = {"usuário": 0, "embutido": 1}


def _load(name):
    """Lista de (origem, chip) do banco embutido e do arquivo do usuário."""
    items = []
    with open(os.path.join(CHIP_DIR, name), encoding="utf-8") as f:
        items += [("embutido", c) for c in json.load(f).get("chips", [])]
    user = os.path.join(config_dir(), name.replace(".json", "_user.json"))
    if os.path.isfile(user):
        try:
            with open(user, encoding="utf-8") as f:
                items = [("usuário", c) for c in json.load(f).get("chips", [])] + items
        except (OSError, ValueError):
            pass
    return items


def _load_auto() -> dict:
    """Banco gerado pelo atualizador automático (chipupdate)."""
    try:
        with open(os.path.join(config_dir(), "chips_auto.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _norm(name: str) -> str:
    return "".join(ch for ch in name.upper() if ch.isalnum())


@lru_cache(maxsize=1)
def spi_chips() -> list[SpiChip]:
    out, seen = [], set()

    def add(c, origin):
        try:
            chip = SpiChip(
                name=c["name"], vendor=c.get("vendor", "?"), jedec=int(c["id"], 16),
                size=int(c["size"]) * 1024, voltage=c.get("volt", "3.3"),
                flags=list(c.get("flags", [])), page=int(c.get("page", 256)),
                sector=int(c.get("sector", 4096)), block=int(c.get("block", 65536)),
                origin=origin)
        except (KeyError, ValueError, TypeError):
            return
        keys = {(_norm(a), chip.jedec) for a in chip.aliases}
        if keys & seen:            # já existe com prioridade maior
            return
        seen.update(keys)
        out.append(chip)

    for origin, c in _load("spi_flash.json"):
        add(c, origin)
    for c in _load_auto().get("spi", []):
        add(c, "auto: " + ", ".join(c.get("sources", [])))
    out.sort(key=lambda c: (c.vendor.upper(), c.size, c.name))
    return out


@lru_cache(maxsize=1)
def eeprom_chips() -> list[EepromChip]:
    out, seen = [], set()
    entries = [c for _, c in _load("i2c_eeprom.json")] + _load_auto().get("i2c", [])
    for c in entries:
        try:
            chip = EepromChip(c["name"], int(c["size"]), int(c["page"]), int(c["addr"]))
        except (KeyError, ValueError, TypeError):
            continue
        if _norm(chip.name) in seen:
            continue
        seen.add(_norm(chip.name))
        out.append(chip)
    out.sort(key=lambda c: (c.size, c.name))
    return out


def reload():
    """Recarrega o banco (depois de uma atualização)."""
    spi_chips.cache_clear()
    eeprom_chips.cache_clear()


def find_spi_by_id(jedec: int) -> list[SpiChip]:
    found = [c for c in spi_chips() if c.jedec == jedec]

    def rank(c):
        src = 0 if "flashrom" in c.origin else 1 if "imsprog" in c.origin else 2
        return ORIGIN_RANK.get(c.origin, 2), src
    return sorted(found, key=rank)


def find_spi_by_name(name: str) -> SpiChip | None:
    key = name.strip().upper()
    for c in spi_chips():
        if c.name.upper() == key or key in [a.upper() for a in c.aliases]:
            return c
    for c in spi_chips():
        if any(a.upper().startswith(key) for a in c.aliases):
            return c
    return None


def find_eeprom(name: str) -> EepromChip | None:
    key = name.strip().upper().replace("AT", "").replace("M24", "24")
    for c in eeprom_chips():
        if c.name.upper().startswith(key) or key in c.name.upper().split(" / "):
            return c
    return None


def generic_from_id(jedec: int) -> SpiChip | None:
    """Monta um chip genérico pelo byte de capacidade (2^n bytes)."""
    cap = jedec & 0xFF
    mfr = jedec >> 16
    if jedec in (0, 0xFFFFFF) or mfr in (0x00, 0xFF):
        return None
    if 0x10 <= cap <= 0x1A:
        size = 1 << cap
    elif cap in (0x20, 0x21, 0x22):     # convenção Winbond/Micron acima de 256 Mbit
        size = 64 * 1024 * 1024 << (cap - 0x20)
    else:
        return None
    return SpiChip(name=f"Genérico {jedec:06X}", vendor=VENDORS.get(mfr, f"0x{mfr:02X}"),
                   jedec=jedec, size=size, generic=True)


def vendor_name(mfr: int) -> str:
    return VENDORS.get(mfr, f"desconhecido (0x{mfr:02X})")
