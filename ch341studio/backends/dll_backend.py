"""Driver via CH341DLL.DLL oficial da WCH (somente Windows).

Usa o driver original do fabricante (CH341PAR), então convive com outros
programas como NeoProgrammer/AsProgrammer sem trocar driver.

A DLL não acompanha o projeto: baixe o pacote CH341PAR da WCH e coloque
CH341DLLA64.DLL (Python 64 bits) ou CH341DLL.DLL (32 bits) na pasta do
programa, na subpasta "drivers" ou no PATH do sistema.
"""

import ctypes
import os
import struct
import sys

from ..errors import BackendUnavailable, CH341Error, DeviceNotFound
from .base import Backend

IS_64 = struct.calcsize("P") == 8
DLL_NAMES = ["CH341DLLA64.DLL"] if IS_64 else ["CH341DLL.DLL"]

# CH341SetStream: bits 1..0 = velocidade I2C, bit 2 = SPI duplo (não usado),
# bit 7 = ordem dos bits no SPI (1 = MSB primeiro, o que as memórias usam)
STREAM_MSB_FIRST = 0x80
I2C_SPEED_BITS = {20: 0, 100: 1, 400: 2, 750: 3}
CS_D0 = 0x80  # bit 7 = controlar CS, bits 1..0 = 00 -> pino D0
# mMAX_BUFFER_LENGTH da CH341DLL: nenhuma chamada de stream pode passar de 4096 bytes
DLL_MAX_BUFFER = 4096


def _search_dirs():
    base = os.path.dirname(os.path.abspath(sys.argv[0] or "."))
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    dirs = [base, os.path.join(base, "drivers"), root, os.path.join(root, "drivers"), os.getcwd()]
    if getattr(sys, "_MEIPASS", None):   # executável do PyInstaller
        dirs.insert(0, sys._MEIPASS)
    return dirs


def load_dll():
    if sys.platform != "win32":
        raise BackendUnavailable("O modo CH341DLL só existe no Windows. Use o modo libusb.")
    tried = []
    for d in _search_dirs():
        for name in DLL_NAMES:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                try:
                    return ctypes.WinDLL(path), path
                except OSError as e:
                    tried.append(f"{path}: {e}")
    for name in DLL_NAMES:   # PATH / System32
        try:
            return ctypes.WinDLL(name), name
        except OSError:
            tried.append(name)
    raise BackendUnavailable(
        f"{DLL_NAMES[0]} não encontrada. Instale o driver CH341PAR da WCH e copie a DLL "
        "para a pasta do programa (ou para a subpasta 'drivers').")


class DllBackend(Backend):
    name = "CH341DLL"
    # 4096 do buffer da DLL menos o cabeçalho (opcode + até 4 bytes de endereço + dummy)
    max_spi_read = 2048
    max_i2c_write = 64
    max_i2c_read = 256

    def __init__(self, index: int = 0):
        super().__init__()
        self.index = index
        self._dll = None
        self.dll_path = ""

    @classmethod
    def available(cls):
        if sys.platform != "win32":
            return False, "somente Windows"
        try:
            load_dll()
        except BackendUnavailable as e:
            return False, str(e)
        return True, ""

    def _bind(self, dll):
        u = ctypes.c_ulong
        dll.CH341OpenDevice.argtypes = [u]
        dll.CH341OpenDevice.restype = ctypes.c_void_p
        dll.CH341CloseDevice.argtypes = [u]
        dll.CH341CloseDevice.restype = None
        dll.CH341SetStream.argtypes = [u, u]
        dll.CH341SetStream.restype = ctypes.c_bool
        dll.CH341StreamSPI4.argtypes = [u, u, u, ctypes.c_void_p]
        dll.CH341StreamSPI4.restype = ctypes.c_bool
        dll.CH341StreamI2C.argtypes = [u, u, ctypes.c_void_p, u, ctypes.c_void_p]
        dll.CH341StreamI2C.restype = ctypes.c_bool
        dll.CH341GetVersion.restype = u
        dll.CH341GetDrvVersion.restype = u

    def open(self):
        dll, self.dll_path = load_dll()
        self._bind(dll)
        handle = dll.CH341OpenDevice(self.index)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            raise DeviceNotFound(
                "CH341A não encontrado pelo driver oficial. Confira se o driver CH341PAR está "
                "instalado (e não o WinUSB/Zadig — nesse caso use o modo libusb).")
        self._dll = dll
        self._apply_stream()

    def close(self):
        if self._dll is not None:
            self._dll.CH341CloseDevice(self.index)
            self._dll = None

    def describe(self):
        try:
            ver = self._dll.CH341GetDrvVersion() if self._dll else 0
            return f"CH341DLL (driver WCH v{ver})"
        except Exception:
            return "CH341DLL (driver WCH)"

    def _apply_stream(self):
        mode = STREAM_MSB_FIRST | I2C_SPEED_BITS[self.i2c_khz]
        if not self._dll.CH341SetStream(self.index, mode):
            raise CH341Error("CH341SetStream falhou")

    def set_i2c_speed(self, khz: int):
        super().set_i2c_speed(khz)
        if self._dll is not None:
            self._apply_stream()

    def spi_xfer(self, write: bytes, read_len: int = 0) -> bytes:
        data = bytes(write) + b"\xFF" * read_len
        if len(data) > DLL_MAX_BUFFER:
            raise CH341Error(f"Transferência SPI de {len(data)} bytes excede o limite da "
                             f"CH341DLL ({DLL_MAX_BUFFER})")
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        if not self._dll.CH341StreamSPI4(self.index, CS_D0, len(data), buf):
            raise CH341Error(f"CH341StreamSPI4 falhou ({len(data)} bytes). "
                             "Confira o cabo USB e o encaixe do gravador.")
        return bytes(buf)[len(write):]

    def i2c_write(self, dev7: int, payload: bytes):
        data = bytes([dev7 << 1]) + bytes(payload)
        wbuf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        if not self._dll.CH341StreamI2C(self.index, len(data), wbuf, 0, None):
            raise CH341Error("CH341StreamI2C (escrita) falhou")

    def i2c_read(self, dev7: int, prefix: bytes, n: int) -> bytes:
        out = bytearray()
        first = True
        while len(out) < n:
            k = min(self.max_i2c_read, n - len(out))
            data = bytes([dev7 << 1]) + (bytes(prefix) if first else b"")
            wbuf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            rbuf = (ctypes.c_ubyte * k)()
            # sem prefixo a DLL ainda envia o endereço; como o EEPROM ignora
            # uma escrita sem dados de endereço, a leitura segue do ponteiro atual
            if not self._dll.CH341StreamI2C(self.index, len(data), wbuf, k, rbuf):
                raise CH341Error("CH341StreamI2C (leitura) falhou")
            out += bytes(rbuf)
            first = False
        return bytes(out)
