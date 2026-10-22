"""Imagens Intel sintéticas para os testes (descritor, GbE, ME, NVRAM, MSDM, $DMI)."""
import os, random

def make_image(seed, size=8 << 20, mac=b"\x00\x11\x22\x33\x44\x55", key=b"ABCDE-FGHIJ-KLMNO-PQRST-UVWXY",
               serial=b"PF2ABC12", nvfill=None):
    r = random.Random(seed)
    img = bytearray(b"\xFF" * size)
    img[0x10:0x14] = (0x0FF0A55A).to_bytes(4, "little")
    img[0x14:0x18] = ((0x04 << 16) | (4 << 24)).to_bytes(4, "little")   # FRBA = 0x40
    def flreg(base, limit):
        return ((base >> 12) | ((limit >> 12) << 16)).to_bytes(4, "little")
    regs = [(0, 0xFFF), (0x300000, size - 1), (0x3000, 0x2FFFFF), (0x1000, 0x2FFF)]
    for i, (b, l) in enumerate(regs):
        img[0x40 + 4 * i:0x44 + 4 * i] = flreg(b, l)
    img[0x1000:0x1006] = mac; img[0x2000:0x2006] = mac
    img[0x3000:0x100000] = bytes(r.getrandbits(8) for _ in range(0x100000 - 0x3000))   # "ME"
    # BIOS: um FV de NVRAM AMI e um FV de código
    fv = 0x300000
    img[fv + 16:fv + 32] = bytes.fromhex("A3B9F5CE6D477F499FDCE98143E0422C")
    img[fv + 32:fv + 40] = (0x40000).to_bytes(8, "little")
    img[fv + 40:fv + 44] = b"_FVH"
    nv = nvfill if nvfill is not None else bytes(r.getrandbits(8) for _ in range(0x1000))
    img[fv + 0x100:fv + 0x100 + len(nv)] = nv
    img[fv + 0x2000:fv + 0x2000 + len(serial)] = serial
    code = 0x400000
    img[code + 16:code + 32] = bytes(16); img[code + 40:code + 44] = b"_FVH"
    img[code + 0x1000:code + 0x1000 + 0x10000] = bytes(r.getrandbits(8) for _ in range(0x10000))
    img[code + 0x30:code + 0x40] = b"$IBIOSI$X505.312"
    m = 0x7F0000
    img[m:m + 4] = b"MSDM"; img[m + 4:m + 8] = (0x55).to_bytes(4, "little")
    img[m + 0x38:m + 0x38 + 29] = key
    img[0x7E0000:0x7E0004] = b"$DMI"; img[0x7E0004:0x7E0004 + len(serial)] = serial
    return bytes(img)
