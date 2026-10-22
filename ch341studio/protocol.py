"""Protocolo USB do CH341A em modo "stream" (o mesmo usado pelo flashrom/ch341prog).

O CH341A (VID 1A86 / PID 5512) recebe comandos em pacotes bulk de até 32 bytes
no endpoint 0x02 e devolve dados no endpoint 0x82.

Regra importante: o chip interpreta CADA PACOTE USB isoladamente. Num pacote de
SPI stream (0xA8), todos os bytes depois do comando são enviados no barramento,
então nunca se pode "completar" um pacote SPI com zeros. Já pacotes UIO/I2C
terminam no sub-comando END e o resto do pacote é ignorado.

Pinos (modo SPI): D0 = CS, D3 = SCK, D5 = MOSI, D7 = MISO.
No modo stream o CH341A desloca os bits LSB primeiro, por isso cada byte SPI é
invertido (bit-reverse) na ida e na volta.
"""

VID = 0x1A86
PID = 0x5512
EP_OUT = 0x02
EP_IN = 0x82
PACKET = 32
SPI_DATA_PER_PACKET = PACKET - 1

# Comandos principais
CMD_SPI_STREAM = 0xA8
CMD_I2C_STREAM = 0xAA
CMD_UIO_STREAM = 0xAB

# Sub-comandos do I2C stream
I2C_STM_STA = 0x74   # condição de START
I2C_STM_STO = 0x75   # condição de STOP
I2C_STM_OUT = 0x80   # envia dados; bits 5..0 = quantidade
I2C_STM_IN = 0xC0    # lê dados; bits 5..0 = quantidade (0 = 1 byte com NAK)
I2C_STM_SET = 0x60   # configura velocidade (bits 1..0)
I2C_STM_US = 0x40    # atraso em µs
I2C_STM_MS = 0x50    # atraso em ms
I2C_STM_END = 0x00   # fim do pacote

# Sub-comandos do UIO stream (controle direto dos pinos D0..D5)
UIO_STM_IN = 0x00
UIO_STM_DIR = 0x40
UIO_STM_OUT = 0x80
UIO_STM_US = 0xC0
UIO_STM_END = 0x20

PINS_IDLE = 0x37     # CS=1, SCK=0 (modo 0), demais em 1
PINS_CS_LOW = 0x36   # CS=0
DIR_SPI = 0x3F       # D0..D5 como saída

I2C_SPEEDS = {20: 0, 100: 1, 400: 2, 750: 3}

# Tabela de inversão de bits (b7..b0 -> b0..b7)
REVERSE = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def rev(data) -> bytes:
    """Inverte a ordem dos bits de cada byte."""
    return bytes(data).translate(REVERSE)


def uio_packet(*subcmds: int) -> bytes:
    return bytes([CMD_UIO_STREAM, *subcmds, UIO_STM_END])


def pins_enable() -> bytes:
    return uio_packet(UIO_STM_OUT | PINS_IDLE, UIO_STM_DIR | DIR_SPI)


def pins_disable() -> bytes:
    return uio_packet(UIO_STM_OUT | PINS_IDLE, UIO_STM_DIR | 0x00)


def cs_assert() -> bytes:
    return uio_packet(UIO_STM_OUT | PINS_CS_LOW, UIO_STM_DIR | DIR_SPI)


def cs_release() -> bytes:
    return uio_packet(UIO_STM_OUT | PINS_IDLE)


def spi_packets(payload: bytes) -> bytes:
    """Divide os dados em pacotes SPI stream (cada um: 0xA8 + até 31 bytes)."""
    data = rev(payload)
    out = bytearray()
    for i in range(0, len(data), SPI_DATA_PER_PACKET):
        out.append(CMD_SPI_STREAM)
        out += data[i:i + SPI_DATA_PER_PACKET]
    return bytes(out)


def spi_transaction(write: bytes, read_len: int):
    """Monta uma transação SPI completa (CS baixo + dados).

    Retorna (blob, quantidade_de_bytes_que_o_chip_devolve).
    O pacote de CS ocupa os primeiros 32 bytes (completado com zeros, que o
    chip ignora após o END). Os pacotes SPI seguintes têm 32 bytes, exceto o
    último, então o blob pode ser enviado em pedaços múltiplos de 32.
    A liberação do CS deve ser enviada numa escrita USB separada.
    """
    payload = bytes(write) + b"\xFF" * read_len
    head = cs_assert().ljust(PACKET, b"\x00")
    return head + spi_packets(payload), len(payload)


def i2c_set_speed(khz: int) -> bytes:
    if khz not in I2C_SPEEDS:
        raise ValueError(f"Velocidade I2C inválida: {khz} kHz")
    return bytes([CMD_I2C_STREAM, I2C_STM_SET | I2C_SPEEDS[khz], I2C_STM_END])


# Limites de um pacote I2C (1 cmd + STA + OUT + ... + STO + END <= 32)
I2C_MAX_WRITE = 24   # bytes após o endereço do dispositivo (endereço interno + dados)
I2C_MAX_READ = 32


def i2c_write_packet(dev7: int, payload: bytes) -> bytes:
    """START, endereço (escrita) + payload, STOP."""
    if len(payload) > I2C_MAX_WRITE:
        raise ValueError("payload I2C grande demais para um pacote")
    body = bytes([dev7 << 1]) + bytes(payload)
    return bytes([CMD_I2C_STREAM, I2C_STM_STA, I2C_STM_OUT | len(body), *body,
                  I2C_STM_STO, I2C_STM_END])


def i2c_read_packet(dev7: int, prefix: bytes, n: int) -> bytes:
    """Lê n bytes. Se houver prefixo (endereço interno), faz escrita + START repetido."""
    if not 1 <= n <= I2C_MAX_READ:
        raise ValueError("leitura I2C deve ter entre 1 e 32 bytes por pacote")
    pkt = [CMD_I2C_STREAM, I2C_STM_STA]
    if prefix:
        body = bytes([dev7 << 1]) + bytes(prefix)
        pkt += [I2C_STM_OUT | len(body), *body, I2C_STM_STA]
    pkt += [I2C_STM_OUT | 1, (dev7 << 1) | 1]
    if n > 1:
        pkt.append(I2C_STM_IN | (n - 1))   # n-1 bytes com ACK
    pkt += [I2C_STM_IN, I2C_STM_STO, I2C_STM_END]  # último byte com NAK
    if len(pkt) > PACKET:
        raise ValueError("pacote I2C grande demais")
    return bytes(pkt)


def delay_packets(us: int) -> bytes:
    """Pacotes UIO de espera feita pelo próprio CH341A (até 63 µs por sub-comando)."""
    out = bytearray()
    while us > 0:
        cmds = []
        while us > 0 and len(cmds) < PACKET - 2:
            step = min(63, us)
            cmds.append(UIO_STM_US | step)
            us -= step
        out += uio_packet(*cmds).ljust(PACKET, b"\x00")
    return bytes(out)


def turbo_program_writes(pages, op: int, addr_len: int, delay_us: int):
    """Sequência de escritas USB para gravar páginas em fila.

    Para cada página: WREN, PP (opcode + endereço + dados), CS alto e uma espera
    feita pelo CH341A. Cada item retornado deve ir numa chamada de escrita USB
    separada: assim todo pacote SPI curto termina uma transferência e nunca se
    junta ao comando seguinte. Retorna (lista_de_escritas, bytes_esperados_de_volta).
    """
    pad = lambda pkt: pkt.ljust(PACKET, b"\x00")
    writes = []
    expect = 0
    prefix = b""
    for addr, data in pages:
        writes.append(prefix + pad(cs_assert()) + spi_packets(b"\x06"))
        expect += 1
        cmd = bytes([op]) + addr.to_bytes(addr_len, "big") + bytes(data)
        writes.append(pad(cs_release()) + pad(cs_assert()) + spi_packets(cmd))
        expect += len(cmd)
        prefix = pad(cs_release()) + delay_packets(delay_us)
    if prefix:
        writes.append(prefix)
    return writes, expect
