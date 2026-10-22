"""Testes das ferramentas: saúde do dump, EDID, histórico, Intel, transplante, turbo, benchmark."""

import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from synth_images import make_image  # noqa: E402

from ch341studio import benchmark, chipdb, edid as E, health, i18n, identify, intel  # noqa: E402
from ch341studio import transplant as T  # noqa: E402
from ch341studio.backends.simulator import SimBackend, SpiFlashModel  # noqa: E402
from ch341studio.history import History  # noqa: E402
from ch341studio.spiflash import SpiFlash  # noqa: E402

rnd = random.Random(7)
FW = bytes(rnd.getrandbits(8) for _ in range(1 << 20))


def flash_for(model):
    b = SimBackend(flash=model, eeprom=None)
    b.open()
    f = SpiFlash(b)
    f.chip = chipdb.find_spi_by_id(model.jedec)[0]
    return f


class HealthTests(unittest.TestCase):
    def levels(self, data, size=None):
        return [x.level for x in health.analyze_dump(data, size).findings]

    def test_good_dump(self):
        self.assertEqual(health.analyze_dump(FW, len(FW)).worst, health.INFO)

    def test_mirrored(self):
        self.assertIn(health.BAD, self.levels(FW[:1 << 19] * 2))

    def test_stuck_bit(self):
        self.assertIn(health.BAD, self.levels(bytes(b & 0xFE for b in FW)))

    def test_all_zero_and_pattern(self):
        self.assertEqual(health.analyze_dump(bytes(4096)).worst, health.BAD)
        self.assertEqual(health.analyze_dump(b"\xEF\x40\x17" * 2000).worst, health.BAD)

    def test_compare_reads(self):
        b = bytearray(FW)
        b[70000] ^= 1
        self.assertEqual(health.compare_reads(FW, bytes(b)), (1, [69632]))

    def test_clip(self):
        f = flash_for(SpiFlashModel(0xEF4014, 1 << 20))
        self.assertTrue(health.clip_test(f, rounds=5).stable)
        f.b.fake.flash = None             # chip "solto"
        self.assertFalse(health.clip_test(f, rounds=3).stable)


class EdidTests(unittest.TestCase):
    def make(self):
        e = bytearray(128)
        e[:8] = E.HEADER
        e[8:10] = E.encode_mfr("LGD").to_bytes(2, "big")
        e[18:20] = b"\x01\x04"
        e[72:90] = bytes([0, 0, 0, 0xFC, 0]) + b"TV\n          "
        e[90:108] = bytes([0, 0, 0, 0x10, 0]) + b" " * 13
        E.fix_checksum(e)
        return bytes(b"\xFF" * 16 + e)

    def test_edit_roundtrip(self):
        buf = bytearray(self.make())
        ed = E.Edid.from_buffer(buf)
        self.assertEqual((ed.offset, ed.manufacturer, ed.name), (16, "LGD", "TV"))
        ed.serial = 123456
        ed.set_text(E.TAG_SERIAL, "ABC123")
        ed.fix_checksums()
        ed.apply_to(buf)
        e2 = E.Edid.from_buffer(buf)
        self.assertEqual((e2.serial, e2.serial_text), (123456, "ABC123"))
        self.assertTrue(e2.checksum_ok())

    def test_bad_mfr(self):
        with self.assertRaises(ValueError):
            E.encode_mfr("")
        with self.assertRaises(ValueError):
            E.encode_mfr("A1B")


class HistoryTests(unittest.TestCase):
    def test_add_search_note(self):
        h = History(os.path.join(tempfile.mkdtemp(), "h.sqlite"))
        i = h.add("Leitura", "W25Q64", "EF4017", 8 << 20, "AABBCCDD", "ff" * 32, job="Cliente Ana")
        h.set_note(i, "placa B450")
        h.set_file(i, "/tmp/x.bin")
        r = h.search("ana")[0]
        self.assertEqual((r["note"], r["file"], r["chip_id"]), ("placa B450", "/tmp/x.bin", "EF4017"))
        self.assertEqual(len(h.find_by_hash("ff" * 32)), 1)


class IntelTests(unittest.TestCase):
    def test_regions_and_replace(self):
        img = make_image(1)
        d = intel.parse_descriptor(img)
        names = {r.name: (r.base, r.size) for r in d.regions}
        self.assertEqual(names["GbE"], (0x1000, 0x2000))
        self.assertEqual(names["BIOS"], (0x300000, 0x500000))
        bios = d.region("BIOS")
        new = intel.replace_region(img, bios, b"\x00" * bios.size)
        self.assertEqual(new[:bios.base], img[:bios.base])
        with self.assertRaises(ValueError):
            intel.replace_region(img, bios, b"\x00" * 10)

    def test_capsule(self):
        img = make_image(2)
        cap = bytes.fromhex("8BA63C4A2377FB48803D578CC1FEC44D") + (0x800).to_bytes(4, "little") \
            + bytes(0x800 - 20) + img
        self.assertEqual(intel.capsule_info(cap), ("AMI Aptio Capsule", 0x800))
        out, why = intel.strip_capsule(cap, len(img))
        self.assertEqual(out, img)
        self.assertIsNone(intel.strip_capsule(img, len(img)))

    def test_identify(self):
        idt = identify.identify(make_image(1))
        self.assertIn("Intel", idt.kind)
        self.assertTrue(any("X505.312" in d for d in idt.details))


class TransplantTests(unittest.TestCase):
    def test_flow(self):
        orig = make_image(1)
        clean = make_image(2, mac=b"\x88" * 6, key=b"00000-00000-00000-00000-00000", serial=b"DEFAULTS")
        items = {i.key: i for i in T.analyze(orig, clean)}
        self.assertTrue(items["gbe"].default and items["msdm"].default)
        self.assertFalse(items["nvram@300000"].default)        # arriscado: desmarcado
        new, done = T.apply(orig, clean, list(items.values()), {"gbe", "msdm"})
        self.assertEqual(new[0x1000:0x1006], b"\x00\x11\x22\x33\x44\x55")
        self.assertEqual(T.find_msdm(new)[1], "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY")
        self.assertEqual(new[0x300000:0x340000], clean[0x300000:0x340000])
        self.assertIn("PF2ABC12", T.serial_candidates(orig, clean))

    def test_size_mismatch(self):
        items = T.analyze(make_image(1), make_image(2)[:4 << 20])
        self.assertEqual(items[0].key, "size")
        self.assertFalse(items[0].available)


class TurboTests(unittest.TestCase):
    def test_turbo_fast_chip(self):
        m = SpiFlashModel(0xEF4014, 1 << 20)
        f = flash_for(m)
        f.turbo = True
        stats, vr = f.write(FW[:300000], smart=False)
        self.assertTrue(vr.ok)
        self.assertEqual(f.turbo_retries, 0)
        self.assertEqual(m.ignored_while_busy, 0)

    def test_turbo_slow_chip_recovers(self):
        m = SpiFlashModel(0xEF4014, 1 << 20)
        m.t_page_us = 3000                       # chip lento: a espera padrão não basta
        f = flash_for(m)
        f.turbo = True
        stats, vr = f.write(FW[:200000], smart=False)
        self.assertTrue(vr.ok)
        self.assertGreater(f.turbo_retries, 0)
        self.assertGreater(f.turbo_delay_us, 1000)

    def test_turbo_ignored_on_dll(self):
        from ch341studio.backends.dll_backend import DllBackend
        self.assertFalse(DllBackend.supports_turbo)


class BenchmarkTests(unittest.TestCase):
    def test_restores_area(self):
        m = SpiFlashModel(0xEF4014, 1 << 20, fill=FW)
        f = flash_for(m)
        res = benchmark.run(f, include_write=True)
        self.assertEqual(bytes(m.mem), FW)
        self.assertGreater(res.write_kibs, 0)
        self.assertGreater(res.turbo_kibs, 0)


class I18nTests(unittest.TestCase):
    def test_tr(self):
        i18n.set_lang("en")
        try:
            self.assertEqual(i18n.tr("Ler (F6)"), "Read (F6)")
            self.assertEqual(i18n.tr("Salvo em {p}", p="x.bin"), "Saved to x.bin")
            self.assertEqual(i18n.tr("texto sem tradução"), "texto sem tradução")
        finally:
            i18n.set_lang("pt")
        self.assertEqual(i18n.tr("Ler (F6)"), "Ler (F6)")


if __name__ == "__main__":
    unittest.main()


class TextSearchTests(unittest.TestCase):
    def test_version_ascii_and_utf16(self):
        from ch341studio import textsearch as TS
        d = (b"\xff" * 64 + b"\x00Version 86.04.3B.00.25 \x00junk"
             + "VERSION 90.02".encode("utf-16-le") + b"\x00\x00")
        r = TS.search(d, "version")
        self.assertEqual([(m.encoding, m.context.strip()) for m in r],
                         [("ASCII", "Version 86.04.3B.00.25"), ("UTF-16", "VERSION 90.02")])
        self.assertEqual(len(TS.search(d, "version", case_sensitive=True)), 0)
        self.assertEqual(len(TS.search(d, "version", utf16=False)), 1)
        self.assertEqual(TS.search_hex(d, "56 65 72")[0].offset, 65)


class AppImageIntegrationTests(unittest.TestCase):
    def test_shortcuts(self):
        from unittest import mock
        from ch341studio import integration as I
        tmp = tempfile.mkdtemp()
        home = os.path.join(tmp, "home")
        desk = os.path.join(home, "Desktop")
        os.makedirs(desk)
        app = os.path.join(tmp, "CH341Studio.AppImage")
        open(app, "w").close()
        env = {"HOME": home, "APPIMAGE": app, "XDG_DATA_HOME": ""}
        with mock.patch.dict(os.environ, env), mock.patch.object(I, "desktop_dir", lambda: desk), \
                mock.patch.object(I.sys, "platform", "linux"):
            self.assertFalse(I.is_integrated())
            created = I.integrate()
            self.assertEqual(len(created), 2)
            self.assertEqual(I.integrated_exec(), app)
            new = os.path.join(tmp, "novo.AppImage")
            os.rename(app, new)
            os.environ["APPIMAGE"] = new
            self.assertTrue(I.refresh_if_moved())
            self.assertIn(new, open(os.path.join(desk, "ch341studio.desktop")).read())
            self.assertEqual(len(I.remove_integration()), 3)
