from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import tempfile
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QObject, Property, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from ...app_core import (
    AppRunConfig,
    build_image_processing_options,
    build_layout_options,
    detect_input_mode,
    preset_default_contrast,
    preset_default_gamma,
    preset_default_jpeg_quality,
    primary_output_suffix,
    resolve_output_location,
    suggest_output_location,
    validate_run_config,
)
from ...i18n import decode_i18n_message, encode_i18n_message, resolve_language
from ...plugin_registry import DEFAULT_KFX_PLUGIN_ID
from ...gui.i18n import normalize_ui_language, translate_gui_text, ui_language_options
from ...gui.models import (
    CROP_MODE_OPTIONS,
    GuiState,
    IMAGE_PRESET_OPTIONS,
    OUTPUT_FORMAT_OPTIONS,
    PANEL_MOVEMENT_OPTIONS,
    PAGE_LAYOUT_OPTIONS,
    READING_DIRECTION_OPTIONS,
    SHIFT_MODE_OPTIONS,
    TRI_STATE_OPTIONS,
    VIRTUAL_PANELS_OPTIONS,
)
from ...gui.preview import render_preview
from ...gui.settings import GuiSettingsStore
from ...gui.workers import BuildWorker


PROFILE_PRESERVED_FIELDS = frozenset(
    {
        "input_dir",
        "output_location",
        "title",
        "language",
        "theme_mode",
    }
)


class PreviewWorker(QObject):
    finished = Signal(str, str, str, int, int, object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        source_dir: Path,
        config: AppRunConfig,
        shift_first_page: bool,
        show_crop_boxes: bool,
        anchor_page_number: int | None,
        language: str,
        output_path: Path,
    ) -> None:
        super().__init__()
        self._source_dir = source_dir
        self._config = config
        self._shift_first_page = shift_first_page
        self._show_crop_boxes = show_crop_boxes
        self._anchor_page_number = anchor_page_number
        self._language = language
        self._output_path = output_path

    @Slot()
    def run(self) -> None:
        try:
            image_processing = build_image_processing_options(self._config)
            layout_options = build_layout_options(self._config)
            preview = render_preview(
                source_dir=self._source_dir,
                image_processing=image_processing,
                layout_options=layout_options,
                shift_first_page=self._shift_first_page,
                show_crop_boxes=self._show_crop_boxes,
                anchor_page_number=self._anchor_page_number,
                language=self._language,
            )
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            preview.image.save(self._output_path)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(
            str(self._output_path),
            preview.summary,
            preview.hint,
            preview.current_page_number,
            preview.total_pages,
            preview.available_page_numbers,
        )


class AppController(QObject):
    stateChanged = Signal()
    detectionChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._settings = GuiSettingsStore()
        loaded_state = self._settings.load()
        language = normalize_ui_language(loaded_state.language or resolve_language(), default="zh")
        loaded_state.language = language
        if not loaded_state.kfx_plugin:
            loaded_state.kfx_plugin = DEFAULT_KFX_PLUGIN_ID
        if not loaded_state.image_custom and (
            not loaded_state.gamma_auto or not loaded_state.contrast_auto or not loaded_state.jpeg_quality_auto
        ):
            loaded_state.image_custom = True
        self._state = loaded_state
        self._status_text = self._tr("ui.no.folder.selected")
        self._source_summary = ""
        self._is_runnable = False
        self._is_running = False
        self._run_state = "setup"
        self._run_status_text = self._tr("ui.waiting.start")
        self._run_summary_text = self._tr("ui.waiting.start")
        self._run_progress_current = 0
        self._run_progress_total = 0
        self._run_progress_successes = 0
        self._run_progress_failures = 0
        self._run_progress_name = ""
        self._run_cancel_requested = False
        self._run_pause_requested = False
        self._last_output_location = ""
        self._log_lines: list[dict[str, str]] = []
        self._selected_profile_name = self._initial_profile_name()
        self._worker_thread: QThread | None = None
        self._worker: BuildWorker | None = None
        self._last_detection = None
        self._preview_thread: QThread | None = None
        self._preview_worker: PreviewWorker | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(240)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self._preview_source = ""
        self._preview_status_text = self._tr("ui.preview.appears.input.folder.detected")
        self._preview_hint_text = self._tr("ui.preview.reflects.crop.color.single.facing.rtl")
        self._preview_busy = False
        self._preview_pending = False
        self._preview_anchor_page_number: int | None = None
        self._preview_selected_source_dir: Path | None = None
        self._preview_current_page_number = 0
        self._preview_total_pages = 0
        self._preview_available_page_numbers: tuple[int, ...] = ()
        self._preview_show_crop_boxes = False
        self._preview_aspect_ratio = 0.72
        self._preview_cache: dict[tuple, tuple[str, str, str, int, int, tuple[int, ...], float]] = {}
        self._preview_applied_cache_key: tuple | None = None
        self._preview_dir = Path(tempfile.gettempdir()) / "img2kpf-qml-preview"
        if self._state.input_dir:
            self._detect_input()
        self._schedule_preview_refresh()

    @Property(str, notify=stateChanged)
    def inputDir(self) -> str:
        return self._state.input_dir

    @Property(str, notify=stateChanged)
    def outputLocation(self) -> str:
        return self._state.output_location

    @Property(str, notify=stateChanged)
    def title(self) -> str:
        return self._state.title

    @Property(str, notify=stateChanged)
    def imagePreset(self) -> str:
        return self._state.image_preset

    @Property(str, notify=stateChanged)
    def imageStyle(self) -> str:
        return "custom" if self._state.image_custom else self._state.image_preset

    @Property(bool, notify=stateChanged)
    def imageCustom(self) -> bool:
        return self._state.image_custom

    @Property(str, notify=stateChanged)
    def imageCustomBaseText(self) -> str:
        return self._tr("ui.custom.based.on").format(base=self._label_for_value(IMAGE_PRESET_OPTIONS, self._state.image_preset))

    @Property(str, notify=stateChanged)
    def cropMode(self) -> str:
        return self._state.crop_mode

    @Property(float, notify=stateChanged)
    def spreadFillEdgeThreshold(self) -> float:
        return self._state.spread_fill_edge_threshold

    @Property(str, notify=stateChanged)
    def readingDirection(self) -> str:
        return self._state.reading_direction

    @Property(str, notify=stateChanged)
    def pageLayout(self) -> str:
        return self._state.page_layout

    @Property(str, notify=stateChanged)
    def virtualPanels(self) -> str:
        return self._state.virtual_panels

    @Property(str, notify=stateChanged)
    def outputFormat(self) -> str:
        return self._state.output_format

    @Property(str, notify=stateChanged)
    def outputLocationPickerMode(self) -> str:
        detection = self._last_detection
        if detection is not None and detection.mode == "batch":
            return "folder"
        return "file"

    @Property(QUrl, notify=stateChanged)
    def outputLocationDialogFolder(self) -> QUrl:
        return QUrl.fromLocalFile(str(self._output_dialog_folder()))

    @Property(QUrl, notify=stateChanged)
    def outputLocationDialogFile(self) -> QUrl:
        return QUrl.fromLocalFile(str(self._output_dialog_file()))

    @Property(str, notify=stateChanged)
    def outputLocationFileFilter(self) -> str:
        return self._output_file_filter()

    @Property(str, notify=stateChanged)
    def templatePath(self) -> str:
        return self._state.template_path

    @Property(str, notify=stateChanged)
    def panelMovement(self) -> str:
        return self._state.panel_movement

    @Property(str, notify=stateChanged)
    def panelPreset(self) -> str:
        return self._state.panel_preset

    @Property(str, notify=stateChanged)
    def targetSizeText(self) -> str:
        return self._state.target_size_text

    @Property(str, notify=stateChanged)
    def preserveColor(self) -> str:
        return self._state.preserve_color

    @Property(str, notify=stateChanged)
    def autoContrast(self) -> str:
        return self._state.autocontrast

    @Property(str, notify=stateChanged)
    def autoLevel(self) -> str:
        return self._state.autolevel

    @Property(str, notify=stateChanged)
    def shiftMode(self) -> str:
        return self._state.shift_mode

    @Property(str, notify=stateChanged)
    def kfxPlugin(self) -> str:
        return self._state.kfx_plugin

    @Property(int, notify=stateChanged)
    def jobs(self) -> int:
        return self._state.jobs

    @Property(str, notify=stateChanged)
    def language(self) -> str:
        return self._state.language

    @Property(str, notify=stateChanged)
    def themeMode(self) -> str:
        return self._state.theme_mode

    @Property(float, notify=stateChanged)
    def gammaValue(self) -> float:
        return self._state.gamma_value

    @Property(float, notify=stateChanged)
    def contrastValue(self) -> float:
        return self._state.contrast_value

    @Property(int, notify=stateChanged)
    def jpegQualityValue(self) -> int:
        return self._state.jpeg_quality_value

    @Property(str, notify=stateChanged)
    def previewImageSource(self) -> str:
        return self._preview_source

    @Property(str, notify=stateChanged)
    def previewStatusText(self) -> str:
        return self._preview_status_text

    @Property(str, notify=stateChanged)
    def previewHintText(self) -> str:
        return self._preview_hint_text

    @Property(bool, notify=stateChanged)
    def previewBusy(self) -> bool:
        return self._preview_busy

    @Property(bool, notify=stateChanged)
    def previewShowCropBoxes(self) -> bool:
        return self._preview_show_crop_boxes

    @Property(str, notify=stateChanged)
    def previewPageText(self) -> str:
        if self._preview_total_pages <= 0:
            return "—"
        return f"{self._preview_current_page_number}/{self._preview_total_pages}"

    @Property(int, notify=stateChanged)
    def previewCurrentPageNumber(self) -> int:
        return self._preview_current_page_number

    @Property(int, notify=stateChanged)
    def previewTotalPages(self) -> int:
        return self._preview_total_pages

    @Property("QVariantList", notify=stateChanged)
    def previewVolumeOptions(self) -> list[dict[str, str]]:
        detection = self._last_detection
        if detection is None or detection.mode != "batch":
            return []
        return [{"value": str(path), "label": path.name} for path in detection.image_subdirs]

    @Property(str, notify=stateChanged)
    def previewVolume(self) -> str:
        return str(self._preview_selected_source_dir or "")

    @Property(bool, notify=stateChanged)
    def previewCanGoPrevious(self) -> bool:
        return self._preview_neighbor_page(-1) is not None

    @Property(bool, notify=stateChanged)
    def previewCanGoNext(self) -> bool:
        return self._preview_neighbor_page(1) is not None

    @Property(bool, notify=stateChanged)
    def previewCanGoLeft(self) -> bool:
        return self._preview_neighbor_page(self._visual_preview_direction("left")) is not None

    @Property(bool, notify=stateChanged)
    def previewCanGoRight(self) -> bool:
        return self._preview_neighbor_page(self._visual_preview_direction("right")) is not None

    @Property(str, notify=stateChanged)
    def previewLeftActionText(self) -> str:
        return self._tr("ui.preview.next" if self._visual_preview_direction("left") > 0 else "ui.preview.prev")

    @Property(str, notify=stateChanged)
    def previewRightActionText(self) -> str:
        return self._tr("ui.preview.next" if self._visual_preview_direction("right") > 0 else "ui.preview.prev")

    @Property(float, notify=stateChanged)
    def previewAspectRatio(self) -> float:
        return self._preview_aspect_ratio

    @Property(str, notify=stateChanged)
    def statusText(self) -> str:
        return self._status_text

    @Property(str, notify=detectionChanged)
    def inputMode(self) -> str:
        detection = self._last_detection
        return str(detection.mode) if detection is not None else "empty"

    @Property(str, notify=detectionChanged)
    def inputModeText(self) -> str:
        detection = self._last_detection
        if detection is None or not detection.is_runnable:
            return ""
        return self._tr("ui.batch" if detection.mode == "batch" else "ui.single")

    @Property(str, notify=detectionChanged)
    def sourceSummary(self) -> str:
        return self._source_summary

    @Property(str, notify=detectionChanged)
    def headerCaptionText(self) -> str:
        if not self._state.input_dir.strip():
            return self._tr("ui.next.choose.input.folder")

        detection = self._last_detection
        if detection is None:
            return self._status_text
        if detection.is_runnable:
            return ""
        if detection.mode == "empty":
            return self._tr("ui.choose.folder.containing.jpg.jpeg.png.images")
        return self._status_text

    @Property(str, notify=detectionChanged)
    def headerDetailText(self) -> str:
        detection = self._last_detection
        if detection is None or not detection.is_runnable:
            return ""
        return translate_gui_text(detection.message, self._state.language)

    @Property(bool, notify=detectionChanged)
    def isRunnable(self) -> bool:
        return self._is_runnable

    @Property(bool, notify=stateChanged)
    def isRunning(self) -> bool:
        return self._is_running

    @Property(str, notify=stateChanged)
    def runStatusText(self) -> str:
        return self._run_status_text

    @Property(str, notify=stateChanged)
    def runState(self) -> str:
        if self._is_running:
            return self._run_state
        if self._run_state in {"completed", "partial", "failed", "cancelled", "stale"}:
            return self._run_state
        return "ready" if self._is_runnable else "setup"

    @Property(str, notify=stateChanged)
    def runStateText(self) -> str:
        return self._tr(
            {
                "setup": "ui.setup",
                "ready": "ui.ready",
                "running": "ui.running.compact",
                "pausing": "ui.pausing",
                "paused": "ui.paused",
                "cancelling": "ui.cancelling",
                "completed": "ui.task.completed",
                "partial": "ui.task.partial",
                "failed": "ui.task.failed",
                "cancelled": "ui.task.cancelled",
                "stale": "ui.task.stale",
            }.get(self.runState, "ui.setup")
        )

    @Property(str, notify=stateChanged)
    def runSummaryText(self) -> str:
        return self._run_summary_text

    @Property(str, notify=stateChanged)
    def runProgressText(self) -> str:
        if self._run_progress_total <= 0:
            return "—"
        return f"{self._run_progress_current} / {self._run_progress_total}"

    @Property(float, notify=stateChanged)
    def runProgressValue(self) -> float:
        if self._run_progress_total <= 0:
            return 0.0
        return max(0.0, min(1.0, self._run_progress_current / self._run_progress_total))

    @Property(bool, notify=stateChanged)
    def canPauseRun(self) -> bool:
        return self._is_running and self._run_state == "running"

    @Property(bool, notify=stateChanged)
    def canResumeRun(self) -> bool:
        return self._is_running and self._run_state == "paused"

    @Property(bool, notify=stateChanged)
    def canCancelRun(self) -> bool:
        return self._is_running and self._run_state != "cancelling"

    @Property(bool, notify=stateChanged)
    def canOpenOutput(self) -> bool:
        return not self._is_running and self._output_open_target() is not None

    @Property(bool, notify=stateChanged)
    def canRerun(self) -> bool:
        return (
            not self._is_running
            and self._is_runnable
            and self.runState in {"completed", "partial", "failed", "cancelled", "stale"}
        )

    @Property(bool, notify=stateChanged)
    def canClearInputOutput(self) -> bool:
        return bool(self._state.input_dir or self._state.output_location or self._state.title)

    @Property(str, notify=stateChanged)
    def logText(self) -> str:
        return "\n".join(entry["text"] for entry in self._log_lines[-8:])

    @Property(str, notify=stateChanged)
    def fullLogText(self) -> str:
        return "\n".join(entry["text"] for entry in self._log_lines)

    @Property("QVariantList", notify=stateChanged)
    def logEntries(self) -> list[dict[str, str]]:
        return [dict(entry) for entry in self._log_lines]

    @Property(bool, notify=stateChanged)
    def canClearLogs(self) -> bool:
        return bool(self._log_lines)

    @Property("QVariantList", notify=stateChanged)
    def profileOptions(self) -> list[dict[str, str]]:
        profiles = self._settings.load_profiles()
        default_profile = self._settings.load_default_profile_name()
        options: list[dict[str, str]] = []
        for name in sorted(profiles):
            options.append(
                {
                    "value": name,
                    "label": f"★ {name}" if name == default_profile else name,
                    "tooltip": self._profile_summary_text(name, name == default_profile),
                }
            )
        return options

    @Property(str, notify=stateChanged)
    def selectedProfileName(self) -> str:
        return self._selected_profile_name

    @Property(str, notify=stateChanged)
    def defaultProfileName(self) -> str:
        default = self._settings.load_default_profile_name()
        return default or ""

    @Property(str, notify=stateChanged)
    def profileStatusText(self) -> str:
        return self._profile_status_text()

    @Property(bool, notify=stateChanged)
    def selectedProfileDirty(self) -> bool:
        return self._selected_profile_is_dirty()

    @Property(bool, notify=stateChanged)
    def canRevertSelectedProfile(self) -> bool:
        return self._selected_profile_is_dirty()

    @Property(str, notify=stateChanged)
    def profileStatusTone(self) -> str:
        if self._selected_profile_is_dirty():
            return "warning"
        if self._profile_exists(self._selected_profile_name):
            return "success"
        return "neutral"

    @Property("QVariantList", notify=stateChanged)
    def selectedProfileChangePreview(self) -> list[dict[str, str]]:
        return self._selected_profile_change_preview()

    @Property("QVariantList", notify=stateChanged)
    def languageOptions(self) -> list[dict[str, str]]:
        return [{"value": value, "label": label} for value, label in ui_language_options()]

    @Property("QVariantList", notify=stateChanged)
    def imagePresetOptions(self) -> list[dict[str, str]]:
        return self._options(IMAGE_PRESET_OPTIONS)

    @Property("QVariantList", notify=stateChanged)
    def imageStyleOptions(self) -> list[dict[str, str]]:
        return [*self._options(IMAGE_PRESET_OPTIONS), {"value": "custom", "label": self._tr("ui.image.preset.custom")}]

    @Property("QVariantList", notify=stateChanged)
    def cropModeOptions(self) -> list[dict[str, str]]:
        return self._options(CROP_MODE_OPTIONS)

    @Property("QVariantList", notify=stateChanged)
    def readingDirectionOptions(self) -> list[dict[str, str]]:
        return self._options(READING_DIRECTION_OPTIONS)

    @Property("QVariantList", notify=stateChanged)
    def pageLayoutOptions(self) -> list[dict[str, str]]:
        return self._options(PAGE_LAYOUT_OPTIONS)

    @Property("QVariantList", notify=stateChanged)
    def virtualPanelsOptions(self) -> list[dict[str, str]]:
        return self._options(VIRTUAL_PANELS_OPTIONS)

    @Property("QVariantList", notify=stateChanged)
    def outputFormatOptions(self) -> list[dict[str, str]]:
        return self._options(OUTPUT_FORMAT_OPTIONS)

    @Property("QVariantList", notify=stateChanged)
    def panelMovementOptions(self) -> list[dict[str, str]]:
        return self._options(PANEL_MOVEMENT_OPTIONS)

    @Property("QVariantList", notify=stateChanged)
    def panelPresetOptions(self) -> list[dict[str, str]]:
        custom_tooltip = self._state.target_size_text.strip().replace("x", " × ")
        return [
            {
                "value": "none",
                "label": self._tr("ui.not.set"),
                "tooltip": "",
            },
            {
                "value": "scribe_1240x1860",
                "label": "Kindle Scribe",
                "tooltip": "1240 × 1860",
            },
            {
                "value": "custom",
                "label": self._tr("ui.custom.size.label"),
                "tooltip": custom_tooltip,
            },
        ]

    @Property("QVariantList", notify=stateChanged)
    def triStateOptions(self) -> list[dict[str, str]]:
        return self._options(TRI_STATE_OPTIONS)

    @Property("QVariantList", notify=stateChanged)
    def shiftModeOptions(self) -> list[dict[str, str]]:
        return self._options(SHIFT_MODE_OPTIONS)

    @Property(bool, notify=stateChanged)
    def panelPresetCustom(self) -> bool:
        return self._state.panel_preset == "custom"

    @Property(bool, notify=stateChanged)
    def shiftModeEnabled(self) -> bool:
        return self._state.page_layout == "facing"

    @Property(bool, notify=stateChanged)
    def panelMovementEnabled(self) -> bool:
        return self._state.virtual_panels == "enabled"

    @Property(bool, notify=stateChanged)
    def kfxPluginEnabled(self) -> bool:
        return self._state.output_format in {"kpf_kfx", "kfx_only"}

    @Property(bool, notify=stateChanged)
    def jobsEnabled(self) -> bool:
        return bool(self._last_detection is not None and self._last_detection.mode == "batch")

    @Property(bool, notify=stateChanged)
    def spreadFillEdgeThresholdEnabled(self) -> bool:
        return self._state.crop_mode in {"kcc-spread-fill", "spread-fill"}

    @Slot(str)
    def setInputDir(self, value: str) -> None:
        normalized = self._normalize_path(value)
        if self._state.input_dir == normalized:
            return
        self._state.input_dir = normalized
        self._preview_anchor_page_number = None
        self._preview_selected_source_dir = None
        self._detect_input()
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot(str)
    def setOutputLocation(self, value: str) -> None:
        normalized = self._normalize_path(value)
        if self._state.output_location == normalized:
            return
        self._state.output_location = normalized
        self._mark_output_affecting_change()
        self._save()
        self.stateChanged.emit()

    @Slot(str, result=bool)
    def profileExists(self, name: str) -> bool:
        return self._profile_exists(name)

    @Slot(str)
    def setSelectedProfileName(self, value: str) -> None:
        normalized = self._normalize_profile_name(value)
        if normalized == self._selected_profile_name:
            return
        self._selected_profile_name = normalized
        self.stateChanged.emit()

    @Slot(result=bool)
    def loadSelectedProfile(self) -> bool:
        return self._apply_selected_profile("ui.profile.loaded")

    @Slot(result=bool)
    def revertSelectedProfile(self) -> bool:
        return self._apply_selected_profile("ui.profile.reverted")

    @Slot(str, result=bool)
    def saveProfile(self, name: str) -> bool:
        normalized = self._normalize_profile_name(name)
        if not normalized:
            self._status_text = self._tr("ui.please.enter.profile.name")
            self.stateChanged.emit()
            return False
        self._settings.save_profile(normalized, self._state)
        self._selected_profile_name = normalized
        self._status_text = self._tr(encode_i18n_message("ui.profile.saved", name=normalized))
        self.stateChanged.emit()
        return True

    @Slot(result=bool)
    def deleteSelectedProfile(self) -> bool:
        name = self._selected_profile_name
        if not name:
            self._status_text = self._tr("ui.please.select.profile.first")
            self.stateChanged.emit()
            return False
        if not self._profile_exists(name):
            self._status_text = self._tr("ui.selected.profile.does.not.exist.please.choose")
            self._selected_profile_name = self._initial_profile_name()
            self.stateChanged.emit()
            return False
        self._settings.delete_profile(name)
        self._selected_profile_name = self._initial_profile_name()
        self._status_text = self._tr(encode_i18n_message("ui.profile.deleted", name=name))
        self.stateChanged.emit()
        return True

    @Slot(result=bool)
    def setSelectedProfileDefault(self) -> bool:
        name = self._selected_profile_name
        if not name:
            self._status_text = self._tr("ui.please.select.profile.first")
            self.stateChanged.emit()
            return False
        if not self._profile_exists(name):
            self._status_text = self._tr("ui.selected.profile.does.not.exist.please.choose")
            self.stateChanged.emit()
            return False
        self._settings.set_default_profile(name)
        self._status_text = self._tr(encode_i18n_message("ui.profile.default.set", name=name))
        self.stateChanged.emit()
        return True

    @Slot(result=bool)
    def clearDefaultProfile(self) -> bool:
        default_profile = self._settings.load_default_profile_name()
        if default_profile is None:
            self._status_text = self._tr("ui.no.default.profile.set")
            self.stateChanged.emit()
            return False
        self._settings.set_default_profile(None)
        self._status_text = self._tr("ui.profile.default.cleared")
        self.stateChanged.emit()
        return True

    @Slot(result=bool)
    def toggleSelectedProfileDefault(self) -> bool:
        name = self._selected_profile_name
        if not name:
            self._status_text = self._tr("ui.please.select.profile.first")
            self.stateChanged.emit()
            return False
        if self._settings.load_default_profile_name() == name:
            return self.clearDefaultProfile()
        return self.setSelectedProfileDefault()

    @Slot()
    def clearLogs(self) -> None:
        if not self._log_lines:
            return
        self._log_lines.clear()
        self.stateChanged.emit()

    @Slot()
    def copyLogsToClipboard(self) -> None:
        text = "\n".join(entry["text"] for entry in self._log_lines)
        QGuiApplication.clipboard().setText(text)
        if text:
            self._status_text = self._tr("ui.logs.copied")
            self.stateChanged.emit()

    @Slot(str)
    def setTemplatePath(self, value: str) -> None:
        normalized = self._normalize_path(value)
        if self._state.template_path == normalized:
            return
        self._state.template_path = normalized
        self._mark_output_affecting_change()
        self._save()
        self.stateChanged.emit()

    @Slot(str)
    def setTitle(self, value: str) -> None:
        if self._state.title == value:
            return
        self._state.title = value
        self._mark_output_affecting_change()
        self._save()
        self.stateChanged.emit()

    @Slot(str, str)
    def setOption(self, name: str, value: str) -> None:
        if not hasattr(self._state, name):
            return
        if getattr(self._state, name) == value:
            return
        setattr(self._state, name, value)
        if name == "output_format":
            self._detect_input()
        if name == "image_preset":
            if self._state.gamma_auto:
                self._state.gamma_value = preset_default_gamma(value)
            if self._state.contrast_auto:
                self._state.contrast_value = preset_default_contrast(value)
            if self._state.jpeg_quality_auto:
                self._state.jpeg_quality_value = preset_default_jpeg_quality(value)
        if name == "shift_mode":
            self._state.shift = value == "on"
        if name == "page_layout" and value == "single":
            self._state.shift_mode = "off"
            self._state.shift = False
        if name == "panel_preset":
            self._state.scribe_panel = value == "scribe_1240x1860"
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot(float)
    def setSpreadFillEdgeThreshold(self, value: float) -> None:
        normalized = round(max(0.7, min(float(value), 1.0)), 2)
        if self._state.spread_fill_edge_threshold == normalized:
            return
        self._state.spread_fill_edge_threshold = normalized
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot()
    def resetSpreadFillEdgeThreshold(self) -> None:
        if self._state.spread_fill_edge_threshold == 0.96:
            return
        self._state.spread_fill_edge_threshold = 0.96
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot(str)
    def setImageStyle(self, value: str) -> None:
        if value == "custom":
            if self._state.image_custom:
                return
            self._state.image_custom = True
            self._mark_output_affecting_change()
            self._save()
            self._schedule_preview_refresh()
            self.stateChanged.emit()
            return
        if self._state.image_preset == value and not self._state.image_custom:
            return
        self._state.image_preset = value
        self._state.image_custom = False
        self._state.gamma_value = preset_default_gamma(value)
        self._state.gamma_auto = True
        self._state.contrast_value = preset_default_contrast(value)
        self._state.contrast_auto = True
        self._state.jpeg_quality_value = preset_default_jpeg_quality(value)
        self._state.jpeg_quality_auto = True
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot(str)
    def setLanguage(self, value: str) -> None:
        selected = normalize_ui_language(value, default=self._state.language)
        if selected == self._state.language:
            return
        if self._is_running:
            self._status_text = self._tr("ui.please.wait.task.finish.switching.language")
            self.stateChanged.emit()
            return
        self._state.language = selected
        self._save()
        self._run_status_text = self._tr("ui.waiting.start")
        if self._state.input_dir:
            self._detect_input()
        else:
            self._status_text = self._tr("ui.no.folder.selected")
            self._source_summary = ""
            self.detectionChanged.emit()
        self.stateChanged.emit()

    @Slot(str)
    def setTargetSizeText(self, value: str) -> None:
        normalized = value.strip()
        if self._state.target_size_text == normalized:
            return
        self._state.target_size_text = normalized
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot(str)
    def setKfxPlugin(self, value: str) -> None:
        normalized = self._normalize_path(value)
        if self._state.kfx_plugin == normalized:
            return
        self._state.kfx_plugin = normalized
        self._mark_output_affecting_change()
        self._save()
        self.stateChanged.emit()

    @Slot(int)
    def setJobs(self, value: int) -> None:
        normalized = max(1, min(int(value), 64))
        if self._state.jobs == normalized:
            return
        self._state.jobs = normalized
        self._mark_output_affecting_change()
        self._save()
        self.stateChanged.emit()

    @Slot()
    def toggleThemeMode(self) -> None:
        self._state.theme_mode = "light" if self._state.theme_mode == "dark" else "dark"
        self._save()
        self.stateChanged.emit()

    @Slot(float)
    def setGammaValue(self, value: float) -> None:
        normalized = round(max(0.1, min(float(value), 3.0)), 2)
        if self._state.gamma_value == normalized and not self._state.gamma_auto:
            return
        self._state.image_custom = True
        self._state.gamma_value = normalized
        self._state.gamma_auto = False
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot(float)
    def setContrastValue(self, value: float) -> None:
        normalized = round(max(0.4, min(float(value), 2.2)), 2)
        if self._state.contrast_value == normalized and not self._state.contrast_auto:
            return
        self._state.image_custom = True
        self._state.contrast_value = normalized
        self._state.contrast_auto = False
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot(int)
    def setJpegQualityValue(self, value: int) -> None:
        normalized = max(1, min(int(value), 100))
        if self._state.jpeg_quality_value == normalized and not self._state.jpeg_quality_auto:
            return
        self._state.image_custom = True
        self._state.jpeg_quality_value = normalized
        self._state.jpeg_quality_auto = False
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot()
    def resetGammaValue(self) -> None:
        self._state.gamma_value = preset_default_gamma(self._state.image_preset)
        self._state.gamma_auto = True
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot()
    def resetContrastValue(self) -> None:
        self._state.contrast_value = preset_default_contrast(self._state.image_preset)
        self._state.contrast_auto = True
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot()
    def resetJpegQualityValue(self) -> None:
        self._state.jpeg_quality_value = preset_default_jpeg_quality(self._state.image_preset)
        self._state.jpeg_quality_auto = True
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot()
    def resetSettingsToDefaults(self) -> None:
        defaults = GuiState()
        preserved_input_dir = self._state.input_dir
        preserved_output_location = self._state.output_location
        preserved_title = self._state.title
        preserved_language = self._state.language
        preserved_theme_mode = self._state.theme_mode

        self._state = defaults
        self._state.input_dir = preserved_input_dir
        self._state.output_location = preserved_output_location
        self._state.title = preserved_title
        self._state.language = preserved_language
        self._state.theme_mode = preserved_theme_mode
        if not self._state.kfx_plugin:
            self._state.kfx_plugin = DEFAULT_KFX_PLUGIN_ID

        if self._state.input_dir:
            self._detect_input()
        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self.stateChanged.emit()

    @Slot()
    def clearInputOutput(self) -> None:
        if not (self._state.input_dir or self._state.output_location or self._state.title):
            return
        self._state.input_dir = ""
        self._state.output_location = ""
        self._state.title = ""
        self._preview_anchor_page_number = None
        self._preview_selected_source_dir = None
        self._last_output_location = ""
        self._reset_run_result()
        self._detect_input()
        self._save()
        self._schedule_preview_refresh(immediate=True)
        self.stateChanged.emit()

    @Slot()
    def togglePreviewCropBoxes(self) -> None:
        self._preview_show_crop_boxes = not self._preview_show_crop_boxes
        self._schedule_preview_refresh(immediate=True)
        self.stateChanged.emit()

    @Slot()
    def previousPreviewPage(self) -> None:
        self._set_preview_page_from_neighbor(-1)

    @Slot()
    def nextPreviewPage(self) -> None:
        self._set_preview_page_from_neighbor(1)

    @Slot()
    def leftPreviewPage(self) -> None:
        self._set_preview_page_from_neighbor(self._visual_preview_direction("left"))

    @Slot()
    def rightPreviewPage(self) -> None:
        self._set_preview_page_from_neighbor(self._visual_preview_direction("right"))

    @Slot(str)
    def jumpPreviewPage(self, value: str) -> None:
        try:
            page_number = int(value.strip())
        except ValueError:
            self.stateChanged.emit()
            return
        if self._preview_total_pages <= 0:
            return
        page_number = max(1, min(page_number, self._preview_total_pages))
        if page_number == self._preview_current_page_number:
            self.stateChanged.emit()
            return
        self._preview_anchor_page_number = page_number
        self._schedule_preview_refresh(immediate=True)
        self.stateChanged.emit()

    @Slot(str)
    def setPreviewVolume(self, value: str) -> None:
        candidate = Path(self._normalize_path(value))
        detection = self._last_detection
        if detection is None or detection.mode != "batch":
            return
        if candidate not in detection.image_subdirs:
            return
        if self._preview_selected_source_dir == candidate:
            return
        self._preview_selected_source_dir = candidate
        self._preview_anchor_page_number = None
        self._schedule_preview_refresh(immediate=True)
        self.stateChanged.emit()

    @Slot(str, result=str)
    def uiText(self, key: str) -> str:
        return self._tr(key)

    @Slot(str, "QVariant", result="QVariant")
    def valueForKey(self, name: str, fallback: object = None) -> object:
        if not isinstance(name, str) or not name or name.startswith("_"):
            return fallback
        try:
            value = getattr(self, name)
        except Exception:
            return fallback
        return fallback if value is None else value

    @Slot()
    def startRun(self) -> None:
        if self._is_running:
            return
        try:
            config = self._build_run_config()
            detection = validate_run_config(config)
            output_location = resolve_output_location(config, detection.mode)
        except Exception as exc:
            self._status_text = self._tr(str(exc))
            self._run_status_text = self._tr("ui.validation.failed")
            self._run_state = "failed"
            self._run_summary_text = self._tr(str(exc))
            self._append_log_entry(str(exc), level="danger")
            self.stateChanged.emit()
            return

        self._save()
        self._log_lines = []
        self._append_log_entry("ui.run.started", level="info")
        self._append_log_entry(encode_i18n_message("ui.summary.output", path=output_location), level="muted")
        self._is_running = True
        self._run_state = "running"
        self._run_cancel_requested = False
        self._run_pause_requested = False
        self._run_progress_current = 0
        self._run_progress_total = 0
        self._run_progress_successes = 0
        self._run_progress_failures = 0
        self._run_progress_name = ""
        self._run_status_text = self._tr("ui.preparing")
        self._run_summary_text = self._tr("ui.background.task.about.start")
        self._status_text = self._tr("ui.running")
        self._last_output_location = ""
        self.stateChanged.emit()

        self._worker_thread = QThread(self)
        self._worker = BuildWorker(config)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.log_message.connect(self._append_log)
        self._worker.status_changed.connect(self._update_run_status)
        self._worker.progress_changed.connect(self._update_progress)
        self._worker.finished.connect(self._handle_finished)
        self._worker.failed.connect(self._handle_failed)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.failed.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker)
        self._worker_thread.start()

    @Slot()
    def requestStop(self) -> None:
        if self._worker is None:
            return
        self._run_state = "cancelling"
        self._run_cancel_requested = True
        self._run_status_text = self._tr("ui.cancelling")
        self._run_summary_text = self._tr("ui.stop.requested.current.volume.then.stop")
        self.stateChanged.emit()
        self._worker.request_stop()

    @Slot()
    def requestPause(self) -> None:
        if self._worker is None or not (self._is_running and self._run_state == "running"):
            return
        self._run_state = "pausing"
        self._run_pause_requested = True
        self._run_status_text = self._tr("ui.pausing")
        self._run_summary_text = self._tr("ui.pause.requested.current.volume.then.pause")
        self.stateChanged.emit()
        self._worker.request_pause()

    @Slot()
    def requestResume(self) -> None:
        if self._worker is None or not (self._is_running and self._run_state == "paused"):
            return
        self._run_state = "running"
        self._run_pause_requested = False
        self._run_status_text = self._tr("ui.running")
        self._run_summary_text = self._tr("ui.resume.requested")
        self.stateChanged.emit()
        self._worker.request_resume()

    @Slot()
    def openOutputLocation(self) -> None:
        target = self._output_open_target()
        if target is None:
            self._status_text = self._tr("ui.output.folder.unavailable")
            self.stateChanged.emit()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self._status_text = self._tr("ui.output.open.failed")
            self.stateChanged.emit()

    @Slot(result="QVariantMap")
    def stateSnapshot(self) -> dict:
        return asdict(self._state)

    def _build_run_config(self) -> AppRunConfig:
        return AppRunConfig(
            input_dir=self._state.input_dir,
            output_location=self._state.output_location,
            template_path=self._state.template_path,
            title=self._state.title,
            shift=self._state.shift,
            reading_direction=self._state.reading_direction,
            page_layout=self._state.page_layout,
            virtual_panels=self._state.virtual_panels == "enabled",
            panel_movement=self._state.panel_movement,
            image_preset=self._state.image_preset,
            crop_mode=self._state.crop_mode,
            spread_fill_edge_threshold=self._state.spread_fill_edge_threshold,
            target_size_text=self._state.target_size_text if self._state.panel_preset == "custom" else "",
            scribe_panel=self._state.scribe_panel,
            preserve_color=self._state.preserve_color,
            gamma_value=self._state.gamma_value,
            gamma_auto=self._state.gamma_auto,
            contrast_value=self._state.contrast_value,
            contrast_auto=self._state.contrast_auto,
            autocontrast=self._state.autocontrast,
            autolevel=self._state.autolevel,
            jpeg_quality_value=self._state.jpeg_quality_value,
            jpeg_quality_auto=self._state.jpeg_quality_auto,
            emit_kfx=self._state.output_format in {"kpf_kfx", "kfx_only"},
            output_format=self._state.output_format,
            kfx_plugin=self._state.kfx_plugin,
            jobs=self._state.jobs,
        )

    def _detect_input(self) -> None:
        input_text = self._state.input_dir.strip()
        self._is_runnable = False
        self._last_detection = None
        if not input_text:
            self._status_text = self._tr("ui.no.folder.selected")
            self._source_summary = ""
            self._preview_source = ""
            self._preview_status_text = self._tr("ui.preview.appears.input.folder.detected")
            self._preview_hint_text = self._tr("ui.preview.reflects.crop.color.single.facing.rtl")
            self._preview_current_page_number = 0
            self._preview_total_pages = 0
            self._preview_available_page_numbers = ()
            self._preview_applied_cache_key = None
            self.detectionChanged.emit()
            self.stateChanged.emit()
            return

        try:
            detection = detect_input_mode(Path(input_text))
        except Exception as exc:
            self._status_text = self._tr(str(exc))
            self._source_summary = ""
            self._preview_source = ""
            self._preview_status_text = self._tr("ui.preview.temporarily.unavailable")
            self._preview_hint_text = self._tr("ui.preview.reflects.crop.color.single.facing.rtl")
            self._preview_current_page_number = 0
            self._preview_total_pages = 0
            self._preview_available_page_numbers = ()
            self._preview_applied_cache_key = None
            self.detectionChanged.emit()
            self.stateChanged.emit()
            return

        self._last_detection = detection
        self._is_runnable = detection.is_runnable
        if detection.mode == "batch" and detection.image_subdirs:
            if self._preview_selected_source_dir not in detection.image_subdirs:
                self._preview_selected_source_dir = detection.image_subdirs[0]
        else:
            self._preview_selected_source_dir = None
        self._status_text = translate_gui_text(detection.message, self._state.language)
        if detection.mode == "single":
            self._source_summary = f"{Path(input_text).name} · {self._tr(encode_i18n_message('ui.mode.count.images', count=len(detection.root_images)))}"
        elif detection.mode == "batch":
            self._source_summary = f"{Path(input_text).name} · {self._tr(encode_i18n_message('ui.mode.count.volumes', count=len(detection.image_subdirs)))}"
        else:
            self._source_summary = ""

        if not self._state.output_location and detection.is_runnable:
            suggestion = suggest_output_location(Path(input_text), detection.mode, self._state.output_format)
            if suggestion is not None:
                self._state.output_location = str(suggestion)
        self.detectionChanged.emit()
        self.stateChanged.emit()

    def _schedule_preview_refresh(self, immediate: bool = False) -> None:
        if self._resolve_preview_source_dir() is not None:
            self._preview_busy = True
            self._preview_status_text = self._tr("ui.preview.updating")
        if self._preview_thread is not None:
            self._preview_pending = True
            return
        if immediate:
            self._preview_timer.stop()
            self._refresh_preview()
            return
        self._preview_timer.start()

    def _resolve_preview_source_dir(self) -> Path | None:
        detection = self._last_detection
        if detection is None or not detection.is_runnable:
            return None
        if detection.mode == "single":
            return detection.input_dir
        if detection.mode == "batch" and detection.image_subdirs:
            if self._preview_selected_source_dir in detection.image_subdirs:
                return self._preview_selected_source_dir
            return detection.image_subdirs[0]
        return None

    def _preview_cache_key(self, source_dir: Path) -> tuple:
        return (
            "preview-crop-v5-facing-fill-outer-anchor",
            str(source_dir),
            self._state.image_preset,
            self._state.image_custom,
            round(self._state.gamma_value, 2),
            self._state.gamma_auto,
            round(self._state.contrast_value, 2),
            self._state.contrast_auto,
            self._state.jpeg_quality_value,
            self._state.jpeg_quality_auto,
            self._state.crop_mode,
            round(self._state.spread_fill_edge_threshold, 2),
            self._state.reading_direction,
            self._state.page_layout,
            self._state.virtual_panels,
            self._state.panel_movement,
            self._state.target_size_text if self._state.panel_preset == "custom" else "",
            self._state.panel_preset,
            self._state.preserve_color,
            self._state.autocontrast,
            self._state.autolevel,
            self._state.shift,
            self._preview_show_crop_boxes,
            self._preview_anchor_page_number,
        )

    @Slot()
    def _refresh_preview(self) -> None:
        source_dir = self._resolve_preview_source_dir()
        if source_dir is None:
            self._preview_source = ""
            self._preview_busy = False
            self._preview_status_text = self._tr("ui.preview.appears.input.folder.detected")
            self._preview_hint_text = self._tr("ui.preview.reflects.crop.color.single.facing.rtl")
            self._preview_current_page_number = 0
            self._preview_total_pages = 0
            self._preview_available_page_numbers = ()
            self._preview_applied_cache_key = None
            self.stateChanged.emit()
            return

        cache_key = self._preview_cache_key(source_dir)
        cached = self._preview_cache.get(cache_key)
        if cached and Path(cached[0]).is_file():
            self._apply_preview_payload(cached, cache_key)
            self._preview_busy = False
            self.stateChanged.emit()
            return

        self._preview_busy = True
        self._preview_status_text = self._tr("ui.preview.updating")
        self.stateChanged.emit()

        cache_id = hashlib.sha1(repr(cache_key).encode("utf-8")).hexdigest()[:16]
        output_path = self._preview_dir / f"preview_{cache_id}.png"
        self._preview_thread = QThread(self)
        self._preview_worker = PreviewWorker(
            source_dir=source_dir,
            config=self._build_run_config(),
            shift_first_page=self._state.shift,
            show_crop_boxes=self._preview_show_crop_boxes,
            anchor_page_number=self._preview_anchor_page_number,
            language=self._state.language,
            output_path=output_path,
        )
        self._preview_worker.moveToThread(self._preview_thread)
        self._preview_thread.started.connect(self._preview_worker.run)
        self._preview_worker.finished.connect(
            lambda path, summary, hint, current_page, total_pages, available_pages, key=cache_key: self._handle_preview_finished(
                path,
                summary,
                hint,
                current_page,
                total_pages,
                available_pages,
                key,
            )
        )
        self._preview_worker.failed.connect(lambda message, key=cache_key: self._handle_preview_failed(message, key))
        self._preview_worker.finished.connect(self._preview_thread.quit)
        self._preview_worker.failed.connect(self._preview_thread.quit)
        self._preview_thread.finished.connect(self._cleanup_preview_worker)
        self._preview_thread.start()

    def _handle_preview_finished(
        self,
        path: str,
        summary: str,
        hint: str,
        current_page: int,
        total_pages: int,
        available_pages: object,
        cache_key: tuple,
    ) -> None:
        preview_path = Path(path)
        aspect_ratio = 0.72
        try:
            from PIL import Image

            with Image.open(preview_path) as image:
                aspect_ratio = max(0.2, min(image.width / max(1, image.height), 4.0))
        except Exception:
            pass

        normalized_available_pages = tuple(int(page) for page in available_pages)
        self._preview_cache[cache_key] = (
            path,
            summary,
            hint,
            current_page,
            total_pages,
            normalized_available_pages,
            aspect_ratio,
        )
        if self._preview_pending or self._current_preview_cache_key() != cache_key:
            return
        self._apply_preview_payload(self._preview_cache[cache_key], cache_key)
        self._preview_busy = False
        self.stateChanged.emit()

    def _handle_preview_failed(self, message: str, cache_key: tuple) -> None:
        if self._preview_pending or self._current_preview_cache_key() != cache_key:
            return
        self._preview_source = ""
        self._preview_status_text = self._tr("ui.preview.generation.failed")
        self._preview_hint_text = self._tr(message)
        self._preview_applied_cache_key = None
        self._preview_busy = False
        self.stateChanged.emit()

    def _cleanup_preview_worker(self) -> None:
        if self._preview_worker is not None:
            self._preview_worker.deleteLater()
            self._preview_worker = None
        if self._preview_thread is not None:
            self._preview_thread.deleteLater()
            self._preview_thread = None
        if self._preview_pending:
            self._preview_pending = False
            self._schedule_preview_refresh(immediate=True)
            return
        self._sync_preview_state_after_worker()

    def _preview_neighbor_page(self, direction: int) -> int | None:
        current_page = self._preview_current_page_number or self._preview_anchor_page_number or 1
        if direction < 0:
            previous_pages = [page for page in self._preview_available_page_numbers if page < current_page]
            return previous_pages[-1] if previous_pages else None
        next_pages = [page for page in self._preview_available_page_numbers if page > current_page]
        return next_pages[0] if next_pages else None

    def _set_preview_page_from_neighbor(self, direction: int) -> None:
        page_number = self._preview_neighbor_page(direction)
        if page_number is None:
            return
        self._preview_anchor_page_number = page_number
        self._schedule_preview_refresh(immediate=True)
        self.stateChanged.emit()

    def _visual_preview_direction(self, side: str) -> int:
        if self._state.reading_direction == "rtl":
            return 1 if side == "left" else -1
        return -1 if side == "left" else 1

    def _options(self, source: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
        return [{"value": value, "label": self._tr(label)} for value, label in source]

    def _tr(self, key: str) -> str:
        return translate_gui_text(key, self._state.language)

    @Slot(str)
    def _append_log(self, message: str) -> None:
        self._append_log_entry(message)

    def _append_log_entry(self, message: str, level: str | None = None) -> None:
        resolved_level = level or self._infer_log_level(message)
        self._log_lines.append({"text": self._tr(message), "level": resolved_level})
        self.stateChanged.emit()

    @Slot(str)
    def _update_run_status(self, text: str) -> None:
        if text == "ui.pausing":
            self._run_state = "pausing"
        elif text == "ui.paused":
            self._run_state = "paused"
        elif text == "ui.cancelling":
            self._run_state = "cancelling"
        elif text == "ui.running" and self._is_running and not self._run_cancel_requested:
            self._run_state = "running"
        self._run_status_text = self._tr(text)
        self.stateChanged.emit()

    @Slot(object)
    def _update_progress(self, progress: object) -> None:
        total = max(int(getattr(progress, "total", 0) or 0), 0)
        current = max(0, min(int(getattr(progress, "current", 0) or 0), total if total > 0 else 0))
        self._run_progress_current = current
        self._run_progress_total = total
        self._run_progress_successes = int(getattr(progress, "successes", 0) or 0)
        self._run_progress_failures = int(getattr(progress, "failures", 0) or 0)
        self._run_progress_name = str(getattr(progress, "current_name", "") or "")

        phase = self._tr(str(getattr(progress, "phase", "") or self._run_status_text))
        pieces = [phase]
        if self._run_progress_name:
            pieces.append(self._run_progress_name)
        if total > 0:
            pieces.append(f"{current} / {total}")
        if str(getattr(progress, "mode", "")) == "batch":
            pieces.append(
                self._tr(
                    encode_i18n_message(
                        "ui.success.failed",
                        successes=self._run_progress_successes,
                        failures=self._run_progress_failures,
                    )
                )
            )
        self._run_summary_text = " · ".join(pieces)
        if self._is_running and self._run_state not in {"paused", "pausing", "cancelling"}:
            self._run_state = "running"
        self.stateChanged.emit()

    def _handle_finished(self, summary: object) -> None:
        self._append_log_entry("ui.run.completed", level="success")
        successes = len(getattr(summary, "successes", ()) or ())
        failures = len(getattr(summary, "failures", ()) or ())
        stopped = bool(getattr(summary, "stopped", False))
        output_location = getattr(summary, "output_location", "")
        self._last_output_location = str(output_location) if successes > 0 and output_location else ""

        if stopped:
            self._run_state = "cancelled"
            self._run_status_text = self._tr("ui.task.cancelled")
            self._status_text = self._tr("ui.task.cancelled")
        elif failures > 0 and successes > 0:
            self._run_state = "partial"
            self._run_status_text = self._tr("ui.task.partial")
            self._status_text = self._tr("ui.task.partial")
        elif failures > 0:
            self._run_state = "failed"
            self._run_status_text = self._tr("ui.task.failed")
            self._status_text = self._tr("ui.task.failed")
        else:
            self._run_state = "completed"
            self._run_status_text = self._tr("ui.task.completed")
            self._status_text = self._tr("ui.task.completed")

        self._run_summary_text = self._tr(
            encode_i18n_message(
                "ui.task.summary.done",
                successes=successes,
                failures=failures,
                output=output_location,
            )
        )
        self._run_cancel_requested = False
        self._run_pause_requested = False
        self.stateChanged.emit()

    def _handle_failed(self, message: str) -> None:
        self._append_log_entry(encode_i18n_message("ui.log.run.failed", reason=message), level="danger")
        self._run_state = "failed"
        self._run_status_text = self._tr("ui.task.failed")
        self._run_summary_text = self._tr(message)
        self._status_text = self._tr("ui.task.failed")
        self._run_cancel_requested = False
        self._run_pause_requested = False
        self.stateChanged.emit()

    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._worker_thread is not None:
            self._worker_thread.deleteLater()
            self._worker_thread = None
        self._is_running = False
        self.stateChanged.emit()

    def _mark_output_affecting_change(self) -> None:
        if self._is_running:
            return
        if not self._is_runnable:
            if self._run_state in {"completed", "partial", "failed", "cancelled", "stale"}:
                self._reset_run_result()
            return
        if self._run_state in {"completed", "partial"}:
            self._run_state = "stale"
            self._run_status_text = self._tr("ui.task.stale")
            self._run_summary_text = self._tr("ui.task.changed.needs.rerun")
            self._status_text = self._tr("ui.task.stale")
            self._clear_run_progress()
        elif self._run_state in {"failed", "cancelled"}:
            self._reset_run_result()

    def _reset_run_result(self) -> None:
        self._run_state = "setup"
        self._run_status_text = self._tr("ui.waiting.start")
        self._run_summary_text = self._tr("ui.waiting.start")
        self._clear_run_progress()
        if self._is_runnable:
            self._status_text = self._tr("ui.ready")

    def _clear_run_progress(self) -> None:
        self._run_progress_current = 0
        self._run_progress_total = 0
        self._run_progress_successes = 0
        self._run_progress_failures = 0
        self._run_progress_name = ""
        self._run_cancel_requested = False
        self._run_pause_requested = False

    def _output_open_target(self) -> Path | None:
        if not self._last_output_location.strip():
            return None
        path = Path(self._last_output_location).expanduser()
        if path.is_dir():
            return path
        if path.exists():
            return path.parent
        if path.suffix and path.parent.exists():
            return path.parent
        return None

    def _save(self) -> None:
        self._settings.save(self._state)

    def _initial_profile_name(self) -> str:
        default_profile = self._settings.load_default_profile_name()
        if default_profile:
            return default_profile
        profiles = sorted(self._settings.load_profiles())
        return profiles[0] if profiles else ""

    def _normalize_profile_name(self, value: str) -> str:
        return value.strip()

    def _profile_exists(self, name: str) -> bool:
        normalized = self._normalize_profile_name(name)
        if not normalized:
            return False
        return normalized in self._settings.load_profiles()

    def _apply_selected_profile(self, status_key: str) -> bool:
        name = self._selected_profile_name
        state = self._settings.get_profile(name) if name else None
        if state is None:
            self._status_text = self._tr("ui.selected.profile.does.not.exist.please.choose")
            self.stateChanged.emit()
            return False

        preserved = {field: getattr(self._state, field) for field in PROFILE_PRESERVED_FIELDS}
        self._state = state
        for field, value in preserved.items():
            setattr(self._state, field, value)
        if not self._state.kfx_plugin:
            self._state.kfx_plugin = DEFAULT_KFX_PLUGIN_ID

        if self._state.input_dir:
            self._detect_input()
        else:
            self._status_text = self._tr("ui.no.folder.selected")
            self._source_summary = ""
            self.detectionChanged.emit()

        self._mark_output_affecting_change()
        self._save()
        self._schedule_preview_refresh()
        self._status_text = self._tr(encode_i18n_message(status_key, name=name))
        self.stateChanged.emit()
        return True

    def _selected_profile_is_dirty(self) -> bool:
        name = self._selected_profile_name
        state = self._settings.get_profile(name) if name else None
        if state is None:
            return False
        return self._profile_payload(self._state) != self._profile_payload(state)

    def _selected_profile_change_preview(self) -> list[dict[str, str]]:
        name = self._selected_profile_name
        state = self._settings.get_profile(name) if name else None
        if state is None:
            return []
        return self._profile_change_preview(self._state, state)

    def _profile_payload(self, state: GuiState) -> dict[str, object]:
        payload = asdict(state)
        for field in PROFILE_PRESERVED_FIELDS:
            payload.pop(field, None)
        if not payload.get("kfx_plugin"):
            payload["kfx_plugin"] = DEFAULT_KFX_PLUGIN_ID
        return payload

    def _profile_summary_text(self, name: str, is_default: bool) -> str:
        state = self._settings.get_profile(name)
        if state is None:
            return name
        parts = [self._label_for_value(IMAGE_PRESET_OPTIONS, state.image_preset)]
        parts.append(self._label_for_value(READING_DIRECTION_OPTIONS, state.reading_direction))
        parts.append(self._label_for_value(PAGE_LAYOUT_OPTIONS, state.page_layout))
        if state.panel_preset == "custom" and state.target_size_text:
            parts.append(state.target_size_text)
        if is_default:
            parts.append(self._tr("ui.default.profile.selected").replace("{profile}", name))
        return " · ".join(parts)

    def _profile_status_text(self) -> str:
        profiles = self._settings.load_profiles()
        if not profiles:
            return self._tr("ui.profile.none.saved.yet")
        selected = self._selected_profile_name if self._selected_profile_name in profiles else ""
        if not selected:
            default_profile = self._settings.load_default_profile_name()
            if default_profile:
                return self._tr(encode_i18n_message("ui.default.profile", profile=default_profile))
            return self._tr("ui.no.default.profile.selected")
        key = "ui.current.profile.modified" if self._selected_profile_is_dirty() else "ui.current.profile.synced"
        return self._tr(encode_i18n_message(key, profile=selected))

    def _profile_change_preview(self, current: GuiState, target: GuiState) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        groups = (
            (
                "ui.image.preset",
                (
                    "image_preset",
                    "image_custom",
                    "gamma_value",
                    "gamma_auto",
                    "contrast_value",
                    "contrast_auto",
                    "jpeg_quality_value",
                    "jpeg_quality_auto",
                    "autocontrast",
                    "autolevel",
                    "preserve_color",
                ),
                self._image_settings_summary,
            ),
            ("ui.crop", ("crop_mode", "spread_fill_edge_threshold"), self._crop_summary),
            ("ui.reading.direction", ("reading_direction",), self._reading_direction_summary),
            ("ui.layout", ("page_layout", "shift_mode", "shift"), self._layout_summary),
            ("ui.virtual.panels", ("virtual_panels", "panel_movement"), self._virtual_panels_summary),
            ("ui.panel.size", ("panel_preset", "target_size_text", "scribe_panel"), self._panel_size_summary),
            ("ui.output.format", ("output_format",), self._output_format_summary),
            ("ui.template.file", ("template_path",), self._template_path_summary),
            ("ui.kfx.plugin", ("kfx_plugin",), self._kfx_plugin_summary),
            ("ui.parallel.volumes", ("jobs",), self._jobs_summary),
        )
        current_payload = self._profile_payload(current)
        target_payload = self._profile_payload(target)
        for label_key, fields, formatter in groups:
            if not any(current_payload.get(field) != target_payload.get(field) for field in fields):
                continue
            rows.append(
                {
                    "label": self._tr(label_key),
                    "current": formatter(current),
                    "target": formatter(target),
                }
            )
        return rows

    def _image_settings_summary(self, state: GuiState) -> str:
        base = self._label_for_value(IMAGE_PRESET_OPTIONS, state.image_preset)
        if not state.image_custom:
            return base
        return " · ".join(
            [
                self._tr("ui.custom.based.on").format(base=base),
                f"{self._tr('ui.gamma')} {state.gamma_value:g}",
                f"{self._tr('ui.contrast')} {state.contrast_value:g}",
                f"{self._tr('ui.jpeg.quality')} {state.jpeg_quality_value}",
            ]
        )

    def _crop_summary(self, state: GuiState) -> str:
        summary = self._label_for_value(CROP_MODE_OPTIONS, state.crop_mode)
        if state.crop_mode in {"kcc-spread-fill", "spread-fill"}:
            summary = " · ".join([summary, f"{self._tr('ui.spread.fill.edge.threshold')} {state.spread_fill_edge_threshold:.2f}"])
        return summary

    def _reading_direction_summary(self, state: GuiState) -> str:
        return self._label_for_value(READING_DIRECTION_OPTIONS, state.reading_direction)

    def _layout_summary(self, state: GuiState) -> str:
        parts = [self._label_for_value(PAGE_LAYOUT_OPTIONS, state.page_layout)]
        if state.page_layout == "facing" and state.shift_mode == "on":
            parts.append(self._tr("ui.first.shift"))
        return " · ".join(parts)

    def _virtual_panels_summary(self, state: GuiState) -> str:
        if state.virtual_panels != "enabled":
            return self._tr("ui.virtual.panels.disabled")
        return self._label_for_value(PANEL_MOVEMENT_OPTIONS, state.panel_movement)

    def _panel_size_summary(self, state: GuiState) -> str:
        if state.panel_preset == "scribe_1240x1860":
            return "Kindle Scribe"
        if state.panel_preset == "custom" and state.target_size_text:
            return self._tr("ui.custom").format(size=state.target_size_text)
        return self._tr("ui.not.set")

    def _output_format_summary(self, state: GuiState) -> str:
        return self._label_for_value(OUTPUT_FORMAT_OPTIONS, state.output_format)

    def _template_path_summary(self, state: GuiState) -> str:
        return self._file_name_or_not_set(state.template_path)

    def _kfx_plugin_summary(self, state: GuiState) -> str:
        path = state.kfx_plugin
        if not path or path == DEFAULT_KFX_PLUGIN_ID:
            return self._tr("ui.default.kfx.output")
        return self._file_name_or_not_set(path)

    def _file_name_or_not_set(self, path: str) -> str:
        if not path:
            return self._tr("ui.not.set")
        return Path(path).name or path

    def _jobs_summary(self, state: GuiState) -> str:
        return str(state.jobs)

    def _output_dialog_folder(self) -> Path:
        if self._state.output_location.strip():
            path = Path(self._state.output_location).expanduser()
            if path.is_dir():
                return path
            if path.parent.exists():
                return path.parent
        if self._state.input_dir.strip():
            return Path(self._state.input_dir).expanduser()
        return Path.home()

    def _output_dialog_file(self) -> Path:
        current = self._state.output_location.strip()
        if current:
            path = Path(current).expanduser()
            if path.suffix:
                return path
            if path.is_dir():
                suffix = primary_output_suffix(self._state.output_format)
                return path / f"output{suffix}"
        input_dir = self._state.input_dir.strip()
        if input_dir:
            base = Path(input_dir).expanduser()
            suggestion = suggest_output_location(base, self._last_detection.mode if self._last_detection else "single", self._state.output_format)
            if suggestion is not None and suggestion.suffix:
                return suggestion
        return self._output_dialog_folder() / f"output{primary_output_suffix(self._state.output_format)}"

    def _output_file_filter(self) -> str:
        suffix = primary_output_suffix(self._state.output_format)
        if suffix == ".kfx":
            return "Kindle Format (*.kfx)"
        return "Kindle Package (*.kpf)"

    def _infer_log_level(self, message: str) -> str:
        parsed = decode_i18n_message(message)
        key = parsed[0] if parsed is not None else message
        if key in {"ui.log.run.failed", "ui.task.failed"} or "failed" in key or "error" in key:
            return "danger"
        if key in {"ui.task.completed", "ui.run.completed"}:
            return "success"
        if key in {"ui.task.partial", "ui.task.cancelled", "ui.task.stale", "ui.cancelling", "ui.pausing", "ui.paused"}:
            return "warning"
        if key in {"ui.stop.requested.current.volume.then.stop", "ui.pause.requested.current.volume.then.pause", "ui.resume.requested", "ui.resume.applied"}:
            return "muted"
        return "neutral"

    def _current_preview_cache_key(self) -> tuple | None:
        source_dir = self._resolve_preview_source_dir()
        if source_dir is None:
            return None
        return self._preview_cache_key(source_dir)

    def _apply_preview_payload(
        self,
        payload: tuple[str, str, str, int, int, tuple[int, ...], float],
        cache_key: tuple,
    ) -> None:
        path, summary, hint, current_page, total_pages, available_pages, aspect_ratio = payload
        self._preview_source = QUrl.fromLocalFile(path).toString()
        self._preview_status_text = summary
        self._preview_hint_text = hint
        self._preview_current_page_number = current_page
        self._preview_total_pages = total_pages
        self._preview_available_page_numbers = available_pages
        self._preview_aspect_ratio = aspect_ratio
        self._preview_applied_cache_key = cache_key

    def _sync_preview_state_after_worker(self) -> None:
        current_cache_key = self._current_preview_cache_key()
        if current_cache_key is None:
            if self._preview_source or self._preview_applied_cache_key is not None or self._preview_busy:
                self._preview_source = ""
                self._preview_status_text = self._tr("ui.preview.appears.input.folder.detected")
                self._preview_hint_text = self._tr("ui.preview.reflects.crop.color.single.facing.rtl")
                self._preview_current_page_number = 0
                self._preview_total_pages = 0
                self._preview_available_page_numbers = ()
                self._preview_applied_cache_key = None
                self._preview_busy = False
                self.stateChanged.emit()
            return

        cached = self._preview_cache.get(current_cache_key)
        if cached is not None and Path(cached[0]).is_file():
            if self._preview_applied_cache_key != current_cache_key:
                self._apply_preview_payload(cached, current_cache_key)
                self._preview_busy = False
                self.stateChanged.emit()
                return
            if self._preview_busy:
                self._preview_busy = False
                self.stateChanged.emit()
            return

        if self._preview_busy:
            self._preview_busy = False
            self.stateChanged.emit()

    def _label_for_value(self, source: tuple[tuple[str, str], ...], value: str) -> str:
        for option_value, label in source:
            if option_value == value:
                return self._tr(label)
        return value

    @staticmethod
    def _normalize_path(value: str) -> str:
        if not value:
            return ""
        if value.startswith("file:"):
            parsed = urlparse(value)
            return unquote(parsed.path)
        return value
