from __future__ import annotations

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from ..app_core import AppRunConfig, execute_run


class BuildWorker(QObject):
    log_message = Signal(str)
    status_changed = Signal(str)
    progress_changed = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, config: AppRunConfig) -> None:
        super().__init__()
        self._config = config
        self._stop_event = Event()

    def request_stop(self) -> None:
        self._stop_event.set()
        self.log_message.emit("ui.stop.requested.current.volume.then.stop")
        self.status_changed.emit("ui.stopping")

    @Slot()
    def run(self) -> None:
        try:
            summary = execute_run(
                config=self._config,
                log_callback=self.log_message.emit,
                status_callback=self.status_changed.emit,
                progress_callback=self.progress_changed.emit,
                stop_requested=self._stop_event.is_set,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(summary)
