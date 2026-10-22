"""Detecção de problemas de contato e de dumps suspeitos.

Mau contato no clipe é a causa mais comum de BIOS estragada: a leitura "funciona"
mas vem com bits errados, metades repetidas ou tudo 0x00/0xFF. Aqui ficam:

* analyze_dump()  — heurísticas sobre um dump já lido
* compare_reads() — compara duas leituras do mesmo chip (leitura dupla)
* clip_test()     — lê o ID e um trecho várias vezes e mede a estabilidade
"""

import math
import zlib
from collections import Counter
from dataclasses import dataclass, field

OK, INFO, WARN, BAD = "ok", "info", "warn", "bad"


@dataclass
class Finding:
    level: str
    text: str


@dataclass
class HealthReport:
    findings: list = field(default_factory=list)

    def add(self, level, text):
        self.findings.append(Finding(level, text))

    @property
    def worst(self) -> str:
        order = [OK, INFO, WARN, BAD]
        return max((f.level for f in self.findings), key=order.index, default=OK)

    def lines(self) -> list[str]:
        return [f.text for f in self.findings]


def entropy(data: bytes) -> float:
    """Entropia de Shannon em bits por byte (0 = constante, 8 = aleatório)."""
    if not data:
        return 0.0
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in Counter(data).values())


def _sample(data: bytes, max_len: int = 1 << 20) -> bytes:
    if len(data) <= max_len:
        return data
    step = len(data) // max_len
    return data[::step]


def analyze_dump(data: bytes, chip_size: int | None = None) -> HealthReport:
    rep = HealthReport()
    n = len(data)
    if n == 0:
        rep.add(BAD, "Dump vazio.")
        return rep
    if chip_size and n != chip_size:
        rep.add(WARN, f"Tamanho do dump ({n} bytes) diferente do chip ({chip_size} bytes).")

    ff = data.count(0xFF)
    zz = data.count(0x00)
    if ff == n:
        rep.add(INFO, "Tudo 0xFF: memória apagada (ou sem contato nenhum — confira o ID).")
        return rep
    if zz == n:
        rep.add(BAD, "Tudo 0x00: quase sempre é falta de contato, chip invertido ou sem alimentação.")
        return rep

    # metades / quartos repetidos: clipe lendo endereço errado ou chip menor que o selecionado
    for parts in (2, 4):
        if n % parts == 0 and n >= 4096 * parts:
            size = n // parts
            first = data[:size]
            if first.count(0xFF) < size and all(data[i * size:(i + 1) * size] == first
                                                 for i in range(1, parts)):
                rep.add(BAD, f"O conteúdo se repete {parts} vezes (espelhado). O chip real "
                             f"provavelmente tem {size // 1024} KiB, ou o clipe está com mau contato "
                             "num pino de endereço/dados.")
                break

    # bits travados: um bit sempre 0 (ou sempre 1) em todo o dump com conteúdo variado
    s = _sample(data)
    if entropy(s) > 3:
        and_all = 0xFF
        or_all = 0x00
        for b in set(s):
            and_all &= b
            or_all |= b
        stuck1 = [i for i in range(8) if and_all >> i & 1]
        stuck0 = [i for i in range(8) if not or_all >> i & 1]
        if stuck1:
            rep.add(BAD, f"Bit(s) {stuck1} sempre em 1 em todo o dump: típico de pino MISO/dados "
                         "sem contato ou em curto.")
        if stuck0:
            rep.add(BAD, f"Bit(s) {stuck0} sempre em 0 em todo o dump: típico de mau contato.")

    # padrão periódico curto (ex.: ID repetido) — o chip não entrou em modo de leitura
    head = data[:4096]
    for period in (1, 2, 3, 4, 8, 16):
        pat = head[:period]
        if pat * (len(head) // period) == head[:len(head) // period * period] and pat.count(0xFF) < period:
            rep.add(BAD, f"Início do dump é um padrão repetido de {period} byte(s) "
                         f"({pat.hex(' ').upper()}): o chip não está respondendo à leitura.")
            break

    used = n - ff
    pct = f"{used * 100 / n:.1f}".replace(".", ",")
    rep.add(INFO, f"Uso: {pct}% do chip com dados (o resto é 0xFF).")
    e = entropy(s)
    if e < 1.0 and ff / n < 0.5:
        rep.add(WARN, f"Entropia muito baixa ({e:.2f} bits/byte) para um firmware: confira o contato.")
    if not any(f.level in (WARN, BAD) for f in rep.findings):
        rep.add(OK, "Nenhum sinal de problema de contato no conteúdo.")
    return rep


def compare_reads(a: bytes, b: bytes, block: int = 4096) -> tuple[int, list[int]]:
    """(bytes diferentes, endereços dos primeiros blocos com diferença)."""
    diff = 0
    blocks = []
    for off in range(0, max(len(a), len(b)), block):
        x, y = a[off:off + block], b[off:off + block]
        if x != y:
            d = sum(1 for i in range(min(len(x), len(y))) if x[i] != y[i]) + abs(len(x) - len(y))
            diff += d
            if len(blocks) < 32:
                blocks.append(off)
    return diff, blocks


@dataclass
class ClipResult:
    ids: Counter
    sample_crcs: Counter
    rounds: int

    @property
    def stable(self) -> bool:
        return len(self.ids) == 1 and len(self.sample_crcs) <= 1 and \
            next(iter(self.ids)) not in (0, 0xFFFFFF)

    def summary(self) -> str:
        ids = ", ".join(f"{k:06X}×{v}" for k, v in self.ids.most_common())
        if self.stable:
            return f"Contato estável: {self.rounds} leituras idênticas (ID {ids})."
        if all(k in (0, 0xFFFFFF) for k in self.ids):
            return f"Sem resposta do chip em {self.rounds} tentativas (ID {ids}). Reposicione o clipe."
        return (f"Contato INSTÁVEL: IDs lidos {ids}; {len(self.sample_crcs)} versões diferentes do "
                "mesmo trecho. Limpe os pinos e reposicione o clipe antes de gravar.")


def clip_test(flash, rounds: int = 20, sample_len: int = 4096, progress=None) -> ClipResult:
    """Lê o JEDEC ID e o início do chip várias vezes e mede a estabilidade."""
    ids = Counter()
    crcs = Counter()
    for i in range(rounds):
        ids[flash.read_jedec()] += 1
        if flash.chip is not None:
            crcs[zlib.crc32(flash.read(0, min(sample_len, flash.chip.size), stage=None))] += 1
        if progress:
            progress(i + 1, rounds, "Testando o clipe")
    return ClipResult(ids, crcs, rounds)
