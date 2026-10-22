"""Atualização automática do banco de chips a partir de bases públicas.

Fontes (baixadas na hora, no computador do usuário — nada disso é distribuído
junto com o programa):

* flashrom  — github.com/flashrom/flashrom  (flashchips/*.c, a base mais revisada)
* IMSProg   — github.com/bigbigmdm/IMSProg  (IMSProg.Dat, feita para o CH341A)
* AsProgrammer — github.com/nofeletru/UsbAsp-flash (chiplist.xml)

O resultado vai para <pasta de configuração>/chips_auto.json. Prioridade na
hora de usar: arquivo do usuário > banco embutido (curado) > banco automático.
Quando fontes diferentes discordam sobre um chip, vale a mais confiável
(flashrom > IMSProg > AsProgrammer); tensão de 1,8 V vence sempre (por segurança).
"""

import hashlib
import json
import os
import re
import ssl
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .chipdb import config_dir

AUTO_FILE = "chips_auto.json"
STATE_FILE = "chips_update_state.json"
CHECK_INTERVAL_H = 24
TIMEOUT = 25
UA = "CH341Studio-chip-updater (+https://youtube.com/GDTechinformatica)"

GH_RAW = "https://raw.githubusercontent.com"
FLASHROM_RAW = f"{GH_RAW}/flashrom/flashrom/main"
SOURCES = {
    "flashrom": {"label": "flashrom", "rank": 3},
    "imsprog": {
        "label": "IMSProg", "rank": 2,
        "urls": [f"{GH_RAW}/bigbigmdm/IMSProg/main/IMSProg_programmer/database/IMSProg.Dat",
                 "https://antenna-dvb-t2.ru/dl_all/IMSProg.Dat"],
    },
    "asprogrammer": {
        "label": "AsProgrammer", "rank": 1,
        "urls": [f"{GH_RAW}/nofeletru/UsbAsp-flash/master/chiplist.xml"],
    },
}

# fabricantes que costumam ter SR2 com QE (WRSR de 1 byte pode zerar o QE)
SR2_VENDORS = {0xEF, 0xC8, 0x68, 0x5E, 0xA1, 0x85, 0x0B, 0x1F}
MAX_SPI = 256 * 1024 * 1024


# ============================================================ download
# Arquivos de certificados (CA) usados pelas distros Linux mais comuns. O Python embutido
# no AppImage/executável procura só no caminho da máquina onde foi gerado, que muda de
# distro para distro — por isso carregamos também estes e o pacote certifi embutido.
SYSTEM_CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",                 # Debian, Ubuntu, Mint, Arch, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",                   # Fedora, RHEL, CentOS
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora/RHEL (novo)
    "/etc/ssl/ca-bundle.pem",                             # openSUSE
    "/etc/ssl/cert.pem",                                  # Alpine, macOS, BSD
)
_SSL_CTX = None


def ssl_context() -> ssl.SSLContext:
    """Contexto TLS com verificação de certificado SEMPRE ligada e CAs de várias fontes."""
    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    ctx = ssl.create_default_context()          # certificados padrão do sistema
    try:
        import certifi                          # certificados embutidos no programa
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    for path in SYSTEM_CA_FILES:
        if os.path.isfile(path):
            try:
                ctx.load_verify_locations(cafile=path)
            except (ssl.SSLError, OSError):
                pass
    _SSL_CTX = ctx
    return ctx


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl_context()) as r:
        return r.read()


def _fetch_first(urls) -> tuple[bytes, str]:
    errors = []
    for u in urls:
        try:
            return _fetch(u), u
        except Exception as e:
            errors.append(f"{u}: {e}")
    raise OSError("; ".join(errors))


def download_flashrom() -> dict[str, bytes]:
    """Baixa include/flashchips.h + flashchips.c + cada flashchips/<fabricante>.c."""
    files = {"include/flashchips.h": _fetch(f"{FLASHROM_RAW}/include/flashchips.h")}
    main = _fetch(f"{FLASHROM_RAW}/flashchips.c")
    files["flashchips.c"] = main
    for inc in re.findall(rb'#include\s+"(flashchips/[\w.-]+\.c)"', main):
        name = inc.decode()
        files[name] = _fetch(f"{FLASHROM_RAW}/{name}")
    if not any(k.startswith("flashchips/") for k in files):
        raise OSError("estrutura do flashrom mudou (nenhum arquivo de chips encontrado)")
    return files


# ============================================================ normalização
def _norm_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _spi(name, vendor, jedec, size, source, volt="3.3", page=256, flags=()):
    return {"name": name.strip(), "vendor": vendor.strip() or "?", "id": f"{jedec:06X}",
            "size": size // 1024, "volt": volt, "page": page,
            "flags": sorted(set(flags)), "source": source}


def _valid_spi(jedec: int, size: int) -> bool:
    mfr = jedec >> 16
    return (0 < jedec < 0xFFFFFF and mfr not in (0x00, 0xFF) and (jedec & 0xFFFF) not in (0, 0xFFFF)
            and 4 * 1024 <= size <= MAX_SPI and size % 1024 == 0)


def _guess_flags(jedec: int, name: str, flags: set) -> set:
    if (jedec >> 16) in SR2_VENDORS and "Q" in name.upper() and "sr2" not in flags:
        flags = set(flags) | {"sr2"}
    return flags


# ============================================================ parsers
def parse_imsprog(data: bytes) -> dict:
    """IMSProg.Dat: registros de 0x44 bytes ("TIPO,FABRICANTE,NOME\\0" + campos binários)."""
    spi, i2c = [], []
    vcc = {0: "3.3", 1: "1.8", 2: "5.0", 3: "2.5"}
    for i in range(len(data) // 0x44):
        r = data[i * 0x44:(i + 1) * 0x44]
        txt = r[:0x30].split(b"\0")[0].decode("latin-1", "replace")
        parts = txt.split(",", 2)
        if len(parts) < 3:
            continue
        typ, vendor, name = parts
        size = int.from_bytes(r[0x34:0x38], "little")
        block = int.from_bytes(r[0x38:0x3A], "little")
        if typ == "SPI_FLASH" and r[0x3A] == 0:
            jedec = (r[0x32] << 16) | (r[0x31] << 8) | r[0x30]
            if not _valid_spi(jedec, size):
                continue
            volt = vcc.get(r[0x43], "3.3")
            page = block if block in (1, 64, 128, 256, 512) else 256
            flags = set()
            if page == 1:
                flags |= {"byte_program", "ewsr"}
                page = 256
            spi.append(_spi(name, vendor.title(), jedec, size, "imsprog", volt, page,
                            _guess_flags(jedec, name, flags)))
        elif typ == "24_EEPROM" and r[0x3A] == 1 and 16 <= size <= 256 * 1024:
            algo = r[0x3B]
            addr = 2 if (algo & 0x0F) == 2 else 1
            page = block if block in (1, 2, 4, 8, 16, 32, 64, 128, 256) else 8
            i2c.append({"name": name.strip(), "size": size, "page": page, "addr": addr,
                        "source": "imsprog"})
    return {"spi": spi, "i2c": i2c}


def parse_asprogrammer(data: bytes) -> dict:
    """chiplist.xml do AsProgrammer: <SPI><FABRICANTE><CHIP id= page= size= spicmd=/>."""
    root = ET.fromstring(data)
    spi, i2c = [], []
    sec = root.find("SPI")
    for vendor in (list(sec) if sec is not None else []):
        for chip in vendor:
            a = chip.attrib
            if a.get("spicmd", "25") != "25":
                continue
            idh = a.get("id", "")
            try:
                jedec = int(idh, 16)
                size = int(a.get("size", "0"))
            except ValueError:
                continue
            if len(idh) != 6 or not _valid_spi(jedec, size):
                continue
            page_s = a.get("page", "256").upper()
            flags = set()
            if page_s in ("SSTB", "SSTW"):
                flags |= {"byte_program", "ewsr"}
                page = 256
            else:
                try:
                    page = int(page_s)
                except ValueError:
                    page = 256
            volt = "1.8" if re.search(r"1[._]?8V|1V8", chip.tag.upper()) else "?"
            spi.append(_spi(chip.tag, vendor.tag.title(), jedec, size, "asprogrammer", volt,
                            page, _guess_flags(jedec, chip.tag, flags)))
    sec = root.find("I2C")
    for vendor in (list(sec) if sec is not None else []):
        for chip in vendor:
            a = chip.attrib
            try:
                size = int(a.get("size", "0"))
                page = int(a.get("page", "8"))
            except ValueError:
                continue
            if not 16 <= size <= 256 * 1024 or not chip.tag.upper().startswith(("24", "AT24", "M24")):
                continue
            i2c.append({"name": chip.tag, "size": size, "page": page,
                        "addr": 1 if size <= 2048 else 2, "source": "asprogrammer"})
    return {"spi": spi, "i2c": i2c}


_SIZE_UNITS = {"KiB": 1, "MiB": 1024}


def _c_int(expr: str, macros: dict) -> int | None:
    expr = re.sub(r"/\*.*?\*/", "", expr).strip().rstrip(",").strip()
    m = re.fullmatch(r"(\d+)\s*\*\s*(KiB|MiB)", expr)
    if m:
        return int(m.group(1)) * _SIZE_UNITS[m.group(2)]
    if re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", expr):
        return int(expr, 0)
    if expr in macros:
        return macros[expr]
    return None


def parse_flashrom(files: dict[str, bytes]) -> dict:
    """Lê as structs `flashchip` dos arquivos flashchips/*.c."""
    header = files.get("include/flashchips.h", b"").decode("utf-8", "replace")
    macros = {}
    for name, val in re.findall(r"#define\s+(\w+)\s+(0[xX][0-9a-fA-F]+|\d+)\b", header):
        macros[name] = int(val, 0)
    spi = []
    for fname, raw in files.items():
        if not fname.startswith("flashchips/"):
            continue
        text = raw.decode("utf-8", "replace")
        # cada chip começa em ".vendor" e vai até o próximo ".vendor"
        starts = [m.start() for m in re.finditer(r"\.vendor\s*=", text)]
        for i, st in enumerate(starts):
            body = text[st:starts[i + 1] if i + 1 < len(starts) else len(text)]

            def field(key):
                m = re.search(rf"\.{key}\s*=\s*([^\n]+)", body)
                return m.group(1).strip() if m else ""

            if "BUS_SPI" not in field("bustype") or "PROBE_SPI_RDID" not in field("probe"):
                continue
            write = field("write")
            if not any(w in write for w in ("SPI_CHIP_WRITE256", "SPI_CHIP_WRITE1", "SPI_WRITE_AAI")):
                continue
            vendor = re.search(r'"([^"]*)"', field("vendor"))
            name = re.search(r'"([^"]*)"', field("name"))
            mfr = _c_int(field("manufacture_id"), macros)
            model = _c_int(field("model_id"), macros)
            size_k = _c_int(field("total_size"), macros)
            if not (vendor and name and mfr is not None and model is not None and size_k):
                continue
            if mfr > 0xFF or model > 0xFFFF or name.group(1).lower().startswith(("unknown", "generic")):
                continue
            jedec = (mfr << 16) | model
            size = size_k * 1024
            if not _valid_spi(jedec, size):
                continue
            feats = field("feature_bits")
            # feature_bits pode continuar na linha seguinte
            fm = re.search(r"\.feature_bits\s*=\s*([^;]*?),\s*\n\s*\.", body, re.S)
            if fm:
                feats = fm.group(1)
            unlock = field("unlock")
            flags = set()
            if "SPI_CHIP_WRITE1" in write or "SPI_WRITE_AAI" in write:
                flags.add("byte_program")
            if "FEATURE_WRSR_EWSR" in feats:
                flags.add("ewsr")
            if "FEATURE_WRSR_EXT2" in feats or "FEATURE_WRSR2" in feats:
                flags.add("sr2")
            if "SST26_GLOBAL_UNPROTECT" in unlock:
                flags.add("unlock98")
            if size > 16 * 1024 * 1024:
                native = any(f in feats for f in ("FEATURE_4BA_NATIVE", "FEATURE_4BA_READ")) or \
                    re.search(r"FEATURE_4BA\b", feats)
                if not native:
                    if "FEATURE_4BA_ENTER" in feats:
                        flags.add("en4b")
                    else:
                        continue  # só por registrador EAR: não suportado ainda
            vm = re.search(r"\.voltage\s*=\s*\{\s*(\d+)\s*,\s*(\d+)\s*\}", body)
            volt = "3.3"
            if vm:
                vmin, vmax = int(vm.group(1)), int(vm.group(2))
                volt = "1.8" if vmax <= 2100 else ("1.8-3.3" if vmin <= 1800 else "3.3")
            page = _c_int(field("page_size"), macros) or 256
            spi.append(_spi(name.group(1), vendor.group(1), jedec, size, "flashrom", volt,
                            page if page in (64, 128, 256, 512) else 256,
                            _guess_flags(jedec, name.group(1), flags)))
    return {"spi": spi, "i2c": []}


# ============================================================ junção
def merge(results: dict[str, dict]) -> dict:
    """Junta as fontes, sem duplicar (mesmo nome normalizado + mesmo ID)."""
    rank = {k: v["rank"] for k, v in SOURCES.items()}
    spi: dict[tuple, dict] = {}
    by_id_volt: dict[str, str] = {}
    for src in sorted(results, key=lambda s: -rank.get(s, 0)):
        for c in results[src].get("spi", []):
            key = (_norm_name(c["name"]), c["id"])
            if c["volt"] == "1.8":
                by_id_volt[c["id"]] = "1.8"
            if key not in spi:
                spi[key] = dict(c)
                spi[key]["sources"] = [src]
            else:
                spi[key]["sources"].append(src)
                if spi[key]["volt"] == "?":
                    spi[key]["volt"] = c["volt"]
                # flags somam (todas são "mais cuidadosas", nunca menos seguras)
                spi[key]["flags"] = sorted(set(spi[key]["flags"]) | set(c["flags"]))
    for c in spi.values():
        if c["volt"] == "?":
            # fonte sem informação de tensão: se outra fonte diz 1,8 V para o mesmo ID,
            # marca 1,8 V por segurança (só gera um aviso); senão assume 3,3 V
            c["volt"] = "1.8" if by_id_volt.get(c["id"]) == "1.8" else "3.3"
        c.pop("source", None)
    i2c: dict[str, dict] = {}
    for src in sorted(results, key=lambda s: -rank.get(s, 0)):
        for c in results[src].get("i2c", []):
            key = _norm_name(c["name"])
            if key not in i2c:
                c = dict(c)
                c.pop("source", None)
                i2c[key] = c
    return {"spi": sorted(spi.values(), key=lambda c: (c["vendor"].upper(), c["size"], c["name"])),
            "i2c": sorted(i2c.values(), key=lambda c: (c["size"], c["name"]))}


# ============================================================ estado/arquivos
def _path(name):
    return os.path.join(config_dir(), name)


def load_state() -> dict:
    try:
        with open(_path(STATE_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_json(name, obj):
    os.makedirs(config_dir(), exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(name))


def load_auto() -> dict:
    try:
        with open(_path(AUTO_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def due(hours: float = CHECK_INTERVAL_H) -> bool:
    last = load_state().get("last_check", 0)
    return time.time() - last > hours * 3600


class UpdateReport:
    def __init__(self):
        self.ok_sources: list[str] = []
        self.failed: dict[str, str] = {}
        self.unchanged: list[str] = []
        self.new_spi: list[dict] = []
        self.new_i2c: list[dict] = []
        self.total_spi = 0
        self.total_i2c = 0
        self.changed = False
        self.first_time = False

    def summary(self) -> str:
        parts = []
        if not self.ok_sources and not (self.total_spi or self.total_i2c):
            return ("não foi possível baixar nenhuma fonte (sem internet ou bloqueio de rede); "
                    "usando só o banco embutido")
        if self.first_time:
            parts.append(f"banco baixado: {self.total_spi} SPI e {self.total_i2c} EEPROM I²C")
            if self.failed:
                parts.append("falhou: " + ", ".join(SOURCES[k]["label"] for k in self.failed))
            return "; ".join(parts)
        if self.new_spi or self.new_i2c:
            parts.append(f"{len(self.new_spi)} SPI e {len(self.new_i2c)} EEPROM I²C novos")
        else:
            parts.append("nenhum chip novo")
        parts.append(f"banco automático com {self.total_spi} SPI e {self.total_i2c} I²C")
        if self.failed:
            parts.append("falhou: " + ", ".join(SOURCES[k]["label"] for k in self.failed))
        return "; ".join(parts)


def update(log=None, progress=None, force: bool = False) -> UpdateReport:
    """Baixa as fontes, reconverte as que mudaram e regrava chips_auto.json."""
    log = log or (lambda m: None)
    rep = UpdateReport()
    state = load_state()
    hashes = state.get("hashes", {})
    cache_dir = _path("sources")
    os.makedirs(cache_dir, exist_ok=True)
    results: dict[str, dict] = {}
    names = list(SOURCES)
    for n, key in enumerate(names):
        if progress:
            progress(n, len(names), f"Baixando {SOURCES[key]['label']}")
        cache = os.path.join(cache_dir, f"{key}.json")
        try:
            if key == "flashrom":
                files = download_flashrom()
                blob = b"".join(files[k] for k in sorted(files))
            else:
                blob, _url = _fetch_first(SOURCES[key]["urls"])
            digest = hashlib.sha256(blob).hexdigest()
            if not force and hashes.get(key) == digest and os.path.isfile(cache):
                with open(cache, encoding="utf-8") as f:
                    results[key] = json.load(f)
                rep.unchanged.append(key)
            else:
                if key == "flashrom":
                    parsed = parse_flashrom(files)
                elif key == "imsprog":
                    parsed = parse_imsprog(blob)
                else:
                    parsed = parse_asprogrammer(blob)
                if not parsed["spi"]:
                    raise ValueError("nenhum chip reconhecido (formato mudou?)")
                with open(cache, "w", encoding="utf-8") as f:
                    json.dump(parsed, f)
                hashes[key] = digest
                results[key] = parsed
                rep.changed = True
            rep.ok_sources.append(key)
            log(f"{SOURCES[key]['label']}: {len(results[key]['spi'])} SPI, "
                f"{len(results[key]['i2c'])} I²C"
                + (" (sem mudanças)" if key in rep.unchanged else " (atualizado)"))
        except Exception as e:
            rep.failed[key] = str(e)
            log(f"{SOURCES[key]['label']}: não foi possível atualizar ({e})")
            if os.path.isfile(cache):          # usa a última versão boa
                try:
                    with open(cache, encoding="utf-8") as f:
                        results[key] = json.load(f)
                except (OSError, ValueError):
                    pass

    old = load_auto()
    rep.first_time = not old.get("spi")
    if results:
        merged = merge(results)
        old_spi = {(_norm_name(c["name"]), c["id"]) for c in old.get("spi", [])}
        old_i2c = {_norm_name(c["name"]) for c in old.get("i2c", [])}
        rep.new_spi = [c for c in merged["spi"] if (_norm_name(c["name"]), c["id"]) not in old_spi]
        rep.new_i2c = [c for c in merged["i2c"] if _norm_name(c["name"]) not in old_i2c]
        rep.total_spi, rep.total_i2c = len(merged["spi"]), len(merged["i2c"])
        if rep.changed or rep.new_spi or rep.new_i2c or not old:
            merged["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            merged["sources"] = {k: SOURCES[k]["label"] for k in results}
            _save_json(AUTO_FILE, merged)
    else:
        rep.total_spi, rep.total_i2c = len(old.get("spi", [])), len(old.get("i2c", []))
    state["hashes"] = hashes
    state["last_check"] = time.time()
    _save_json(STATE_FILE, state)
    if progress:
        progress(len(names), len(names), "Banco de chips")
    return rep
