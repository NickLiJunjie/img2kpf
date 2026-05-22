from __future__ import annotations

from threading import Event
from time import sleep

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
        self._pause_event = Event()

    def request_stop(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()
        self.log_message.emit("ui.stop.requested.current.volume.then.stop")
        self.status_changed.emit("ui.cancelling")

    def request_pause(self) -> None:
        if self._stop_event.is_set():
            return
        self._pause_event.set()
        self.log_message.emit("ui.pause.requested.current.volume.then.pause")
        self.status_changed.emit("ui.pausing")

    def request_resume(self) -> None:
        if not self._pause_event.is_set():
            return
        self._pause_event.clear()
        self.log_message.emit("ui.resume.requested")
        self.status_changed.emit("ui.running")

    def _wait_if_paused(self) -> None:
        if not self._pause_event.is_set() or self._stop_event.is_set():
            return
        self.log_message.emit("ui.pause.applied")
        self.status_changed.emit("ui.paused")
        while self._pause_event.is_set() and not self._stop_event.is_set():
            sleep(0.12)
        if not self._stop_event.is_set():
            self.log_message.emit("ui.resume.applied")
            self.status_changed.emit("ui.running")

    @Slot()
    def run(self) -> None:
        try:
            summary = execute_run(
                config=self._config,
                log_callback=self.log_message.emit,
                status_callback=self.status_changed.emit,
                progress_callback=self.progress_changed.emit,
                stop_requested=self._stop_event.is_set,
                pause_requested=self._wait_if_paused,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(summary)
