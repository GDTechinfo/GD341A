"""Benchmark do gravador: velocidade de leitura e de gravação (normal × turbo)."""

import os
import time
from dataclasses import dataclass, field

from .errors import ChipError


@dataclass
class BenchResult:
    read_kibs: float = 0.0
    write_kibs: float = 0.0
    turbo_kibs: float = 0.0
    turbo_supported: bool = False
    turbo_retries: int = 0
    region: tuple = (0, 0)
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        fmt = lambda v: f"{v:.1f}".replace(".", ",") + " KiB/s"
        parts = [f"Leitura: {fmt(self.read_kibs)}"]
        if self.write_kibs:
            parts.append(f"Gravação normal: {fmt(self.write_kibs)}")
        if self.turbo_kibs:
            ganho = self.turbo_kibs / self.write_kibs if self.write_kibs else 0
            parts.append(f"Gravação turbo: {fmt(self.turbo_kibs)}"
                         + (f" ({ganho:.1f}× mais rápida)".replace(".", ",") if ganho else ""))
        elif self.write_kibs and not self.turbo_supported:
            parts.append("Turbo indisponível neste driver (use libusb)")
        return " • ".join(parts)


def run(flash, include_write: bool = False, write_size: int = 64 * 1024,
        read_size: int = 1024 * 1024, progress=None, log=None) -> BenchResult:
    """Mede leitura e, opcionalmente, gravação numa área no fim do chip.

    A gravação é feita numa cópia de segurança: o conteúdo original da área é lido
    antes e regravado (e verificado) no final.
    """
    log = log or (lambda m: None)
    chip = flash._need_chip()
    res = BenchResult(turbo_supported=getattr(flash.b, "supports_turbo", False))
    rs = min(read_size, chip.size)
    t = time.perf_counter()
    flash.read(0, rs, stage="Benchmark: leitura")
    res.read_kibs = rs / 1024 / max(time.perf_counter() - t, 1e-6)
    log(f"Leitura de {rs // 1024} KiB: {res.read_kibs:.1f} KiB/s")
    if not include_write:
        return res

    ws = min(write_size, chip.size // 2)
    ws -= ws % chip.block if ws >= chip.block else ws % chip.sector
    base = chip.size - ws
    res.region = (base, ws)
    original = flash.read(base, ws, stage="Benchmark: copiando a área de teste")
    flash._prepare_write()
    pattern = os.urandom(ws)
    old_turbo = flash.turbo
    try:
        modes = [False] + ([True] if res.turbo_supported and not chip.has("byte_program") else [])
        for turbo in modes:
            flash._erase_range(base, ws, stats=_Dummy(), stage="Benchmark: apagando")
            flash.turbo = turbo
            flash.turbo_retries = 0
            t = time.perf_counter()
            flash.program(base, pattern, skip_blank=False,
                          stage="Benchmark: gravando" + (" (turbo)" if turbo else ""))
            speed = ws / 1024 / max(time.perf_counter() - t, 1e-6)
            if flash.read(base, ws, stage=None) != pattern:
                raise ChipError("Benchmark: a gravação de teste não conferiu (verifique o contato)")
            if turbo:
                res.turbo_kibs = speed
                res.turbo_retries = flash.turbo_retries
            else:
                res.write_kibs = speed
            log(f"Gravação {'turbo' if turbo else 'normal'} de {ws // 1024} KiB: {speed:.1f} KiB/s")
    finally:
        flash.turbo = False
        flash._erase_range(base, ws, stats=_Dummy(), stage="Benchmark: restaurando")
        flash.program(base, original, stage="Benchmark: restaurando")
        flash.turbo = old_turbo
        if flash.read(base, ws, stage=None) != original:
            raise ChipError("ATENÇÃO: a área de teste não foi restaurada corretamente! "
                            f"Endereço 0x{base:X}, {ws} bytes.")
        log("Área de teste restaurada e conferida.")
    return res


class _Dummy:
    erased_sectors = erased_blocks = programmed_pages = skipped_bytes = 0
