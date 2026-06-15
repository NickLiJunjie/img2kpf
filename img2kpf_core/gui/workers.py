from __future__ import annotations

from pathlib import Path
from threading import Event
from time import sleep

from PySide6.QtCore import QObject, Signal, Slot

from ..app_core import AppRunConfig, RunProgress, execute_run
from ..i18n import encode_i18n_message
from ..spread_splitter import split_spread_sources


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


class SplitSpreadWorker(QObject):
    log_message = Signal(str)
    status_changed = Signal(str)
    progress_changed = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        input_dir: Path,
        reading_direction: str,
        source_dirs: tuple[Path, ...],
        *,
        jobs: int = 1,
        shift_first_page: bool = False,
    ) -> None:
        super().__init__()
        self._input_dir = input_dir
        self._reading_direction = reading_direction
        self._source_dirs = source_dirs
        self._jobs = jobs
        self._shift_first_page = shift_first_page
        self._stop_event = Event()

    def request_stop(self) -> None:
        self._stop_event.set()
        self.log_message.emit("ui.stop.requested.current.volume.then.stop")
        self.status_changed.emit("ui.cancelling")

    def request_pause(self) -> None:
        return

    def request_resume(self) -> None:
        return

    @Slot()
    def run(self) -> None:
        self.status_changed.emit("ui.spread.split.running")
        self.log_message.emit("ui.spread.split.started")

        def report_progress(current: int, total: int, image_path: Path) -> None:
            self.progress_changed.emit(
                RunProgress(
                    mode="split",
                    phase="ui.spread.split.running",
                    current=current,
                    total=total,
                    current_name=image_path.name,
                )
            )

        try:
            result = split_spread_sources(
                self._input_dir,
                self._source_dirs,
                reading_direction=self._reading_direction,
                shift_first_page=self._shift_first_page,
                jobs=self._jobs,
                progress_callback=report_progress,
                stop_requested=self._stop_event.is_set,
            )
        except Exception as exc:
            if str(exc) == "ui.spread.split.cancelled":
                self.failed.emit("ui.spread.split.cancelled")
            else:
                self.failed.emit(str(exc))
            return

        self.log_message.emit(
            encode_i18n_message(
                "ui.spread.split.completed",
                split=result.split_image_count,
                blank=result.blank_page_count,
                output=result.output_image_count,
                path=result.output_dir,
            )
        )
        self.finished.emit(result)
