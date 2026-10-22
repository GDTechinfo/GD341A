"""Testes do atualizador automático do banco de chips (sem rede)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ch341studio import chipdb, chipupdate as U  # noqa: E402


def ims_record(typ, vendor, name, jedec=0, size=0, block=256, type_hex=0, algo=0, vcc=0):
    r = bytearray(0x44)
    txt = f"{typ},{vendor},{name}".encode()
    r[:len(txt)] = txt
    r[0x30], r[0x31], r[0x32] = jedec & 0xFF, (jedec >> 8) & 0xFF, jedec >> 16
    r[0x34:0x38] = size.to_bytes(4, "little")
    r[0x38:0x3A] = block.to_bytes(2, "little")
    r[0x3A], r[0x3B], r[0x43] = type_hex, algo, vcc
    return bytes(r)


IMS = b"".join([
    ims_record("SPI_FLASH", "WINBOND", "W25Q64JV", 0xEF4017, 8 << 20),
    ims_record("SPI_FLASH", "XTX", "XT25F128F", 0x0B4018, 16 << 20),
    ims_record("SPI_FLASH", "MACRONIX", "MX25U6435F", 0xC22537, 8 << 20, vcc=1),
    ims_record("SPI_NAND", "X", "NAND1", 0xEFAA21, 128 << 20, type_hex=6),
    ims_record("24_EEPROM", "COMMON", "24C02", 0, 256, 8, 1, 1),
    ims_record("24_EEPROM", "COMMON", "24C512", 0, 65536, 128, 1, 2),
])

ASP = b"""<?xml version="1.0" encoding="utf-8"?>
<chiplist><SPI>
  <WINBOND><W25Q64JV-IQ-JQ id="EF4017" page="256" size="8388608"/></WINBOND>
  <SST><SST25VF016B id="BF2541" page="SSTB" size="2097152"/></SST>
  <ATMEL><AT45DB041 id="1F2400" page="264" size="540672" spicmd="45"/></ATMEL>
  <MACRONIX><MX25U3235F_1.8V id="C22536" page="256" size="4194304"/></MACRONIX>
  <BAD><ZERO id="000000" page="256" size="1048576"/></BAD>
</SPI><I2C><ATMEL><AT24C64 page="32" size="8192"/></ATMEL></I2C></chiplist>
"""

FR_H = b"#define WINBOND_NEX_ID\t0xEF\n#define WINBOND_NEX_W25Q256_V\t0x4019\n#define SST_ID 0xBF\n"
FR_C = b"""
	{
		.vendor		= "Winbond",
		.name		= "W25Q256JV_Q",
		.bustype	= BUS_SPI,
		.manufacture_id	= WINBOND_NEX_ID,
		.model_id	= WINBOND_NEX_W25Q256_V,
		.total_size	= 32768,
		.page_size	= 256,
		.feature_bits	= FEATURE_WRSR_WREN | FEATURE_OTP | FEATURE_4BA |
				  FEATURE_WRSR2,
		.probe		= PROBE_SPI_RDID,
		.write		= SPI_CHIP_WRITE256,
		.voltage	= {2700, 3600},
	},
	{
		.vendor		= "Winbond",
		.name		= "W25Q64FW",
		.bustype	= BUS_SPI,
		.manufacture_id	= WINBOND_NEX_ID,
		.model_id	= 0x6017,
		.total_size	= 8 * MiB / 1024,
		.page_size	= 256,
		.probe		= PROBE_SPI_RDID,
		.write		= SPI_CHIP_WRITE256,
		.voltage	= {1650, 1950},
	},
	{
		.vendor		= "Winbond",
		.name		= "W25Q16.V",
		.bustype	= BUS_SPI,
		.manufacture_id	= WINBOND_NEX_ID,
		.model_id	= 0x4015,
		.total_size	= 2048,
		.page_size	= 256,
		.probe		= PROBE_SPI_RDID,
		.write		= SPI_CHIP_WRITE256,
		.voltage	= {1650, 1950},
	},
	{
		.vendor		= "SST",
		.name		= "SST26VF016B(A)",
		.bustype	= BUS_SPI,
		.manufacture_id	= SST_ID,
		.model_id	= 0x2641,
		.total_size	= 2048,
		.page_size	= 256,
		.probe		= PROBE_SPI_RDID,
		.unlock		= SPI_DISABLE_BLOCKPROTECT_SST26_GLOBAL_UNPROTECT,
		.write		= SPI_CHIP_WRITE256,
		.voltage	= {2700, 3600},
	},
	{
		.vendor		= "Intel",
		.name		= "28F001",
		.bustype	= BUS_PARALLEL,
		.manufacture_id	= 0x89,
		.model_id	= 0x94,
		.total_size	= 128,
		.probe		= PROBE_JEDEC,
		.write		= WRITE_JEDEC1,
	},
"""
FR = {"include/flashchips.h": FR_H, "flashchips.c": b'#include "flashchips/test.c"', "flashchips/test.c": FR_C}


class ParserTests(unittest.TestCase):
    def test_imsprog(self):
        r = U.parse_imsprog(IMS)
        names = {c["name"]: c for c in r["spi"]}
        self.assertEqual(set(names), {"W25Q64JV", "XT25F128F", "MX25U6435F"})
        self.assertEqual(names["MX25U6435F"]["volt"], "1.8")
        self.assertIn("sr2", names["W25Q64JV"]["flags"])
        e = {c["name"]: c for c in r["i2c"]}
        self.assertEqual((e["24C02"]["addr"], e["24C512"]["addr"], e["24C512"]["page"]), (1, 2, 128))

    def test_asprogrammer(self):
        r = U.parse_asprogrammer(ASP)
        names = {c["name"]: c for c in r["spi"]}
        self.assertEqual(set(names), {"W25Q64JV-IQ-JQ", "SST25VF016B", "MX25U3235F_1.8V"})
        self.assertIn("byte_program", names["SST25VF016B"]["flags"])
        self.assertEqual(names["MX25U3235F_1.8V"]["volt"], "1.8")
        self.assertEqual(r["i2c"][0]["addr"], 2)

    def test_flashrom(self):
        r = U.parse_flashrom(FR)
        names = {c["name"]: c for c in r["spi"]}
        self.assertIn("W25Q256JV_Q", names)
        self.assertEqual(names["W25Q256JV_Q"]["id"], "EF4019")
        self.assertIn("sr2", names["W25Q256JV_Q"]["flags"])
        self.assertNotIn("en4b", names["W25Q256JV_Q"]["flags"])
        self.assertEqual(names["W25Q16.V"]["volt"], "1.8")
        self.assertIn("unlock98", names["SST26VF016B(A)"]["flags"])
        self.assertNotIn("28F001", names)            # paralela: fora

    def test_merge_priority_and_voltage(self):
        m = U.merge({"flashrom": U.parse_flashrom(FR), "imsprog": U.parse_imsprog(IMS),
                     "asprogrammer": U.parse_asprogrammer(ASP)})
        names = {c["name"]: c for c in m["spi"]}
        # nome só do AsProgrammer (sem tensão) com ID que outra fonte diz ser 1,8 V
        self.assertEqual(names["W25Q64JV-IQ-JQ"]["volt"], "3.3")
        self.assertTrue(all(c["volt"] in ("3.3", "1.8", "1.8-3.3") for c in m["spi"]))


class UpdateFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cfg = chipdb.config_dir
        chipdb.config_dir = lambda: self.tmp
        U.config_dir = chipdb.config_dir
        self._fetch = U._fetch
        self.served = {
            U.FLASHROM_RAW + "/include/flashchips.h": FR_H,
            U.FLASHROM_RAW + "/flashchips.c": FR["flashchips.c"],
            U.FLASHROM_RAW + "/flashchips/test.c": FR_C,
            U.SOURCES["imsprog"]["urls"][0]: IMS,
            U.SOURCES["asprogrammer"]["urls"][0]: ASP,
        }
        self.calls = []

        def fetch(url):
            self.calls.append(url)
            if url not in self.served:
                raise OSError("404")
            return self.served[url]
        U._fetch = fetch
        U.download_flashrom.__globals__["_fetch"] = fetch
        chipdb.reload()

    def tearDown(self):
        U._fetch = self._fetch
        chipdb.config_dir = self._cfg
        U.config_dir = self._cfg
        chipdb.reload()
        shutil.rmtree(self.tmp)

    def test_full_cycle(self):
        n0 = len(chipdb.spi_chips())
        self.assertTrue(U.due())
        rep = U.update()
        self.assertFalse(rep.failed)
        self.assertGreater(len(rep.new_spi), 5)
        self.assertFalse(U.due())
        chipdb.reload()
        self.assertGreater(len(chipdb.spi_chips()), n0)
        # chip novo detectável; o embutido continua tendo prioridade
        self.assertEqual(chipdb.find_spi_by_id(0x0B4018)[0].name, "XT25F128F")
        self.assertEqual(chipdb.find_spi_by_id(0xEF4017)[0].origin, "embutido")
        # sem mudanças
        rep2 = U.update()
        self.assertEqual(len(rep2.new_spi), 0)
        self.assertEqual(sorted(rep2.unchanged), ["asprogrammer", "flashrom", "imsprog"])
        # uma fonte ganha chip novo
        self.served[U.SOURCES["imsprog"]["urls"][0]] = IMS + ims_record(
            "SPI_FLASH", "ZBIT", "ZB25VQ256", 0x5E4019, 32 << 20)
        rep3 = U.update()
        self.assertEqual([c["name"] for c in rep3.new_spi], ["ZB25VQ256"])
        # fonte fora do ar: mantém o cache e avisa
        self.served.clear()
        rep4 = U.update()
        self.assertEqual(len(rep4.failed), 3)
        chipdb.reload()
        self.assertTrue(chipdb.find_spi_by_id(0x5E4019))

    def test_user_file_wins(self):
        with open(os.path.join(self.tmp, "spi_flash_user.json"), "w") as f:
            f.write('{"chips": [{"name": "XT25F128F", "vendor": "Meu", "id": "0B4018", "size": 16384}]}')
        U.update()
        chipdb.reload()
        c = chipdb.find_spi_by_id(0x0B4018)[0]
        self.assertEqual((c.vendor, c.origin), ("Meu", "usuário"))


if __name__ == "__main__":
    unittest.main()
