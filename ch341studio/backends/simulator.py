"""Simulador de CH341A + memórias, para testes e demonstração sem hardware.

O `FakeCH341Usb` imita um dispositivo pyusb: recebe os MESMOS bytes que iriam
para o USB, divide em pacotes de 32 bytes como o hardware faria e interpreta os
comandos UIO/SPI/I2C. Assim o driver libusb real é exercitado de ponta a ponta.
"""

import threading

from .. import protocol as P
from .base import Backend
from .libusb_backend import LibusbBackend

# ======================================================================
#  Modelo de memória SPI NOR (estilo W25Qxx)
# ======================================================================

READ_OPS = {0x03: 4, 0x0B: 5, 0x13: 5, 0x0C: 6}   # opcode -> bytes de cabeçalho


class SpiFlashModel:
    def __init__(self, jedec: int = 0xEF4017, size: int = 8 * 1024 * 1024,
                 sr1: int = 0x00, sr2: int = 0x02, fill: bytes | None = None,
                 byte_program_only: bool = False):
        self.jedec = jedec
        self.size = size
        self.mem = bytearray(b"\xFF" * size)
        if fill:
            self.mem[:len(fill)] = fill
        self.sr1 = sr1          # bits: 0 WIP, 1 WEL, 2-4 BP, 5 TB, 6 SEC, 7 SRP
        self.sr2 = sr2          # bit 1 QE (preservado), bit 6 CMP
        self.addr4 = False
        # tempo simulado (µs): o chip fica ocupado (WIP=1) por um tempo após gravar/apagar
        self.busy_us = 0
        self.t_page_us = 700        # gravação de página (típico de W25Qxx)
        self.t_sector_us = 30000
        self.t_block_us = 150000
        self.t_chip_us = 400000
        self.t_wrsr_us = 5000
        self.ignored_while_busy = 0
        self.byte_program_only = byte_program_only
        self.ewsr = False
        self.rx = bytearray()
        self.selected = False
        self.log: list[int] = []   # opcodes executados (para testes)

    # --- helpers
    @property
    def protected(self):
        return bool(self.sr1 & 0x1C)

    def _addr(self, start: int, n: int) -> int:
        return int.from_bytes(self.rx[start:start + n], "big") % self.size

    def _addr_len(self, op):
        if op in (0x13, 0x12, 0x21, 0xDC, 0x0C):
            return 4
        return 4 if self.addr4 else 3

    # --- barramento
    def tick(self, us: float):
        self.busy_us = max(0, self.busy_us - us)

    @property
    def busy_polls(self):           # compatibilidade
        return 1 if self.busy_us > 0 else 0

    def begin(self):
        self.tick(100)              # custo de uma transação (USB + CS)
        self.selected = True
        self.rx = bytearray()

    def exchange(self, mosi: bytes) -> bytes:
        start = len(self.rx)
        self.rx += mosi
        n = len(mosi)
        self.tick(5 * n)            # ~5 µs por byte no barramento SPI
        op = self.rx[0]
        out = bytearray(b"\xFF" * n)
        if op == 0x9F:
            resp = b"\xFF" + self.jedec.to_bytes(3, "big")
            for i in range(n):
                pos = start + i
                if pos < len(resp):
                    out[i] = resp[pos]
        elif op in (0x05, 0x35, 0x15):
            if op == 0x05:
                val = self.sr1 | (0x01 if self.busy_us > 0 else 0)
            else:
                val = self.sr2 if op == 0x35 else 0x00
            for i in range(n):
                if start + i >= 1:
                    out[i] = val
        elif op in READ_OPS or (op == 0x03 and self.addr4):
            alen = self._addr_len(op)
            hdr = 1 + alen + (1 if op in (0x0B, 0x0C) else 0)
            for i in range(n):
                pos = start + i
                if pos >= hdr:
                    # copia em bloco o restante deste pedaço
                    a = self._addr(1, alen) + (pos - hdr)
                    cnt = n - i
                    chunk = bytearray()
                    while len(chunk) < cnt:
                        a %= self.size
                        take = min(cnt - len(chunk), self.size - a)
                        chunk += self.mem[a:a + take]
                        a += take
                    out[i:] = chunk
                    break
        elif op == 0x90:
            ids = bytes([self.jedec >> 16, self.jedec & 0xFF])
            for i in range(n):
                pos = start + i
                if pos >= 4:
                    out[i] = ids[(pos - 4) % 2]
        elif op == 0xAB:
            for i in range(n):
                if start + i >= 4:
                    out[i] = (self.jedec & 0xFF) - 1
        return bytes(out)

    def end(self):
        self.selected = False
        if not self.rx:
            return
        op = self.rx[0]
        self.log.append(op)
        wel = bool(self.sr1 & 0x02)
        if self.busy_us > 0 and op not in (0x05, 0x35, 0x15, 0x9F):
            self.ignored_while_busy += 1
            return  # ocupado: o chip real ignora comandos
        if op == 0x06:
            self.sr1 |= 0x02
        elif op == 0x04:
            self.sr1 &= ~0x02
        elif op == 0x50:
            self.ewsr = True
        elif op == 0xB7:
            self.addr4 = True
        elif op == 0xE9:
            self.addr4 = False
        elif op == 0x98 and wel:
            self.sr1 &= ~0x1C
            self.sr1 &= ~0x02
        elif op == 0x01 and (wel or self.ewsr):
            if len(self.rx) >= 2:
                self.sr1 = (self.sr1 & 0x03) | (self.rx[1] & 0xFC)
            if len(self.rx) >= 3:
                self.sr2 = self.rx[2]
            elif len(self.rx) == 2:
                # comportamento dos W25Q antigos: 1 byte zera CMP e QE
                self.sr2 &= ~0x42
            self.sr1 &= ~0x02
            self.ewsr = False
            self.busy_us = self.t_wrsr_us
        elif op in (0x02, 0x12) and wel:
            alen = self._addr_len(op)
            a = self._addr(1, alen)
            data = self.rx[1 + alen:]
            if self.byte_program_only:
                data = data[:1]
            if not self.protected:
                page = a & ~0xFF
                for i, b in enumerate(data[-256:]):
                    addr = page | ((a + i) & 0xFF)
                    self.mem[addr] &= b
            self.sr1 &= ~0x02
            self.busy_us = self.t_page_us
        elif op in (0x20, 0x21, 0x52, 0xD8, 0xDC) and wel:
            alen = self._addr_len(op)
            a = self._addr(1, alen)
            sz = {0x20: 4096, 0x21: 4096, 0x52: 32768, 0xD8: 65536, 0xDC: 65536}[op]
            a &= ~(sz - 1)
            if not self.protected:
                self.mem[a:a + sz] = b"\xFF" * sz
            self.sr1 &= ~0x02
            self.busy_us = self.t_sector_us if sz == 4096 else self.t_block_us
        elif op in (0xC7, 0x60) and wel:
            if not self.protected:
                self.mem[:] = b"\xFF" * self.size
            self.sr1 &= ~0x02
            self.busy_us = self.t_chip_us


# ======================================================================
#  Modelo de EEPROM I2C 24Cxx
# ======================================================================

class Eeprom24Model:
    def __init__(self, size=256, addr_bytes=1, page=8, base=0x50, fill: bytes | None = None):
        self.size = size
        self.addr_bytes = addr_bytes
        self.page = page
        self.base = base
        self.mem = bytearray(b"\xFF" * size)
        if fill:
            self.mem[:len(fill)] = fill
        self.ptr = 0
        self._state = "idle"
        self._buf = bytearray()
        self._bank = 0
        self._read = False

    def _bank_bits(self):
        span = 256 if self.addr_bytes == 1 else 65536
        return max(0, (self.size - 1) // span).bit_length()

    def matches(self, dev8: int) -> bool:
        mask = (1 << self._bank_bits()) - 1
        return ((dev8 >> 1) & ~mask) == self.base

    def start(self):
        self._commit()
        self._state = "addr"
        self._buf = bytearray()

    def write_byte(self, b: int) -> bool:
        if self._state == "addr":
            if not self.matches(b):
                self._state = "nack"
                return False
            self._bank = (b >> 1) & ((1 << self._bank_bits()) - 1)
            self._read = bool(b & 1)
            self._state = "data"
            if self._read:
                return True
            self._buf = bytearray()
            return True
        if self._state == "data" and not self._read:
            self._buf.append(b)
            return True
        return False

    def read_byte(self) -> int:
        if self._state != "data" or not self._read:
            return 0xFF
        v = self.mem[self.ptr % self.size]
        self.ptr = (self.ptr + 1) % self.size
        return v

    def _commit(self):
        if self._state == "data" and not self._read and self._buf:
            ab = self.addr_bytes
            if len(self._buf) >= ab:
                span_shift = 8 * ab
                addr = (self._bank << span_shift) | int.from_bytes(self._buf[:ab], "big")
                self.ptr = addr % self.size
                data = self._buf[ab:]
                if data:
                    page_start = self.ptr - (self.ptr % self.page)
                    off = self.ptr % self.page
                    for b in data:
                        self.mem[page_start + off] = b
                        off = (off + 1) % self.page
            self._buf = bytearray()
        self._state = "idle"

    def stop(self):
        self._commit()


# ======================================================================
#  CH341A falso (nível USB)
# ======================================================================

class FakeCH341Usb:
    """Imita um usb.core.Device do pyusb com write()/read()."""

    def __init__(self, flash: SpiFlashModel | None = None, eeprom: Eeprom24Model | None = None):
        self.flash = flash
        self.eeprom = eeprom
        self.pins = P.PINS_IDLE
        self.dir = 0
        self._in = bytearray()
        self._cond = threading.Condition()
        self.packets = 0
        self.i2c_speed = None

    # API pyusb -----------------------------------------------------------
    def write(self, ep, data, timeout=None):
        assert ep == P.EP_OUT
        data = bytes(data)
        # o host-controller divide a transferência em pacotes de 32 bytes
        for i in range(0, len(data), P.PACKET):
            self._packet(data[i:i + P.PACKET])
        return len(data)

    def read(self, ep, size, timeout=None):
        assert ep == P.EP_IN
        with self._cond:
            if not self._in:
                self._cond.wait((timeout or 1000) / 1000)
            if not self._in:
                raise TimeoutError("timeout (simulador)")
            chunk = bytes(self._in[:size])
            del self._in[:size]
            return chunk

    def _push(self, data: bytes):
        if data:
            with self._cond:
                self._in += data
                self._cond.notify_all()

    # interpretação ------------------------------------------------------
    def _packet(self, pkt: bytes):
        self.packets += 1
        cmd = pkt[0]
        if cmd == P.CMD_SPI_STREAM:
            payload = P.rev(pkt[1:])
            if self.flash and self.flash.selected and self.dir & 0x01:
                resp = self.flash.exchange(payload)
            else:
                resp = b"\xFF" * len(payload)
            self._push(P.rev(resp))
        elif cmd == P.CMD_UIO_STREAM:
            i = 1
            while i < len(pkt):
                sub = pkt[i]
                if sub == P.UIO_STM_END:
                    break
                kind = sub & 0xC0
                if kind == P.UIO_STM_OUT:
                    self._set_pins(sub & 0x3F)
                elif kind == P.UIO_STM_US:
                    if self.flash:
                        self.flash.tick(sub & 0x3F)
                elif kind == P.UIO_STM_DIR:
                    self.dir = sub & 0x3F
                elif kind == P.UIO_STM_IN:
                    self._push(bytes([self.pins]))
                i += 1
        elif cmd == P.CMD_I2C_STREAM:
            self._i2c(pkt)

    def _set_pins(self, value: int):
        old_cs = self.pins & 0x01
        self.pins = value
        new_cs = value & 0x01
        if self.flash and (self.dir & 0x01):
            if old_cs and not new_cs:
                self.flash.begin()
            elif not old_cs and new_cs:
                self.flash.end()

    def _i2c(self, pkt: bytes):
        dev = self.eeprom
        i = 1
        while i < len(pkt):
            sub = pkt[i]
            i += 1
            if sub == P.I2C_STM_END:
                break
            if sub == P.I2C_STM_STA:
                if dev:
                    dev.start()
            elif sub == P.I2C_STM_STO:
                if dev:
                    dev.stop()
            elif 0x60 <= sub <= 0x63:
                self.i2c_speed = sub & 0x03
            elif 0x40 <= sub <= 0x5F:
                pass  # atrasos (µs/ms)
            elif sub & 0xC0 == P.I2C_STM_OUT:
                n = sub & 0x3F
                count = n if n else 1
                for b in pkt[i:i + count]:
                    ack = dev.write_byte(b) if dev else False
                    if n == 0:
                        self._push(bytes([0x00 if ack else 0x01]))
                i += count
            elif sub & 0xC0 == P.I2C_STM_IN:
                n = sub & 0x3F
                count = n if n else 1
                self._push(bytes(dev.read_byte() if dev else 0xFF for _ in range(count)))


class SimBackend(LibusbBackend):
    """Driver libusb rodando sobre o CH341A simulado."""

    name = "Simulador"
    realtime = False

    def __init__(self, flash: SpiFlashModel | None = None, eeprom: Eeprom24Model | None = None):
        if flash is None and eeprom is None:
            flash, eeprom = demo_chips()
        self.fake = FakeCH341Usb(flash, eeprom)
        super().__init__(device=self.fake)

    @classmethod
    def available(cls):
        return True, ""

    def describe(self):
        return "Simulador (sem hardware)"


def demo_chips():
    """W25Q64 (8 MiB) com um 'firmware' de exemplo + 24C02."""
    import hashlib
    pattern = bytearray()
    seed = b"GDTech Informatica - CH341 Studio"
    while len(pattern) < 512 * 1024:
        seed = hashlib.sha256(seed).digest()
        pattern += seed
    header = b"$IBIOSI$ DEMO BIOS GDTECH\x00" + bytes(range(256))
    flash = SpiFlashModel(0xEF4017, 8 * 1024 * 1024, fill=header + bytes(pattern))
    eeprom = Eeprom24Model(256, 1, 8, fill=b"24C02 demo GDTech" + bytes(range(64)))
    return flash, eeprom


__all__ = ["SpiFlashModel", "Eeprom24Model", "FakeCH341Usb", "SimBackend", "Backend"]
