"""Identifica o conteúdo de um dump (BIOS, EC, roteador, TV, EDID...) por assinaturas."""

import re
from dataclasses import dataclass, field

from . import edid as edidmod


@dataclass
class Identity:
    kind: str = "Desconhecido"
    details: list = field(default_factory=list)
    strings: list = field(default_factory=list)

    def summary(self) -> str:
        return self.kind + (" — " + "; ".join(self.details) if self.details else "")


def _find_all(data: bytes, sig: bytes, limit: int = 8) -> list[int]:
    out, i = [], data.find(sig)
    while i >= 0 and len(out) < limit:
        out.append(i)
        i = data.find(sig, i + 1)
    return out


def _ascii_after(data: bytes, off: int, maxlen: int = 64) -> str:
    chunk = data[off:off + maxlen]
    m = re.match(rb"[\x20-\x7e]+", chunk)
    return m.group(0).decode("ascii").strip() if m else ""


def interesting_strings(data: bytes, limit: int = 12) -> list[str]:
    """Strings que costumam dizer modelo/versão (procura nos primeiros e últimos MB)."""
    pats = [rb"(?:BIOS|Bios) (?:Version|version|Ver)[ :]*[\x20-\x7e]{2,40}",
            rb"U-Boot \d{4}\.\d{2}[\x20-\x7e]{0,40}",
            rb"Linux version [\x20-\x7e]{5,60}",
            rb"[A-Z][A-Za-z0-9\-]{2,20} (?:Board|BOARD|Motherboard)[\x20-\x7e]{0,30}",
            rb"(?:Copyright|\(C\)) ?(?:19|20)\d\d[\x20-\x7e]{3,50}",
            rb"[A-Z0-9]{3,8}\.\d{3,4}(?:\.[A-Z0-9]{2,8})?"]
    seen, out = set(), []
    areas = [data[:4 << 20], data[-(4 << 20):]] if len(data) > (8 << 20) else [data]
    for area in areas:
        for p in pats:
            for m in re.finditer(p, area):
                s = m.group(0).decode("ascii", "replace").strip()
                if s not in seen and len(s) >= 6:
                    seen.add(s)
                    out.append(s)
                    if len(out) >= limit:
                        return out
    return out


def identify(data: bytes) -> Identity:
    idt = Identity()
    if not data or data.count(0xFF) == len(data):
        idt.kind = "Vazio (tudo 0xFF)"
        return idt

    if len(data) <= 4096 and edidmod.find_edid(data) is not None:
        try:
            e = edidmod.Edid.from_buffer(data)
            idt.kind = "EDID de monitor/TV"
            idt.details = [f"{e.manufacturer} {e.name or ''}".strip(), e.preferred_mode(),
                           "checksum OK" if e.checksum_ok() else "checksum inválido"]
            return idt
        except ValueError:
            pass

    from . import intel
    desc = intel.parse_descriptor(data)
    if desc:
        idt.kind = "Imagem Intel completa (com descritor)"
        idt.details.append(", ".join(f"{r.name} {r.size // 1024} KiB" for r in desc.regions))

    if data.find(b"$PSP") >= 0 or any(data[o:o + 4] == b"\xAA\x55\xAA\x55"
                                      for o in (0x20000, 0x820000, 0xC20000, 0xE20000, 0xF20000)
                                      if o + 4 <= len(data)):
        if idt.kind == "Desconhecido":
            idt.kind = "BIOS AMD (com PSP)"
        else:
            idt.details.append("PSP AMD")

    vendor = None
    for sig, name in ((b"$IBIOSI$", "AMI Aptio"), (b"AMIBIOS", "AMI"), (b"InsydeH2O", "Insyde H2O"),
                      (b"$INSYDE", "Insyde"), (b"Phoenix SecureCore", "Phoenix SecureCore"),
                      (b"PhoenixBIOS", "Phoenix"), (b"Award BIOS", "Award"), (b"coreboot", "coreboot")):
        if data.find(sig) >= 0:
            vendor = name
            break
    fvh = len(_find_all(data, b"_FVH", 64))
    if vendor or fvh:
        if idt.kind == "Desconhecido":
            idt.kind = "BIOS / UEFI"
        idt.details.append(f"{vendor or 'UEFI'}" + (f", {fvh} volume(s) de firmware" if fvh else ""))
        i = data.find(b"$IBIOSI$")
        if i >= 0:
            ver = _ascii_after(data, i + 8, 40)
            if ver:
                idt.details.append(f"ID da BIOS: {ver}")
    if data.find(b"MSDM") >= 0:
        idt.details.append("contém tabela MSDM (chave do Windows)")

    if idt.kind == "Desconhecido":
        for sig, name in ((b"U-Boot", "Roteador/embarcado (U-Boot)"), (b"\x27\x05\x19\x56", "Imagem uImage (Linux)"),
                          (b"hsqs", "Sistema de arquivos SquashFS (roteador/TV)"),
                          (b"MBOOT", "TV (bootloader MStar MBOOT)"), (b"MSTAR", "TV/monitor MStar"),
                          (b"Realtek", "Realtek (TV/monitor/roteador)"), (b"ITE Tech", "EC ITE"),
                          (b"ENE KB", "EC ENE")):
            if data.find(sig) >= 0:
                idt.kind = name
                break
    idt.strings = interesting_strings(data)
    used = len(data) - data.count(0xFF)
    idt.details.append(f"{used * 100 / len(data):.0f}% ocupado".replace(".", ","))
    return idt
