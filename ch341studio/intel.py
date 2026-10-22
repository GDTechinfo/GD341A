"""Descritor de flash Intel (regiões Descriptor/BIOS/ME/GbE/...) e cápsulas (.CAP)."""

from dataclasses import dataclass

FLVALSIG = 0x0FF0A55A
REGION_NAMES = ["Descriptor", "BIOS", "ME", "GbE", "PDR", "DevExp1", "BIOS2", "Reservado7",
                "EC", "DevExp2", "IE", "10GbE-0", "10GbE-1", "Reservado13", "Reservado14", "PTT"]

# GUIDs (em bytes, little-endian) de cabeçalhos de cápsula conhecidos
CAPSULE_GUIDS = {
    bytes.fromhex("BD86663B760D3040B70EB5519E2FC5A0"): "EFI Capsule",
    bytes.fromhex("8BA63C4A2377FB48803D578CC1FEC44D"): "AMI Aptio Capsule",
}


@dataclass
class Region:
    index: int
    name: str
    base: int
    limit: int          # inclusivo

    @property
    def size(self) -> int:
        return self.limit - self.base + 1

    def label(self) -> str:
        return f"{self.name}: 0x{self.base:06X}–0x{self.limit:06X} ({self.size // 1024} KiB)"


@dataclass
class Descriptor:
    offset: int
    regions: list

    def region(self, name: str) -> Region | None:
        return next((r for r in self.regions if r.name == name), None)


def parse_descriptor(data: bytes) -> Descriptor | None:
    for off in (0x10, 0x00):
        if len(data) >= off + 0x40 and int.from_bytes(data[off:off + 4], "little") == FLVALSIG:
            flmap0 = int.from_bytes(data[off + 4:off + 8], "little")
            frba = ((flmap0 >> 16) & 0xFF) << 4
            if not 0 < frba < 0x1000:
                return None
            regions = []
            for i in range(len(REGION_NAMES)):
                pos = frba + 4 * i
                if pos + 4 > len(data) or pos >= 0x1000:
                    break
                val = int.from_bytes(data[pos:pos + 4], "little")
                base = (val & 0x7FFF) << 12
                limit = (((val >> 16) & 0x7FFF) << 12) | 0xFFF
                if val in (0, 0xFFFFFFFF) and i > 0:
                    continue
                if base > limit or limit >= len(data):
                    continue
                regions.append(Region(i, REGION_NAMES[i], base, limit))
            if regions and regions[0].index == 0:
                return Descriptor(off, regions)
            return None
    return None


def replace_region(image: bytes, region: Region, content: bytes) -> bytes:
    if len(content) != region.size:
        raise ValueError(f"O arquivo tem {len(content)} bytes e a região {region.name} "
                         f"tem {region.size} bytes")
    out = bytearray(image)
    out[region.base:region.limit + 1] = content
    return bytes(out)


# ------------------------------------------------------------------ cápsulas
def capsule_info(data: bytes) -> tuple[str, int] | None:
    """(nome, tamanho_do_cabeçalho) se o arquivo começa com cabeçalho de cápsula UEFI."""
    if len(data) < 0x40:
        return None
    name = CAPSULE_GUIDS.get(bytes(data[:16]))
    hdr = int.from_bytes(data[16:20], "little")
    if name and 0x1C <= hdr <= 0x10000:
        return name, hdr
    return None


def _looks_like_flash(data: bytes) -> bool:
    return (parse_descriptor(data) is not None or data[:16].count(0xFF) == 16
            or data.find(b"_FVH", 0, 0x1000) >= 0)


def strip_capsule(data: bytes, chip_size: int) -> tuple[bytes, str] | None:
    """Tenta extrair a imagem do tamanho do chip de um .CAP (ASUS e similares).

    Retorna (imagem, explicação) ou None se não reconhecer.
    """
    if len(data) == chip_size:
        return None
    info = capsule_info(data)
    candidates = []
    if info and len(data) - info[1] == chip_size:
        candidates.append((info[1], f"cabeçalho {info[0]} de {info[1]} bytes"))
    extra = len(data) - chip_size
    if 0 < extra <= 0x10000:
        candidates.append((extra, f"{extra} bytes extras no início"))
    for off, why in candidates:
        img = data[off:off + chip_size]
        if len(img) == chip_size and _looks_like_flash(img):
            return bytes(img), f"Removido {why}."
    return None
