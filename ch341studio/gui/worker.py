"""Execução das operações do gravador fora da thread da interface."""

import traceback

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..errors import Cancelled, CH341Error


class Worker(QObject):
    progress = Signal(object, object, str)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(object)    # a exceção

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def report(self, done, total, stage):
        self.progress.emit(done, total, stage)

    @Slot()
    def run(self):
        try:
            result = self.fn(self)
        except Cancelled as e:
            self.failed.emit(e)
        except CH341Error as e:
            self.failed.emit(e)
        except Exception as e:  # erro de programação: mostra o traceback no log
            self.log.emit(traceback.format_exc())
            self.failed.emit(e)
        else:
            self.done.emit(result)


def start(parent, fn, on_done, on_failed, on_progress, on_log):
    """Cria thread + worker e conecta os sinais. Retorna (thread, worker)."""
    thread = QThread(parent)
    worker = Worker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(on_progress)
    worker.log.connect(on_log)
    worker.done.connect(on_done)
    worker.failed.connect(on_failed)
    worker.done.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, worker
