"""Seleção do driver do CH341A."""

import sys

from ..errors import BackendUnavailable, CH341Error
from .base import Backend
from .dll_backend import DllBackend
from .libusb_backend import LibusbBackend
from .simulator import SimBackend

BACKENDS = {
    "auto": "Automático",
    "libusb": "libusb (WinUSB / Linux)",
    "dll": "CH341DLL (driver oficial WCH)",
    "sim": "Simulador (sem hardware)",
}


def create_backend(kind: str = "auto") -> Backend:
    """Cria e abre o driver. Em 'auto' tenta CH341DLL (Windows) e depois libusb."""
    kind = kind.lower()
    if kind == "sim":
        b = SimBackend()
        b.open()
        return b
    if kind == "dll":
        b = DllBackend()
        b.open()
        return b
    if kind == "libusb":
        b = LibusbBackend()
        b.open()
        return b
    if kind != "auto":
        raise ValueError(f"driver desconhecido: {kind}")

    errors = []
    order = [DllBackend, LibusbBackend] if sys.platform == "win32" else [LibusbBackend]
    for cls in order:
        ok, why = cls.available()
        if not ok:
            errors.append(f"{cls.name}: {why}")
            continue
        b = cls()
        try:
            b.open()
            return b
        except Exception as e:
            errors.append(f"{cls.name}: {e}")
    raise BackendUnavailable("Nenhum driver conseguiu abrir o CH341A:\n" + "\n".join(errors))


__all__ = ["Backend", "LibusbBackend", "DllBackend", "SimBackend", "create_backend", "BACKENDS"]
