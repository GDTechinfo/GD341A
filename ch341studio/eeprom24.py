"""Operações em EEPROMs I2C 24Cxx."""

import time

from .chipdb import EepromChip
from .errors import Cancelled, ChipError
from .spiflash import VerifyResult

WRITE_CYCLE_S = 0.006   # tempo máximo típico de escrita de página (5 ms) + margem


class Eeprom24:
    def __init__(self, backend, chip: EepromChip, base: int = 0x50,
                 progress=None, cancel=None, log=None):
        self.b = backend
        self.chip = chip
        self.base = base
        self.progress_cb = progress
        self.cancel_cb = cancel
        self.log_cb = log
        self._last = 0.0

    def _progress(self, done, total, stage):
        if self.cancel_cb and self.cancel_cb():
            raise Cancelled("Operação cancelada")
        now = time.monotonic()
        if self.progress_cb and (done >= total or now - self._last > 0.05):
            self._last = now
            self.progress_cb(done, total, stage)

    @property
    def span(self) -> int:
        """Quantos bytes cabem no endereço interno (o resto vai no endereço do dispositivo)."""
        return 256 if self.chip.addr_bytes == 1 else 65536

    def _target(self, a: int) -> tuple[int, bytes]:
        bank = a // self.span
        return self.base | bank, (a % self.span).to_bytes(self.chip.addr_bytes, "big")

    def _check_range(self, addr, length):
        if addr < 0 or addr + length > self.chip.size:
            raise ChipError(f"Faixa fora do tamanho da EEPROM ({self.chip.size} bytes)")

    # ------------------------------------------------------------------
    def read(self, addr: int = 0, length: int | None = None, stage: str | None = "Lendo") -> bytes:
        if length is None:
            length = self.chip.size - addr
        self._check_range(addr, length)
        out = bytearray()
        pos = 0
        while pos < length:
            a = addr + pos
            n = min(256, length - pos, self.span - a % self.span)
            dev, prefix = self._target(a)
            out += self.b.i2c_read(dev, prefix, n)
            pos += n
            if stage:
                self._progress(pos, length, stage)
        return bytes(out)

    def write(self, data: bytes, addr: int = 0, verify: bool = True,
              stage: str = "Gravando") -> VerifyResult | None:
        self._check_range(addr, len(data))
        max_data = self.b.max_i2c_write - self.chip.addr_bytes
        pos = 0
        while pos < len(data):
            a = addr + pos
            n = min(self.chip.page - a % self.chip.page, max_data, len(data) - pos)
            dev, prefix = self._target(a)
            self.b.i2c_write(dev, prefix + data[pos:pos + n])
            if getattr(self.b, "realtime", True):
                time.sleep(WRITE_CYCLE_S)
            pos += n
            self._progress(pos, len(data), stage)
        if verify:
            return self.verify(data, addr)
        return None

    def verify(self, data: bytes, addr: int = 0) -> VerifyResult:
        got = self.read(addr, len(data), stage="Verificando")
        res = VerifyResult(ok=got == data)
        if not res.ok:
            for i, (x, y) in enumerate(zip(got, data)):
                if x != y:
                    res.mismatches += 1
                    if len(res.first) < 64:
                        res.first.append(addr + i)
        return res

    def erase(self) -> VerifyResult | None:
        return self.write(b"\xFF" * self.chip.size, verify=True, stage="Apagando (0xFF)")

    def blank_check(self) -> tuple[bool, int | None]:
        data = self.read(stage="Teste de branco")
        for i, v in enumerate(data):
            if v != 0xFF:
                return False, i
        return True, None
