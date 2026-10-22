"""Interface comum aos drivers do CH341A."""

from abc import ABC, abstractmethod


class Backend(ABC):
    """Um driver capaz de fazer transações SPI e I2C no CH341A."""

    name = "base"
    # maior leitura SPI numa única transação (um único CS baixo)
    max_spi_read = 4096
    # maior escrita I2C (endereço interno + dados) numa única transação
    max_i2c_write = 16
    # maior leitura I2C por transação
    max_i2c_read = 32
    # False no simulador: não faz sentido dormir esperando o chip
    realtime = True
    # suporta gravação de páginas em fila (sem consultar o status a cada página)
    supports_turbo = False

    def __init__(self):
        self.i2c_khz = 100

    @classmethod
    def available(cls) -> tuple[bool, str]:
        """(disponível?, motivo se não estiver)."""
        return True, ""

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def spi_xfer(self, write: bytes, read_len: int = 0) -> bytes:
        """CS baixo, envia `write`, lê `read_len` bytes, CS alto."""

    @abstractmethod
    def i2c_write(self, dev7: int, payload: bytes) -> None:
        """START, endereço+W, payload, STOP."""

    @abstractmethod
    def i2c_read(self, dev7: int, prefix: bytes, n: int) -> bytes:
        """[START, endereço+W, prefix], START, endereço+R, lê n bytes, STOP.

        Se prefix for vazio faz uma leitura "no endereço atual".
        """

    def set_i2c_speed(self, khz: int) -> None:
        self.i2c_khz = khz

    def describe(self) -> str:
        return self.name

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
