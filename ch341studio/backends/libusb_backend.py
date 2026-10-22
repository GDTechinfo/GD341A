"""Driver via libusb (pyusb). Funciona em Linux e Windows.

No Windows é preciso trocar o driver do CH341A para WinUSB (ex.: com o Zadig).
No Linux é preciso permissão de acesso ao USB (regra udev em linux/99-ch341a.rules).

Para ganhar velocidade, a escrita dos pacotes e a leitura das respostas
acontecem ao mesmo tempo (uma thread escreve, a outra lê). Assim o CH341A nunca
fica parado esperando o PC esvaziar o buffer de saída.
"""

import sys
import threading
import time

from .. import protocol as P
from ..errors import BackendUnavailable, CH341Error, DeviceNotFound
from .base import Backend

WRITE_CHUNK = P.PACKET * 32   # 1 KiB por chamada de escrita (múltiplo de 32!)


def _is_timeout(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    try:
        import usb.core
        if isinstance(exc, usb.core.USBTimeoutError):
            return True
    except ImportError:
        pass
    return getattr(exc, "errno", None) in (110, 10060) or "timeout" in str(exc).lower()


class LibusbBackend(Backend):
    name = "libusb"
    max_spi_read = 16384
    supports_turbo = True
    max_i2c_write = P.I2C_MAX_WRITE
    max_i2c_read = P.I2C_MAX_READ

    def __init__(self, device=None):
        """`device` permite injetar um dispositivo (ex.: o simulador)."""
        super().__init__()
        self._dev = device
        self._owned = device is None
        self._usb_backend = None

    @classmethod
    def available(cls):
        try:
            import usb.core  # noqa: F401
        except ImportError:
            return False, "pyusb não instalado (pip install pyusb libusb-package)"
        return True, ""

    # ------------------------------------------------------------------ abrir
    def _find_device(self):
        try:
            import usb.core
            import usb.util  # noqa: F401
        except ImportError as e:
            raise BackendUnavailable(
                "pyusb não está instalado. Rode: pip install pyusb libusb-package") from e
        try:
            import libusb_package
            self._usb_backend = libusb_package.get_libusb1_backend()
        except Exception:
            self._usb_backend = None
        try:
            dev = usb.core.find(idVendor=P.VID, idProduct=P.PID, backend=self._usb_backend)
        except usb.core.NoBackendError as e:
            raise BackendUnavailable(
                "Biblioteca libusb não encontrada. No Windows instale: pip install libusb-package; "
                "no Linux: sudo apt install libusb-1.0-0") from e
        if dev is None:
            hint = ("No Windows o driver precisa ser WinUSB (use o Zadig) para o modo libusb. "
                    "Com o driver oficial da WCH, escolha o modo CH341DLL."
                    if sys.platform == "win32" else
                    "Confira o cabo e se o gravador aparece em 'lsusb' como 1a86:5512.")
            raise DeviceNotFound(f"CH341A não encontrado via libusb. {hint}")
        return dev

    def open(self):
        if self._owned:
            dev = self._find_device()      # já avisa se pyusb/libusb faltarem
            import usb.core
            import usb.util
            try:
                if dev.is_kernel_driver_active(0):
                    dev.detach_kernel_driver(0)
            except (NotImplementedError, usb.core.USBError, AttributeError):
                pass
            try:
                dev.set_configuration()
            except NotImplementedError as e:
                # Windows: a libusb não abre dispositivos com o driver da WCH (CH341PAR)
                raise BackendUnavailable(
                    "O CH341A está com o driver oficial da WCH, que a libusb não consegue usar. "
                    "Use o modo CH341DLL, ou troque o driver para WinUSB com o Zadig "
                    "(zadig.akeo.ie) para usar o modo libusb.") from e
            except usb.core.USBError as e:
                if getattr(e, "errno", None) == 13:
                    raise CH341Error(
                        "Sem permissão para acessar o USB. No Linux instale a regra udev "
                        "(linux/99-ch341a.rules) ou rode como root.") from e
                # em alguns sistemas o dispositivo já está configurado
            try:
                usb.util.claim_interface(dev, 0)
            except NotImplementedError as e:
                raise BackendUnavailable(
                    "A libusb não consegue usar o driver atual do CH341A. Use o modo CH341DLL "
                    "ou instale o WinUSB com o Zadig.") from e
            except usb.core.USBError as e:
                raise CH341Error(f"Não foi possível usar o CH341A (outro programa usando?): {e}") from e
            self._dev = dev
        self._write(P.pins_enable())
        self.set_i2c_speed(self.i2c_khz)

    def close(self):
        if self._dev is None:
            return
        try:
            self._write(P.pins_disable())
        except Exception:
            pass
        if self._owned:
            try:
                import usb.util
                usb.util.release_interface(self._dev, 0)
                usb.util.dispose_resources(self._dev)
            except Exception:
                pass
            self._dev = None

    def describe(self):
        return "libusb (WinUSB/libusb-1.0)"

    # ---------------------------------------------------------- baixo nível
    def _write(self, data: bytes, timeout_ms: int = 2000):
        try:
            n = self._dev.write(P.EP_OUT, data, timeout_ms)
        except Exception as e:
            raise CH341Error(f"Falha ao escrever no USB: {e}") from e
        if n != len(data):
            raise CH341Error(f"Escrita USB incompleta ({n}/{len(data)} bytes)")

    def _read_exact(self, n: int, timeout_s: float) -> bytes:
        buf = bytearray()
        deadline = time.monotonic() + timeout_s
        while len(buf) < n:
            want = min(n - len(buf), 4096)
            try:
                chunk = self._dev.read(P.EP_IN, want, 500)
            except Exception as e:
                if _is_timeout(e) and time.monotonic() < deadline:
                    continue
                if _is_timeout(e):
                    raise CH341Error(
                        f"Tempo esgotado esperando resposta do CH341A ({len(buf)}/{n} bytes)") from e
                raise CH341Error(f"Falha ao ler do USB: {e}") from e
            buf += bytes(chunk)
        return bytes(buf[:n])

    def _stream(self, blob: bytes, expect: int) -> bytes:
        """Escreve `blob` e lê `expect` bytes de resposta simultaneamente."""
        if expect == 0:
            for i in range(0, len(blob), WRITE_CHUNK):
                self._write(blob[i:i + WRITE_CHUNK])
            return b""
        err: list[Exception] = []

        def writer():
            try:
                for i in range(0, len(blob), WRITE_CHUNK):
                    self._write(blob[i:i + WRITE_CHUNK])
            except Exception as e:  # repassa para a thread principal
                err.append(e)

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            data = self._read_exact(expect, timeout_s=5 + expect / 20000)
        except CH341Error:
            t.join(3)
            if err:
                raise err[0]
            raise
        t.join(10)
        if err:
            raise err[0]
        return data

    # --------------------------------------------------------------- SPI
    def spi_xfer(self, write: bytes, read_len: int = 0) -> bytes:
        blob, expect = P.spi_transaction(write, read_len)
        try:
            raw = self._stream(blob, expect)
        finally:
            self._write(P.cs_release())
        return P.rev(raw[len(write):])

    def spi_program_pages(self, pages, op: int, addr_len: int, delay_us: int):
        """Grava várias páginas em fila (sem consultar o status). Quem chama confere depois."""
        writes, expect = P.turbo_program_writes(pages, op, addr_len, delay_us)
        err: list[Exception] = []

        def writer():
            try:
                for w in writes:
                    self._write(w, timeout_ms=5000)
            except Exception as e:
                err.append(e)

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            self._read_exact(expect, timeout_s=5 + len(pages) * 0.02)
        finally:
            t.join(10)
        if err:
            raise err[0]

    # --------------------------------------------------------------- I2C
    def set_i2c_speed(self, khz: int):
        super().set_i2c_speed(khz)
        if self._dev is not None:
            self._write(P.i2c_set_speed(khz))

    def i2c_write(self, dev7: int, payload: bytes):
        self._write(P.i2c_write_packet(dev7, payload))

    def i2c_read(self, dev7: int, prefix: bytes, n: int) -> bytes:
        out = bytearray()
        first = True
        while len(out) < n:
            k = min(P.I2C_MAX_READ, n - len(out))
            # depois do primeiro pacote, continua lendo do endereço atual
            pkt = P.i2c_read_packet(dev7, prefix if first else b"", k)
            out += self._stream(pkt, k)
            first = False
        return bytes(out)
