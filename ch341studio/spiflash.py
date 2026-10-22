"""Operações em memórias SPI NOR (25xx): detectar, ler, apagar, gravar, verificar."""

import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import chipdb
from .errors import Cancelled, ChipError, ProtectedError

# fabricantes cujo bit 6 do SR1 é o QE (Quad Enable) — precisa ser preservado
QE_IN_SR1 = {0xC2, 0x9D}

OPS = {   # (opcode 3 bytes, opcode 4 bytes)
    "read": (0x03, 0x13),
    "pp": (0x02, 0x12),
    "se": (0x20, 0x21),
    "be": (0xD8, 0xDC),
}


@dataclass
class VerifyResult:
    ok: bool
    mismatches: int = 0
    first: list = field(default_factory=list)   # primeiros endereços divergentes

    def summary(self) -> str:
        if self.ok:
            return "Verificação OK — conteúdo idêntico."
        addrs = ", ".join(f"0x{a:06X}" for a in self.first[:8])
        return f"Verificação FALHOU: {self.mismatches} byte(s) diferentes. Primeiros: {addrs}"


@dataclass
class WriteStats:
    erased_sectors: int = 0
    erased_blocks: int = 0
    programmed_pages: int = 0
    skipped_bytes: int = 0


class SpiFlash:
    def __init__(self, backend, chip: chipdb.SpiChip | None = None,
                 progress=None, cancel=None, log=None):
        self.b = backend
        self.chip = chip
        self.progress_cb = progress
        self.cancel_cb = cancel
        self.log_cb = log
        self._addr_depth = 0
        self._last_progress = 0.0
        # gravação turbo: páginas em fila (só libusb), conferidas e repetidas se falharem
        self.turbo = False
        self.turbo_delay_us = 1000
        self.turbo_batch = 16
        self.turbo_retries = 0

    # ------------------------------------------------------------ utilidades
    def log(self, msg: str):
        if self.log_cb:
            self.log_cb(msg)

    def _check_cancel(self):
        if self.cancel_cb and self.cancel_cb():
            raise Cancelled("Operação cancelada")

    def _progress(self, done, total, stage, force=False):
        self._check_cancel()
        if not self.progress_cb:
            return
        now = time.monotonic()
        if force or done >= total or now - self._last_progress > 0.05:
            self._last_progress = now
            self.progress_cb(done, total, stage)

    def _need_chip(self) -> chipdb.SpiChip:
        if self.chip is None:
            raise ChipError("Nenhuma memória selecionada. Use 'Detectar' ou escolha o modelo.")
        return self.chip

    def cmd(self, write: bytes, read_len: int = 0) -> bytes:
        return self.b.spi_xfer(bytes(write), read_len)

    # ------------------------------------------------------------ status/ID
    def read_jedec(self) -> int:
        r = self.cmd(b"\x9F", 3)
        return int.from_bytes(r, "big")

    def read_rems(self) -> tuple[int, int]:
        r = self.cmd(b"\x90\x00\x00\x00", 2)
        return r[0], r[1]

    def read_sr1(self) -> int:
        return self.cmd(b"\x05", 1)[0]

    def read_sr2(self) -> int:
        return self.cmd(b"\x35", 1)[0]

    def detect(self) -> tuple[int, list]:
        """Lê o JEDEC ID e retorna (id, chips compatíveis)."""
        jedec = self.read_jedec()
        if jedec in (0x000000, 0xFFFFFF):
            raise ChipError(
                f"Nenhuma memória respondeu (ID {jedec:06X}). Confira a orientação do chip "
                "(pino 1), o encaixe do clipe/soquete e se a placa está desligada.")
        cands = chipdb.find_spi_by_id(jedec)
        if not cands:
            g = chipdb.generic_from_id(jedec)
            if g:
                cands = [g]
        return jedec, cands

    def wait_ready(self, timeout: float = 5.0, stage: str | None = None, est: float = 0.0):
        start = time.monotonic()
        delay = 0.0
        while True:
            sr = self.read_sr1()
            if not sr & 0x01:
                return
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                raise ChipError("Tempo esgotado esperando a memória (bit WIP não liberou)")
            if stage and est:
                self._progress(min(int(elapsed * 1000), int(est * 1000) - 1), int(est * 1000), stage)
            else:
                self._check_cancel()
            if delay and getattr(self.b, "realtime", True):
                time.sleep(delay)
            delay = min(0.05, delay + 0.002)

    def write_enable(self, check: bool = False):
        self.cmd(b"\x06")
        if check and not self.read_sr1() & 0x02:
            raise ChipError("A memória não aceitou o Write Enable (WEL=0). Pino WP/HOLD ou "
                            "contato ruim?")

    # ------------------------------------------------------- proteção
    def _sr1_protect_mask(self) -> int:
        chip = self._need_chip()
        return 0x3C if (chip.jedec >> 16) in QE_IN_SR1 else 0x7C

    def protection_info(self) -> dict:
        chip = self._need_chip()
        sr1 = self.read_sr1()
        info = {"sr1": sr1, "sr2": None, "protected": bool(sr1 & self._sr1_protect_mask())}
        if chip.has("sr2"):
            sr2 = self.read_sr2()
            info["sr2"] = sr2
            if sr2 & 0x40:   # CMP=1 inverte a área protegida
                info["protected"] = True
        if chip.has("unlock98") or chip.has("ewsr"):
            # SST: proteção ligada no power-up; trata como protegido por segurança
            info["protected"] = info["protected"] or chip.has("unlock98")
        return info

    def unprotect(self):
        chip = self._need_chip()
        if chip.has("unlock98"):
            self.write_enable()
            self.cmd(b"\x98")            # Global Block Protection Unlock (SST26)
            self.wait_ready(1)
        sr1 = self.read_sr1()
        keep = 0x40 if (chip.jedec >> 16) in QE_IN_SR1 else 0x00
        new1 = sr1 & keep
        if chip.has("sr2"):
            sr2 = self.read_sr2()
            new2 = sr2 & ~0x40 & 0xFF    # limpa CMP, preserva QE e o resto
            self.write_enable()
            self.cmd(bytes([0x01, new1, new2]))
        elif chip.has("ewsr"):
            self.cmd(b"\x50")
            self.cmd(bytes([0x01, new1]))
        else:
            self.write_enable()
            self.cmd(bytes([0x01, new1]))
        self.wait_ready(2)
        info = self.protection_info()
        if info["protected"] and not chip.has("unlock98"):
            raise ChipError(
                f"Não foi possível remover a proteção (SR1=0x{info['sr1']:02X}). "
                "O pino WP pode estar em nível baixo com o bit SRP ativo.")
        self.log(f"Proteção removida (SR1=0x{info['sr1']:02X}"
                 + (f", SR2=0x{info['sr2']:02X})" if info["sr2"] is not None else ")"))

    # ------------------------------------------------- endereçamento
    @contextmanager
    def _addr_mode(self):
        chip = self._need_chip()
        use_en4b = chip.addr4 and chip.has("en4b")
        if use_en4b and self._addr_depth == 0:
            self.cmd(b"\xB7")
        self._addr_depth += 1
        try:
            yield
        finally:
            self._addr_depth -= 1
            if use_en4b and self._addr_depth == 0:
                self.cmd(b"\xE9")

    def _op(self, kind: str) -> int:
        chip = self._need_chip()
        op3, op4 = OPS[kind]
        return op4 if chip.addr4 and not chip.has("en4b") else op3

    def _addr(self, a: int) -> bytes:
        return a.to_bytes(4 if self._need_chip().addr4 else 3, "big")

    # ------------------------------------------------------- leitura
    def read(self, addr: int = 0, length: int | None = None, stage: str | None = "Lendo") -> bytes:
        chip = self._need_chip()
        if length is None:
            length = chip.size - addr
        if addr < 0 or addr + length > chip.size:
            raise ChipError("Faixa de leitura fora do tamanho da memória")
        out = bytearray(length)
        step = self.b.max_spi_read
        op = self._op("read")
        pos = 0
        with self._addr_mode():
            while pos < length:
                n = min(step, length - pos)
                out[pos:pos + n] = self.cmd(bytes([op]) + self._addr(addr + pos), n)
                pos += n
                if stage:
                    self._progress(pos, length, stage)
                else:
                    self._check_cancel()
        return bytes(out)

    def verify(self, data: bytes, addr: int = 0, stage: str = "Verificando") -> VerifyResult:
        chip = self._need_chip()
        if addr + len(data) > chip.size:
            raise ChipError("Arquivo maior que a memória")
        step = max(self.b.max_spi_read, 4096)
        res = VerifyResult(ok=True)
        pos = 0
        while pos < len(data):
            n = min(step, len(data) - pos)
            got = self.read(addr + pos, n, stage=None)
            exp = data[pos:pos + n]
            if got != exp:
                for i in range(n):
                    if got[i] != exp[i]:
                        res.mismatches += 1
                        if len(res.first) < 64:
                            res.first.append(addr + pos + i)
            pos += n
            self._progress(pos, len(data), stage)
        res.ok = res.mismatches == 0
        return res

    def blank_check(self, stage: str = "Teste de branco") -> tuple[bool, int | None]:
        chip = self._need_chip()
        step = max(self.b.max_spi_read, 4096)
        blank = b"\xFF" * step
        pos = 0
        while pos < chip.size:
            n = min(step, chip.size - pos)
            got = self.read(pos, n, stage=None)
            if got != blank[:n]:
                for i, v in enumerate(got):
                    if v != 0xFF:
                        return False, pos + i
            pos += n
            self._progress(pos, chip.size, stage)
        return True, None

    # ------------------------------------------------------- apagar
    def erase_chip(self, auto_unprotect: bool = False):
        chip = self._need_chip()
        self._prepare_write(auto_unprotect)
        mb = chip.size / (1024 * 1024)
        est = max(2.0, mb * 2.5)
        self.write_enable(check=True)
        self.cmd(b"\xC7")
        self.wait_ready(timeout=60 + mb * 25, stage="Apagando chip inteiro", est=est)
        self._progress(1, 1, "Apagando chip inteiro", force=True)

    def erase_sector(self, addr: int):
        with self._addr_mode():
            self.write_enable()
            self.cmd(bytes([self._op("se")]) + self._addr(addr))
            self.wait_ready(3)

    def erase_block(self, addr: int):
        with self._addr_mode():
            self.write_enable()
            self.cmd(bytes([self._op("be")]) + self._addr(addr))
            self.wait_ready(10)

    # ------------------------------------------------------- gravar
    def _prepare_write(self, auto_unprotect: bool = False):
        chip = self._need_chip()
        info = self.protection_info()
        if info["protected"]:
            if chip.has("unlock98") or auto_unprotect:
                self.unprotect()
            else:
                raise ProtectedError(
                    f"A memória está protegida contra escrita (SR1=0x{info['sr1']:02X}). "
                    "Use 'Desproteger' ou ative a desproteção automática.")

    def program(self, addr: int, data: bytes, skip_blank: bool = True,
                stage: str | None = "Gravando", stats: WriteStats | None = None):
        chip = self._need_chip()
        if addr + len(data) > chip.size:
            raise ChipError("Dados maiores que a memória")
        page = 1 if chip.has("byte_program") else chip.page
        pages = []
        i = 0
        while i < len(data):
            a = addr + i
            n = min(page - (a % page), len(data) - i)
            chunk = data[i:i + n]
            if skip_blank and chunk.count(0xFF) == n:
                if stats:
                    stats.skipped_bytes += n
            else:
                pages.append((a, bytes(chunk), i + n))
            i += n
        self._program_pages(pages, stats, stage, len(data))

    def _use_turbo(self) -> bool:
        chip = self._need_chip()
        return (self.turbo and getattr(self.b, "supports_turbo", False)
                and not chip.has("byte_program"))

    def _program_one(self, op, a, chunk):
        self.write_enable()
        self.cmd(bytes([op]) + self._addr(a) + chunk)
        self.wait_ready(1)

    def _program_pages(self, pages, stats, stage, total):
        """pages: lista de (endereço, dados, posição_para_progresso)."""
        op = self._op("pp")
        with self._addr_mode():
            if not self._use_turbo():
                for a, chunk, pos in pages:
                    self._program_one(op, a, chunk)
                    if stats:
                        stats.programmed_pages += 1
                    if stage:
                        self._progress(pos, total, stage)
                    else:
                        self._check_cancel()
                return
            alen = 4 if self._need_chip().addr4 else 3
            for k in range(0, len(pages), self.turbo_batch):
                batch = pages[k:k + self.turbo_batch]
                self.b.spi_program_pages([(a, c) for a, c, _ in batch], op, alen, self.turbo_delay_us)
                self.wait_ready(2)
                # confere o lote de uma vez e regrava do jeito normal o que falhou
                lo = batch[0][0]
                hi = batch[-1][0] + len(batch[-1][1])
                got = self.read(lo, hi - lo, stage=None)
                failed = [(a, c) for a, c, _ in batch if got[a - lo:a - lo + len(c)] != c]
                for a, c in failed:
                    self._program_one(op, a, c)
                if failed:
                    self.turbo_retries += len(failed)
                    if self.turbo_delay_us < 3500:   # chip mais lento: aumenta a espera
                        self.turbo_delay_us = min(3500, int(self.turbo_delay_us * 1.5))
                        self.log(f"Turbo: {len(failed)} página(s) regravada(s); espera ajustada "
                                 f"para {self.turbo_delay_us} µs")
                if stats:
                    stats.programmed_pages += len(batch)
                if stage:
                    self._progress(batch[-1][2], total, stage)
                else:
                    self._check_cancel()

    def write(self, data: bytes, addr: int = 0, smart: bool = True, verify: bool = True,
              auto_unprotect: bool = False) -> tuple[WriteStats, VerifyResult | None]:
        """Grava `data` em `addr`.

        smart=True: lê cada bloco, pula o que já é igual, apaga só os setores que
        precisam (bits 0->1) e programa só as páginas diferentes.
        smart=False: apaga a faixa (ou o chip inteiro) e programa tudo.
        """
        chip = self._need_chip()
        if addr + len(data) > chip.size:
            raise ChipError(f"O arquivo ({len(data)} bytes) é maior que a memória "
                            f"({chip.size} bytes)")
        self._prepare_write(auto_unprotect)
        stats = WriteStats()
        with self._addr_mode():
            if smart:
                self._smart_write(data, addr, stats)
            else:
                whole = addr == 0 and len(data) == chip.size
                if whole:
                    self.erase_chip(auto_unprotect)
                    stats.erased_blocks = chip.size // chip.block
                else:
                    self._erase_range(addr, len(data), stats)
                self.program(addr, data, stats=stats)
        vr = None
        if verify:
            vr = self.verify(data, addr)
        return stats, vr

    def _erase_range(self, addr, length, stats, stage="Apagando"):
        chip = self._need_chip()
        start = addr - addr % chip.sector
        end = addr + length
        a = start
        while a < end:
            if a % chip.block == 0 and a + chip.block <= end:
                self.erase_block(a)
                stats.erased_blocks += 1
                a += chip.block
            else:
                self.erase_sector(a)
                stats.erased_sectors += 1
                a += chip.sector
            self._progress(min(a, end) - start, end - start, stage)

    def _smart_write(self, data: bytes, addr: int, stats: WriteStats):
        chip = self._need_chip()
        end = addr + len(data)
        blk = addr - addr % chip.block
        stage = "Gravação inteligente"
        while blk < end:
            r0 = max(blk, addr)
            r1 = min(blk + chip.block, end)
            # setores tocados por esta faixa
            s0 = r0 - r0 % chip.sector
            s1 = r1 + (-r1 % chip.sector)
            cur = bytearray(self.read(s0, s1 - s0, stage=None))
            new = bytearray(cur)
            new[r0 - s0:r1 - s0] = data[r0 - addr:r1 - addr]
            if cur != new:
                need_erase = []
                for s in range(s0, s1, chip.sector):
                    o = s - s0
                    c = int.from_bytes(cur[o:o + chip.sector], "big")
                    n = int.from_bytes(new[o:o + chip.sector], "big")
                    if c & n != n:            # precisa transformar 0 em 1
                        need_erase.append(s)
                full_block = s0 == blk and s1 == blk + chip.block
                if full_block and len(need_erase) * 2 > chip.block // chip.sector:
                    self.erase_block(blk)
                    stats.erased_blocks += 1
                    cur[:] = b"\xFF" * len(cur)
                else:
                    for s in need_erase:
                        self.erase_sector(s)
                        stats.erased_sectors += 1
                        o = s - s0
                        cur[o:o + chip.sector] = b"\xFF" * chip.sector
                # programa apenas as páginas diferentes (em lote, para o modo turbo)
                step = 1 if chip.has("byte_program") else chip.page
                todo = []
                for p in range(s0, s1, step):
                    o = p - s0
                    if cur[o:o + step] != new[o:o + step] and new[o:o + step].count(0xFF) != step:
                        todo.append((p, bytes(new[o:o + step]), 0))
                self._program_pages(todo, stats, None, 0)
            else:
                stats.skipped_bytes += r1 - r0
            self._progress(r1 - addr, len(data), stage)
            blk += chip.block
