"""Testes do núcleo usando o CH341A simulado (não precisa de hardware).

Rodar:  python -m unittest discover -s tests -v
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ch341studio import chipdb, fileio  # noqa: E402
from ch341studio import protocol as P  # noqa: E402
from ch341studio.backends.simulator import (  # noqa: E402
    Eeprom24Model, FakeCH341Usb, SimBackend, SpiFlashModel)
from ch341studio.eeprom24 import Eeprom24  # noqa: E402
from ch341studio.errors import Cancelled, ChipError, ProtectedError  # noqa: E402
from ch341studio.spiflash import SpiFlash  # noqa: E402

rnd = random.Random(1234)


def rbytes(n):
    return bytes(rnd.getrandbits(8) for _ in range(n))


class ProtocolTests(unittest.TestCase):
    def test_bit_reverse(self):
        self.assertEqual(P.rev(b"\x01\x80\x0F\xF0"), b"\x80\x01\xF0\x0F")
        self.assertEqual(P.rev(P.rev(bytes(range(256)))), bytes(range(256)))

    def test_spi_packets_never_padded(self):
        blob, n = P.spi_transaction(b"\x03\x00\x00\x00", 60)
        self.assertEqual(n, 64)
        self.assertEqual(blob[:4], bytes([0xAB, 0x80 | 0x36, 0x40 | 0x3F, 0x20]))
        self.assertEqual(len(blob[32:]), 64 + 3)          # 3 pacotes: 31+31+2 dados
        self.assertEqual(blob[32], P.CMD_SPI_STREAM)
        self.assertEqual(blob[64], P.CMD_SPI_STREAM)
        self.assertEqual(blob[96], P.CMD_SPI_STREAM)

    def test_i2c_packet_limits(self):
        pkt = P.i2c_read_packet(0x50, b"\x00\x10", 32)
        self.assertLessEqual(len(pkt), 32)
        self.assertEqual(pkt[-2:], bytes([P.I2C_STM_STO, P.I2C_STM_END]))
        with self.assertRaises(ValueError):
            P.i2c_write_packet(0x50, bytes(P.I2C_MAX_WRITE + 1))


class ChipDbTests(unittest.TestCase):
    def test_lookup(self):
        self.assertEqual(chipdb.find_spi_by_id(0xEF4017)[0].name, "W25Q64")
        self.assertEqual(chipdb.find_spi_by_name("w25q128").size, 16 * 1024 * 1024)
        self.assertEqual(chipdb.find_spi_by_name("MX25L3206").jedec, 0xC22016)
        self.assertEqual(chipdb.find_spi_by_name("MX25L12873").jedec, 0xC22018)
        self.assertEqual(chipdb.find_spi_by_name("S25FL128S").jedec, 0x012018)
        self.assertEqual(chipdb.find_spi_by_name("MT25QL256").jedec, 0x20BA19)
        self.assertTrue(chipdb.find_spi_by_name("W25Q64FW").is_18v)
        self.assertEqual(chipdb.find_eeprom("AT24C256").size, 32768)

    def test_unique_names(self):
        names = [c.name for c in chipdb.spi_chips()]
        self.assertEqual(len(names), len(set(names)))

    def test_generic(self):
        g = chipdb.generic_from_id(0xAB4017)
        self.assertEqual(g.size, 8 * 1024 * 1024)
        self.assertEqual(chipdb.generic_from_id(0xEF4020).size, 64 * 1024 * 1024)
        self.assertIsNone(chipdb.generic_from_id(0xFFFFFF))


class SpiFlashTests(unittest.TestCase):
    def make(self, jedec=0xEF4014, size=1024 * 1024, **kw):
        self.model = SpiFlashModel(jedec, size, **kw)
        self.backend = SimBackend(flash=self.model, eeprom=None)
        self.backend.open()
        self.flash = SpiFlash(self.backend)
        jedec_read, cands = self.flash.detect()
        self.assertEqual(jedec_read, jedec)
        self.flash.chip = cands[0]
        return self.flash

    def test_read_matches_model(self):
        f = self.make()
        content = rbytes(70000)
        self.model.mem[5000:5000 + len(content)] = content
        self.assertEqual(f.read(5000, len(content)), content)
        self.assertEqual(f.read(), bytes(self.model.mem))

    def test_full_write_and_verify(self):
        f = self.make()
        data = rbytes(1024 * 1024)
        stats, vr = f.write(data, smart=False)
        self.assertTrue(vr.ok)
        self.assertEqual(bytes(self.model.mem), data)
        self.assertIn(0xC7, self.model.log)          # chip erase usado

    def test_smart_write_touches_only_changes(self):
        f = self.make()
        base = rbytes(1024 * 1024)
        f.write(base, smart=False, verify=False)
        changed = bytearray(base)
        changed[123456] ^= 0xFF          # precisa apagar 1 setor
        changed[700000] &= 0x0F          # só zera bits: dá para gravar sem apagar
        self.model.log.clear()
        stats, vr = f.write(bytes(changed))
        self.assertTrue(vr.ok)
        self.assertEqual(stats.erased_sectors, 1)
        self.assertEqual(stats.erased_blocks, 0)
        self.assertEqual(stats.programmed_pages, 16 + 1)
        self.assertEqual(bytes(self.model.mem), bytes(changed))

    def test_partial_write_unaligned(self):
        f = self.make()
        before = rbytes(1024 * 1024)
        f.write(before, smart=False, verify=False)
        patch = rbytes(10000)
        f.write(patch, addr=4097)
        expected = bytearray(before)
        expected[4097:4097 + len(patch)] = patch
        self.assertEqual(bytes(self.model.mem), bytes(expected))

    def test_protection(self):
        f = self.make(sr1=0x1C, sr2=0x02)
        with self.assertRaises(ProtectedError):
            f.write(b"\x00" * 16)
        stats, vr = f.write(b"\x00" * 16, auto_unprotect=True)
        self.assertTrue(vr.ok)
        self.assertEqual(self.model.sr2 & 0x02, 0x02, "QE precisa ser preservado")

    def test_macronix_keeps_qe_in_sr1(self):
        f = self.make(jedec=0xC22016, size=4 * 1024 * 1024, sr1=0x40 | 0x1C)
        f.unprotect()
        self.assertEqual(self.model.sr1 & 0x40, 0x40)
        self.assertEqual(self.model.sr1 & 0x3C, 0)

    def test_erase_and_blank(self):
        f = self.make()
        self.model.mem[999] = 0x12
        ok, first = f.blank_check()
        self.assertFalse(ok)
        self.assertEqual(first, 999)
        f.erase_chip()
        self.assertEqual(f.blank_check(), (True, None))

    def test_verify_reports_addresses(self):
        f = self.make()
        data = bytearray(b"\xFF" * 4096)
        data[10] = 0
        vr = f.verify(bytes(data))
        self.assertFalse(vr.ok)
        self.assertEqual(vr.first, [10])

    def test_32mb_uses_4byte_opcodes(self):
        f = self.make(jedec=0xEF4019, size=32 * 1024 * 1024)
        self.assertTrue(f.chip.addr4)
        payload = rbytes(5000)
        f.write(rbytes(5000), addr=20 * 1024 * 1024 + 100, verify=False)
        f.write(payload, addr=20 * 1024 * 1024 + 100)   # agora precisa apagar
        self.assertEqual(f.read(20 * 1024 * 1024 + 100, 5000), payload)
        self.assertIn(0x12, self.model.log)
        self.assertIn(0x21, self.model.log)

    def test_byte_program_chip(self):
        f = self.make(jedec=0xBF2541, size=2 * 1024 * 1024, sr1=0x1C, byte_program_only=True)
        data = rbytes(600)
        stats, vr = f.write(data, auto_unprotect=True)
        self.assertTrue(vr.ok)
        self.assertIn(0x50, self.model.log)          # EWSR das SST25

    def test_cancel(self):
        f = self.make()
        f.cancel_cb = lambda: True
        with self.assertRaises(Cancelled):
            f.read()

    def test_no_chip(self):
        backend = SimBackend(flash=SpiFlashModel(0xFFFFFF, 1024), eeprom=None)
        backend.fake.flash = None
        backend.open()
        with self.assertRaises(ChipError):
            SpiFlash(backend).detect()


class EepromTests(unittest.TestCase):
    def run_chip(self, name, model):
        chip = chipdb.find_eeprom(name)
        b = SimBackend(flash=None, eeprom=model)
        b.open()
        e = Eeprom24(b, chip)
        data = rbytes(chip.size)
        vr = e.write(data)
        self.assertTrue(vr.ok, name)
        self.assertEqual(bytes(model.mem), data)
        self.assertEqual(e.read(), data)
        self.assertEqual(e.read(chip.size - 40, 40), data[-40:])

    def test_24c02(self):
        self.run_chip("24C02", Eeprom24Model(256, 1, 8))

    def test_24c16_bank_bits(self):
        self.run_chip("24C16", Eeprom24Model(2048, 1, 16))

    def test_24c256(self):
        self.run_chip("24C256", Eeprom24Model(32768, 2, 64))

    def test_24c1024(self):
        self.run_chip("24C1024", Eeprom24Model(131072, 2, 256))


class FileTests(unittest.TestCase):
    def test_ihex_roundtrip(self):
        data = rbytes(200000)
        self.assertEqual(fileio.parse_ihex(fileio.to_ihex(data)), data)

    def test_diff(self):
        a = bytes(10000)
        b = bytearray(a)
        b[5] = 1
        b[6] = 1
        b[9000] = 7
        mask, count = fileio.diff_mask(a, bytes(b))
        self.assertEqual(count, 3)
        self.assertEqual(fileio.diff_ranges(mask), [(5, 7), (9000, 9001)])


class UsbFramingTests(unittest.TestCase):
    def test_packets_split_like_hardware(self):
        fake = FakeCH341Usb(SpiFlashModel(), None)
        b = SimBackend.__new__(SimBackend)
        from ch341studio.backends.libusb_backend import LibusbBackend
        LibusbBackend.__init__(b, device=fake)
        b.fake = fake
        b.open()
        f = SpiFlash(b)
        f.chip = chipdb.find_spi_by_name("W25Q64")
        f.read(0, 16384)
        # 1 pacote de CS + ceil((5+16384)/31) pacotes SPI + 1 de liberação, + init
        self.assertGreater(fake.packets, 16384 // 31)


if __name__ == "__main__":
    unittest.main()


class DllLimitTests(unittest.TestCase):
    """DLL falsa que, como a CH341DLL real, recusa buffers acima de 4096 bytes."""

    def test_operations_stay_within_dll_buffer(self):
        import ctypes
        from ch341studio.backends import dll_backend as D

        model = SpiFlashModel(0xC84014, 1024 * 1024)   # GD25Q80
        sizes = []

        class FakeDll:
            def CH341StreamSPI4(self, idx, cs, length, buf):
                sizes.append(length)
                if length > 4096:
                    return False
                data = bytes(buf)
                model.begin()
                out = model.exchange(data)
                model.end()
                ctypes.memmove(buf, out, length)
                return True

        b = D.DllBackend()
        b._dll = FakeDll()
        b.realtime = False
        f = SpiFlash(b)
        _, cands = f.detect()
        f.chip = cands[0]
        data = rbytes(1024 * 1024)
        stats, vr = f.write(data)
        self.assertTrue(vr.ok)
        self.assertEqual(f.read(), data)
        self.assertLessEqual(max(sizes), 4096)


class LibusbWindowsDriverTests(unittest.TestCase):
    def test_wch_driver_gives_friendly_error(self):
        """No Windows com driver WCH, a libusb levanta NotImplementedError no open."""
        import types
        from ch341studio.backends.libusb_backend import LibusbBackend
        from ch341studio.errors import BackendUnavailable

        class Dev:
            def is_kernel_driver_active(self, i):
                raise NotImplementedError

            def set_configuration(self):
                raise NotImplementedError("Operation not supported or unimplemented on this platform")

        usb = types.ModuleType("usb")
        usb.core = types.ModuleType("usb.core")
        usb.util = types.ModuleType("usb.util")
        usb.core.USBError = type("USBError", (Exception,), {})
        saved = {k: sys.modules.get(k) for k in ("usb", "usb.core", "usb.util")}
        sys.modules.update({"usb": usb, "usb.core": usb.core, "usb.util": usb.util})
        try:
            b = LibusbBackend()
            b._find_device = lambda: Dev()
            with self.assertRaises(BackendUnavailable) as cm:
                b.open()
            self.assertIn("CH341DLL", str(cm.exception))
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
