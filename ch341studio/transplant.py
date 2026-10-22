"""Assistente de transplante: leva os dados da placa (MAC, chave do Windows, NVRAM, DMI)
do dump ORIGINAL para uma BIOS LIMPA (ex.: baixada do site do fabricante).

Segurança:
* nada é gravado no chip — o resultado é um buffer/arquivo novo;
* cada item é escolhido pelo usuário; os arriscados vêm desmarcados;
* só copia blocos que existem nos dois arquivos, na mesma posição e com o mesmo tamanho.
"""

import re
from dataclasses import dataclass, field

from . import intel

NVRAM_GUIDS = {
    bytes.fromhex("A3B9F5CE6D477F499FDCE98143E0422C"): "NVRAM AMI (NVAR)",
    bytes.fromhex("8D2BF1FF96768B4CA9852747075B4F50"): "NVRAM UEFI (VSS)",
}
MSDM_LEN = 0x55


@dataclass
class Item:
    key: str
    title: str
    original: str
    target: str
    note: str = ""
    available: bool = True
    default: bool = False
    risky: bool = False
    spans: list = field(default_factory=list)   # [(offset, length)] copiados do original


def _mac(b: bytes) -> str:
    return ":".join(f"{x:02X}" for x in b[:6])


def find_msdm(data: bytes) -> tuple[int, str] | None:
    i = data.find(b"MSDM")
    while i >= 0:
        ln = int.from_bytes(data[i + 4:i + 8], "little")
        if ln == MSDM_LEN and i + ln <= len(data):
            key = data[i + 0x38:i + 0x38 + 29]
            if re.fullmatch(rb"[A-Z0-9]{5}(-[A-Z0-9]{5}){4}", key):
                return i, key.decode()
            return i, ""
        i = data.find(b"MSDM", i + 1)
    return None


def find_volumes(data: bytes) -> list[tuple[int, int, str]]:
    """Volumes NVRAM: (início, tamanho, tipo), pelo GUID do sistema de arquivos do FV."""
    out = []
    i = data.find(b"_FVH")
    while i >= 0:
        start = i - 40
        if start >= 0:
            guid = bytes(data[start + 16:start + 32])
            length = int.from_bytes(data[start + 32:start + 40], "little")
            if guid in NVRAM_GUIDS and 0 < length <= len(data) - start:
                out.append((start, length, NVRAM_GUIDS[guid]))
        i = data.find(b"_FVH", i + 4)
    return out


def find_dmi(data: bytes) -> list[int]:
    out, i = [], data.find(b"$DMI")
    while i >= 0 and len(out) < 8:
        out.append(i)
        i = data.find(b"$DMI", i + 4)
    return out


def _dmi_len(data: bytes, off: int, max_len: int = 0x1000) -> int:
    """Até o primeiro trecho de 32 bytes 0xFF (fim da área DMI)."""
    end = data.find(b"\xFF" * 32, off + 4, off + max_len)
    return (end if end > 0 else off + max_len) - off


def serial_candidates(original: bytes, target: bytes, limit: int = 20) -> list[str]:
    """Textos com cara de número de série que só existem no original."""
    found = []
    for m in re.finditer(rb"(?<![A-Za-z0-9])[A-Z0-9]{8,22}(?![A-Za-z0-9])", original):
        s = m.group(0)
        if (sum(ch.isdigit() for ch in s.decode()) >= 3 and sum(ch.isalpha() for ch in s.decode()) >= 1
                and target.find(s) < 0):
            t = s.decode()
            if t not in found:
                found.append(t)
                if len(found) >= limit:
                    break
    return found


def find_text(data: bytes, text: str) -> list[int]:
    """Procura um texto em ASCII e em UTF-16LE (como a BIOS costuma guardar)."""
    hits = []
    for enc in (text.encode("ascii", "ignore"), text.encode("utf-16-le")):
        if not enc:
            continue
        i = data.find(enc)
        while i >= 0 and len(hits) < 32:
            hits.append(i)
            i = data.find(enc, i + 1)
    return sorted(set(hits))


def analyze(original: bytes, target: bytes) -> list[Item]:
    items: list[Item] = []
    if len(original) != len(target):
        items.append(Item("size", "Tamanhos diferentes", f"{len(original)} bytes", f"{len(target)} bytes",
                          "Os arquivos precisam ter o mesmo tamanho. Se a BIOS nova é um .CAP, remova "
                          "o cabeçalho primeiro (Ferramentas → Remover cabeçalho .CAP).", available=False))
        return items

    # 1) GbE (MAC da rede) — Intel
    d_o, d_t = intel.parse_descriptor(original), intel.parse_descriptor(target)
    if d_o and d_t:
        g_o, g_t = d_o.region("GbE"), d_t.region("GbE")
        if g_o and g_t and (g_o.base, g_o.size) == (g_t.base, g_t.size):
            items.append(Item("gbe", "Região GbE (endereço MAC da rede)",
                              _mac(original[g_o.base:]), _mac(target[g_t.base:]),
                              "Copia a região GbE inteira do original.", default=True,
                              spans=[(g_o.base, g_o.size)]))
        elif g_o or g_t:
            items.append(Item("gbe", "Região GbE (endereço MAC)", "presente" if g_o else "—",
                              "presente" if g_t else "—", "As regiões GbE não coincidem.",
                              available=False))

    # 2) Chave do Windows (tabela ACPI MSDM)
    m_o, m_t = find_msdm(original), find_msdm(target)
    if m_o:
        if m_t and m_t[1] != m_o[1]:
            same_pos = m_t[0] == m_o[0]
            items.append(Item("msdm", "Chave do Windows (MSDM)", m_o[1] or "(formato desconhecido)",
                              m_t[1] or "vazia/placeholder",
                              "Copia a tabela MSDM inteira (85 bytes)." + (
                                  "" if same_pos else " A tabela está em outra posição na BIOS nova; "
                                  "será copiada para lá."),
                              default=True,
                              spans=[(m_o[0], MSDM_LEN)] if same_pos else [("msdm", m_o[0], m_t[0])]))
        elif m_t and m_t[1] == m_o[1]:
            items.append(Item("msdm", "Chave do Windows (MSDM)", m_o[1], m_t[1], "Já é igual.",
                              available=False))
        else:
            items.append(Item("msdm", "Chave do Windows (MSDM)", m_o[1], "sem tabela MSDM",
                              "A BIOS nova não tem espaço para a chave; anote-a para ativar o Windows "
                              "manualmente.", available=False))

    # 3) Volumes NVRAM (configurações + dados gravados pelo fabricante)
    v_o, v_t = find_volumes(original), find_volumes(target)
    for (so, lo, kind) in v_o:
        match = next(((st, lt, kt) for (st, lt, kt) in v_t if (st, lt, kt) == (so, lo, kind)), None)
        if match:
            same = original[so:so + lo] == target[so:so + lo]
            items.append(Item(f"nvram@{so:X}", f"{kind} em 0x{so:06X}", f"{lo // 1024} KiB",
                              "igual" if same else f"{lo // 1024} KiB (diferente)",
                              "Pode conter serial, UUID e configurações. Com versões de BIOS diferentes, "
                              "copiar a NVRAM pode impedir o boot.",
                              available=not same, risky=True, spans=[(so, lo)]))

    # 4) Área DMI antiga da AMI ($DMI)
    for off in find_dmi(original):
        if target[off:off + 4] == b"$DMI":
            ln = _dmi_len(original, off)
            same = original[off:off + ln] == target[off:off + ln]
            items.append(Item(f"dmi@{off:X}", f"Área DMI ($DMI) em 0x{off:06X}", f"{ln} bytes",
                              "igual" if same else "diferente",
                              "Serial, modelo e UUID nas BIOS AMI mais antigas.",
                              available=not same, default=not same, risky=True,
                              spans=[(off, ln)]))
    return items


def apply(original: bytes, target: bytes, items: list[Item], chosen: set[str]) -> tuple[bytes, list[str]]:
    out = bytearray(target)
    done = []
    for it in items:
        if it.key not in chosen or not it.available:
            continue
        for sp in it.spans:
            if len(sp) == 3:                       # ("msdm", origem, destino)
                _, src, dst = sp
                out[dst:dst + MSDM_LEN] = original[src:src + MSDM_LEN]
            else:
                off, ln = sp
                out[off:off + ln] = original[off:off + ln]
        done.append(it.title)
    return bytes(out), done
