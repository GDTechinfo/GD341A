"""Arquivos (.bin / Intel HEX), checksums e comparação de buffers."""

import hashlib
import os
import zlib


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checksum16(data: bytes) -> int:
    """Soma simples de 16 bits (a que muitos programas de gravador mostram)."""
    return sum(data) & 0xFFFF


# ----------------------------------------------------------------- Intel HEX
def parse_ihex(text: str, fill: int = 0xFF) -> bytes:
    mem: dict[int, int] = {}
    base = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"Linha {lineno}: não começa com ':'")
        raw = bytes.fromhex(line[1:])
        if sum(raw) & 0xFF:
            raise ValueError(f"Linha {lineno}: checksum inválido")
        n, addr, rtype = raw[0], int.from_bytes(raw[1:3], "big"), raw[3]
        data = raw[4:4 + n]
        if rtype == 0x00:
            for i, b in enumerate(data):
                mem[base + addr + i] = b
        elif rtype == 0x01:
            break
        elif rtype == 0x02:
            base = int.from_bytes(data, "big") << 4
        elif rtype == 0x04:
            base = int.from_bytes(data, "big") << 16
    if not mem:
        return b""
    size = max(mem) + 1
    out = bytearray([fill]) * size
    for a, b in mem.items():
        out[a] = b
    return bytes(out)


def to_ihex(data: bytes, line_len: int = 16) -> str:
    lines = []
    upper = -1
    for a in range(0, len(data), line_len):
        if a >> 16 != upper:
            upper = a >> 16
            rec = bytes([2, 0, 0, 4]) + upper.to_bytes(2, "big")
            lines.append(":" + (rec + bytes([(-sum(rec)) & 0xFF])).hex().upper())
        chunk = data[a:a + line_len]
        rec = bytes([len(chunk)]) + (a & 0xFFFF).to_bytes(2, "big") + b"\x00" + chunk
        lines.append(":" + (rec + bytes([(-sum(rec)) & 0xFF])).hex().upper())
    lines.append(":00000001FF")
    return "\n".join(lines) + "\n"


def load_file(path: str) -> bytes:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".hex", ".ihx", ".ihex"):
        with open(path, encoding="ascii", errors="replace") as f:
            return parse_ihex(f.read())
    with open(path, "rb") as f:
        return f.read()


def save_file(path: str, data: bytes):
    ext = os.path.splitext(path)[1].lower()
    tmp = path + ".tmp"
    if ext in (".hex", ".ihx", ".ihex"):
        with open(tmp, "w", encoding="ascii", newline="\n") as f:
            f.write(to_ihex(data))
    else:
        with open(tmp, "wb") as f:
            f.write(data)
    os.replace(tmp, path)


# ----------------------------------------------------------------- comparação
def diff_mask(a: bytes, b: bytes, block: int = 4096) -> tuple[bytearray, int]:
    """Máscara (1 = diferente) do tamanho do maior buffer + total de diferenças.

    Compara em blocos para só percorrer byte a byte onde há diferença.
    """
    size = max(len(a), len(b))
    mask = bytearray(size)
    count = 0
    for off in range(0, size, block):
        x = a[off:off + block]
        y = b[off:off + block]
        if x == y:
            continue
        for i in range(max(len(x), len(y))):
            if i >= len(x) or i >= len(y) or x[i] != y[i]:
                mask[off + i] = 1
                count += 1
    return mask, count


def diff_ranges(mask: bytearray, limit: int = 1000) -> list[tuple[int, int]]:
    """Converte a máscara em faixas (início, fim exclusivo)."""
    out = []
    i = mask.find(1)
    while i != -1 and len(out) < limit:
        j = mask.find(0, i)
        if j == -1:
            j = len(mask)
        out.append((i, j))
        i = mask.find(1, j)
    return out
