"""Pesquisa de texto dentro do dump (ex.: "Version" em BIOS de placa de vídeo).

Procura em ASCII e em UTF-16LE (muitas BIOS guardam textos assim), sem diferenciar
maiúsculas por padrão, e devolve cada ocorrência com o texto legível ao redor —
assim a versão que vem depois de "Version" já aparece na lista.
"""

import re
from dataclasses import dataclass

PRINTABLE = re.compile(rb"[\x20-\x7e]")


@dataclass
class Match:
    offset: int
    length: int          # em bytes
    encoding: str        # "ASCII" ou "UTF-16"
    before: str
    text: str            # o trecho encontrado, como está no arquivo
    after: str

    @property
    def context(self) -> str:
        return f"{self.before}{self.text}{self.after}"


def _ascii_context(data: bytes, start: int, end: int, before: int, after: int):
    b = start
    while b > 0 and start - b < before and PRINTABLE.match(data[b - 1:b]):
        b -= 1
    e = end
    while e < len(data) and e - end < after and PRINTABLE.match(data[e:e + 1]):
        e += 1
    dec = lambda x: x.decode("latin-1")
    return dec(data[b:start]), dec(data[start:end]), dec(data[end:e])


def _utf16_context(data: bytes, start: int, end: int, before: int, after: int):
    def ok(i):
        return 0 <= i < len(data) - 1 and 0x20 <= data[i] <= 0x7E and data[i + 1] == 0
    b = start
    while ok(b - 2) and (start - b) // 2 < before:
        b -= 2
    e = end
    while ok(e) and (e - end) // 2 < after:
        e += 2
    dec = lambda x: x.decode("utf-16-le", "replace")
    return dec(data[b:start]), dec(data[start:end]), dec(data[end:e])


def search(data: bytes, query: str, case_sensitive: bool = False, utf16: bool = True,
           max_results: int = 1000, before: int = 24, after: int = 64) -> list[Match]:
    query = query.strip("\r\n")
    if not query:
        return []
    try:
        raw = query.encode("latin-1")
    except UnicodeEncodeError:
        raw = query.encode("utf-8")
    flags = 0 if case_sensitive else re.IGNORECASE
    found: list[Match] = []
    pat = re.compile(re.escape(raw), flags)
    for m in pat.finditer(data):
        b, t, a = _ascii_context(data, m.start(), m.end(), before, after)
        found.append(Match(m.start(), m.end() - m.start(), "ASCII", b, t, a))
        if len(found) >= max_results:
            break
    if utf16 and len(found) < max_results:
        # cada caractere seguido de 00 (sem diferenciar maiúsculas, se pedido)
        parts = []
        for ch in raw:
            c = bytes([ch])
            if not case_sensitive and c.isalpha():
                parts.append(b"[" + re.escape(c.lower()) + re.escape(c.upper()) + b"]\x00")
            else:
                parts.append(re.escape(c) + b"\x00")
        pat16 = re.compile(b"".join(parts))
        for m in pat16.finditer(data):
            b, t, a = _utf16_context(data, m.start(), m.end(), before, after)
            found.append(Match(m.start(), m.end() - m.start(), "UTF-16", b, t, a))
            if len(found) >= max_results:
                break
    found.sort(key=lambda x: x.offset)
    return found


def parse_hex(text: str) -> bytes:
    return bytes.fromhex(text.replace("0x", "").replace(",", " ").replace("-", " "))


def search_hex(data: bytes, text: str, max_results: int = 1000) -> list[Match]:
    pat = parse_hex(text)
    if not pat:
        return []
    out = []
    i = data.find(pat)
    while i >= 0 and len(out) < max_results:
        b, t, a = _ascii_context(data, i, i + len(pat), 16, 32)
        out.append(Match(i, len(pat), "HEX", b, pat.hex(" ").upper(), a))
        i = data.find(pat, i + 1)
    return out
