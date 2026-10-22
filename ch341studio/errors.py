"""Exceções usadas no projeto (mensagens em português, prontas para a interface)."""


class CH341Error(Exception):
    """Erro genérico de comunicação ou operação."""


class DeviceNotFound(CH341Error):
    """O gravador não foi encontrado pelo driver escolhido."""


class BackendUnavailable(CH341Error):
    """O driver (libusb ou CH341DLL) não está disponível neste sistema."""


class ChipError(CH341Error):
    """Problema com a memória (não detectada, protegida, timeout...)."""


class ProtectedError(ChipError):
    """A memória está com proteção de escrita ativa (bits BP)."""


class Cancelled(CH341Error):
    """Operação cancelada pelo usuário."""
