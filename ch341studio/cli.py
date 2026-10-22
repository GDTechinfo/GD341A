"""Linha de comando do CH341 Studio.

Exemplos:
    python -m ch341studio detect
    python -m ch341studio read backup.bin
    python -m ch341studio write bios.bin --unprotect
    python -m ch341studio verify bios.bin
    python -m ch341studio erase
    python -m ch341studio --i2c 24C02 read eeprom.bin
    python -m ch341studio gui            (interface gráfica)
"""

import argparse
import sys
import time

from . import APP_NAME, AUTHOR, __version__, chipdb, fileio
from .backends import BACKENDS, create_backend
from .eeprom24 import Eeprom24
from .errors import CH341Error
from .spiflash import SpiFlash


class Bar:
    def __init__(self):
        self.t0 = time.monotonic()
        self.stage = None

    def __call__(self, done, total, stage):
        if stage != self.stage:
            if self.stage:
                sys.stderr.write("\n")
            self.stage = stage
            self.t0 = time.monotonic()
        pct = done * 100 // max(total, 1)
        el = max(time.monotonic() - self.t0, 1e-6)
        speed = f"{done / el / 1024:7.1f} KiB/s" if el > 0.25 else " " * 13
        filled = pct // 4
        sys.stderr.write(f"\r{stage:<24} [{'#' * filled}{'.' * (25 - filled)}] {pct:3d}%  {speed}")
        sys.stderr.flush()

    def end(self):
        if self.stage:
            sys.stderr.write("\n")
            self.stage = None


def build_parser():
    p = argparse.ArgumentParser(prog="ch341studio",
                                description=f"{APP_NAME} {__version__} — criado por {AUTHOR}")
    p.add_argument("-b", "--backend", default="auto", choices=list(BACKENDS),
                   help="driver: auto, libusb, dll (CH341DLL oficial) ou sim (simulador)")
    p.add_argument("-c", "--chip", help="modelo SPI (ex.: W25Q64). Padrão: detectar pelo JEDEC ID")
    p.add_argument("--i2c", metavar="MODELO", help="usar EEPROM I2C (ex.: 24C02) em vez de SPI")
    p.add_argument("--i2c-speed", type=int, default=100, choices=[20, 100, 400, 750])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gui", help="abre a interface gráfica")
    sub.add_parser("detect", help="detecta a memória SPI")
    sub.add_parser("chips", help="lista o banco de chips")
    u = sub.add_parser("update-chips", help="busca chips novos (flashrom, IMSProg, AsProgrammer)")
    u.add_argument("--force", action="store_true", help="reconverte mesmo sem mudanças")
    r = sub.add_parser("read", help="lê a memória para um arquivo")
    r.add_argument("file")
    w = sub.add_parser("write", help="grava um arquivo na memória")
    w.add_argument("file")
    w.add_argument("--full", action="store_true", help="apaga tudo e grava tudo (sem modo inteligente)")
    w.add_argument("--no-verify", action="store_true")
    w.add_argument("--unprotect", action="store_true", help="remove a proteção automaticamente")
    w.add_argument("--turbo", action="store_true", help="gravação turbo (só libusb; conferida)")
    v = sub.add_parser("verify", help="compara a memória com um arquivo")
    v.add_argument("file")
    e = sub.add_parser("erase", help="apaga a memória inteira")
    e.add_argument("--unprotect", action="store_true")
    sub.add_parser("blank", help="teste de branco (tudo 0xFF?)")
    a = sub.add_parser("analyze", help="analisa um arquivo de dump (saúde, conteúdo, regiões Intel)")
    a.add_argument("file")
    sub.add_parser("clip-test", help="testa a estabilidade do contato do clipe")
    bm = sub.add_parser("benchmark", help="mede a velocidade de leitura (e gravação com --write)")
    bm.add_argument("--write", action="store_true",
                    help="inclui gravação numa área de 64 KiB no fim do chip (restaurada no final)")
    sub.add_parser("unprotect", help="remove os bits de proteção")
    sub.add_parser("status", help="mostra registradores de status")
    return p


def _spi(args, backend, bar):
    flash = SpiFlash(backend, progress=bar, log=lambda m: print(m))
    if args.chip:
        chip = chipdb.find_spi_by_name(args.chip)
        if not chip:
            raise CH341Error(f"Chip '{args.chip}' não está no banco de dados")
        flash.chip = chip
    else:
        jedec, cands = flash.detect()
        if not cands:
            raise CH341Error(f"ID {jedec:06X} desconhecido. Informe o modelo com --chip.")
        flash.chip = cands[0]
        extra = f" (também: {', '.join(c.name for c in cands[1:])})" if len(cands) > 1 else ""
        print(f"Detectado: {flash.chip.label} — ID {jedec:06X}{extra}")
    if flash.chip.is_18v:
        print("ATENÇÃO: memória de 1,8 V! Use o adaptador de 1,8 V para não danificá-la.")
    return flash


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "gui":
        from .gui.app import run
        return run()
    if args.cmd == "update-chips":
        from . import chipupdate
        rep = chipupdate.update(log=print, force=args.force)
        print(rep.summary())
        new = rep.new_spi + rep.new_i2c
        if new and not rep.first_time:
            print("Novos: " + ", ".join(c["name"] for c in new[:60]) + (" …" if len(new) > 60 else ""))
        return 0 if rep.ok_sources else 1
    if args.cmd == "analyze":
        from . import health, identify, intel
        data = fileio.load_file(args.file)
        idt = identify.identify(data)
        print(f"Arquivo: {args.file} ({len(data)} bytes, CRC32 {fileio.crc32(data):08X})")
        print(f"Conteúdo: {idt.summary()}")
        for st in idt.strings:
            print(f"  texto: {st}")
        d = intel.parse_descriptor(data)
        if d:
            for r in sorted(d.regions, key=lambda r: r.base):
                print(f"  região {r.label()}")
        for f in health.analyze_dump(data).findings:
            print(f"[{f.level}] {f.text}")
        return 0
    if args.cmd == "chips":
        for c in chipdb.spi_chips():
            print(f"{c.jedec:06X}  {c.name:<22} {c.vendor:<18} {chipdb.human_size(c.size):<22} "
                  f"{c.voltage:<7} V  [{c.origin}]")
        for c in chipdb.eeprom_chips():
            print(f"I2C     {c.name:<22} {'':<18} {chipdb.human_size(c.size):<22} pág {c.page}")
        return 0

    bar = Bar()
    try:
        backend = create_backend(args.backend)
    except CH341Error as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 2
    print(f"Gravador: {backend.describe()}")
    try:
        if args.i2c:
            chip = chipdb.find_eeprom(args.i2c)
            if not chip:
                raise CH341Error(f"EEPROM '{args.i2c}' não reconhecida")
            if args.cmd in ("detect", "unprotect", "status", "clip-test", "benchmark"):
                raise CH341Error(f"O comando '{args.cmd}' só existe para memórias SPI.")
            backend.set_i2c_speed(args.i2c_speed)
            dev = Eeprom24(backend, chip, progress=bar)
        else:
            dev = _spi(args, backend, bar)

        if args.cmd == "detect":
            pass
        elif args.cmd == "clip-test":
            from . import health
            res = health.clip_test(dev, progress=bar)
            bar.end()
            print(res.summary())
            return 0 if res.stable else 1
        elif args.cmd == "benchmark":
            from . import benchmark
            res = benchmark.run(dev, include_write=args.write, log=print)
            bar.end()
            print(res.summary())
        elif args.cmd == "read":
            data = dev.read()
            bar.end()
            fileio.save_file(args.file, data)
            print(f"Salvo {args.file}: {len(data)} bytes, CRC32 {fileio.crc32(data):08X}")
        elif args.cmd == "write":
            data = fileio.load_file(args.file)
            if args.i2c:
                vr = dev.write(data, verify=not args.no_verify)
            else:
                dev.turbo = args.turbo
                stats, vr = dev.write(data, smart=not args.full, verify=not args.no_verify,
                                      auto_unprotect=args.unprotect)
                bar.end()
                print(f"Setores apagados: {stats.erased_sectors}, blocos: {stats.erased_blocks}, "
                      f"páginas gravadas: {stats.programmed_pages}")
            bar.end()
            if vr:
                print(vr.summary())
                return 0 if vr.ok else 1
        elif args.cmd == "verify":
            vr = dev.verify(fileio.load_file(args.file))
            bar.end()
            print(vr.summary())
            return 0 if vr.ok else 1
        elif args.cmd == "erase":
            if args.i2c:
                dev.erase()
            else:
                dev.erase_chip(auto_unprotect=args.unprotect)
            bar.end()
            print("Memória apagada.")
        elif args.cmd == "blank":
            ok, first = dev.blank_check()
            bar.end()
            print("Memória em branco." if ok else f"Não está em branco (primeiro byte em 0x{first:X}).")
            return 0 if ok else 1
        elif args.cmd == "unprotect":
            dev.unprotect()
        elif args.cmd == "status":
            info = dev.protection_info()
            sr2 = f"  SR2=0x{info['sr2']:02X}" if info["sr2"] is not None else ""
            print(f"SR1=0x{info['sr1']:02X}{sr2}  protegido: {'sim' if info['protected'] else 'não'}")
        return 0
    except CH341Error as e:
        bar.end()
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        bar.end()
        print("Interrompido.", file=sys.stderr)
        return 130
    finally:
        backend.close()


if __name__ == "__main__":
    sys.exit(main())
