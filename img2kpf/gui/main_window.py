from __future__ import annotations

from pathlib import Path
import textwrap

from PySide6.QtCore import QEvent, QPoint, QThread, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QIcon, QPalette, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QInputDialog,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..app_core import (
    CLI_PARAMETER_INFO,
    AppRunConfig,
    DetectionResult,
    RunProgress,
    RunSummary,
    detect_input_mode,
    output_directory_suffix,
    preset_default_gamma,
    preset_default_jpeg_quality,
    primary_output_suffix,
    resolve_output_location,
    suggest_output_location,
    build_image_processing_options,
    build_layout_options,
    validate_run_config,
)
from ..i18n import encode_i18n_message, resolve_language
from .assets import load_app_icon
from .i18n import normalize_ui_language, translate_gui_text, ui_language_options
from .models import (
    CROP_MODE_OPTIONS,
    GuiState,
    IMAGE_PRESET_OPTIONS,
    OUTPUT_FORMAT_OPTIONS,
    PAGE_LAYOUT_OPTIONS,
    PANEL_PRESET_OPTIONS,
    PANEL_MOVEMENT_OPTIONS,
    READING_DIRECTION_OPTIONS,
    SHIFT_MODE_OPTIONS,
    TRI_STATE_OPTIONS,
    VIRTUAL_PANELS_OPTIONS,
)
from .preview import render_preview
from .settings import GuiSettingsStore
from .workers import BuildWorker


READING_DIRECTION_TOOLTIP = "ui.reading.direction.rtl.right.left.comics.ltr"
PAGE_LAYOUT_TOOLTIP = "ui.layout.facing.creates.spreads.single.creates.one"
VIRTUAL_PANELS_TOOLTIP = "ui.enable.disable.kindle.virtual.panels"
PANEL_MOVEMENT_TOOLTIP = "ui.virtual.panels.movement.direction"
LANGUAGE_TOOLTIP = "ui.choose.ui.language.english"


def _route_wheel_to_ancestor_scroll_area(widget: QWidget, event: QWheelEvent) -> bool:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            scroll_bar = parent.verticalScrollBar()
            if scroll_bar is None or scroll_bar.maximum() <= scroll_bar.minimum():
                return False

            delta = event.pixelDelta().y()
            if delta == 0:
                angle = event.angleDelta().y()
                if angle == 0:
                    return False
                delta = int((angle / 120) * max(scroll_bar.singleStep(), 24))

            scroll_bar.setValue(scroll_bar.value() - int(delta))
            event.accept()
            return True
        parent = parent.parentWidget()
    return False


class DropdownOnlyComboBox(QComboBox):
    def wheelEvent(self, event: QWheelEvent) -> None:
        if not _route_wheel_to_ancestor_scroll_area(self, event):
            event.ignore()

    def showPopup(self) -> None:
        if self.count() == 0:
            return

        self._close_custom_popup()

        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        if hasattr(Qt.WindowType, "NoDropShadowWindowHint"):
            flags |= Qt.WindowType.NoDropShadowWindowHint

        popup = DropdownPopup(self, flags)
        popup.setObjectName("ComboFloatingPopup")
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer_layout = QVBoxLayout(popup)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        panel = QFrame(popup)
        panel.setObjectName("ComboFloatingPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        scroll = QScrollArea(panel)
        scroll.setObjectName("ComboFloatingScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget(scroll)
        content.setObjectName("ComboFloatingContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(2)

        for index in range(self.count()):
            item_button = QPushButton(self.itemText(index), content)
            item_button.setObjectName("ComboPopupItem")
            item_button.setCursor(Qt.CursorShape.PointingHandCursor)
            item_button.setProperty("selected", index == self.currentIndex())
            item_button.clicked.connect(lambda checked=False, row=index: self._select_custom_popup_index(row))
            content_layout.addWidget(item_button)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        panel_layout.addWidget(scroll)
        outer_layout.addWidget(panel)

        text_width = max(self.fontMetrics().horizontalAdvance(self.itemText(index)) for index in range(self.count()))
        popup_width = max(self.width(), text_width + 58, 220)
        popup_position = self.mapToGlobal(QPoint(0, self.height() + 6))
        screen = QApplication.screenAt(popup_position) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        visible_items = min(self.count(), 7)
        button_height = 42
        popup_height = visible_items * button_height + 20
        if available is not None:
            popup_height = min(popup_height, max(180, int(available.height() * 0.48)))
        scroll.setFixedHeight(popup_height)
        popup.setFixedWidth(popup_width)
        popup.adjustSize()
        popup_height = popup.sizeHint().height()

        if screen is not None:
            if popup_position.x() + popup_width > available.right():
                popup_position.setX(max(available.left(), available.right() - popup_width))
            if popup_position.y() + popup_height > available.bottom():
                upward_y = self.mapToGlobal(QPoint(0, -popup_height - 6)).y()
                popup_position.setY(max(available.top(), upward_y))

        popup.move(popup_position)
        popup.destroyed.connect(lambda: setattr(self, "_custom_popup", None))
        self._custom_popup = popup
        popup.show()

    def hidePopup(self) -> None:
        if self._close_custom_popup():
            return
        super().hidePopup()

    def _select_custom_popup_index(self, index: int) -> None:
        self._close_custom_popup()
        self.setCurrentIndex(index)
        try:
            self.activated.emit(index)
        except TypeError:
            pass

    def _close_custom_popup(self) -> bool:
        popup = getattr(self, "_custom_popup", None)
        if popup is None:
            return False
        self._custom_popup = None
        popup.close()
        popup.deleteLater()
        return True


class DropdownPopup(QWidget):
    def wheelEvent(self, event: QWheelEvent) -> None:
        event.accept()


class DropdownPopupListView(QListView):
    def wheelEvent(self, event: QWheelEvent) -> None:
        event.accept()


class PreviewPageSpinBox(QSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:
        if not _route_wheel_to_ancestor_scroll_area(self, event):
            event.ignore()


class HelpTipPopup(QWidget):
    def __init__(self) -> None:
        flags = Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        if hasattr(Qt.WindowType, "NoDropShadowWindowHint"):
            flags |= Qt.WindowType.NoDropShadowWindowHint
        super().__init__(None, flags)
        self.setObjectName("HelpTipPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._max_content_width_px = 360
        self.setStyleSheet(
            """
            QWidget#HelpTipPopup {
                background: transparent;
                border: 0;
            }
            QFrame#HelpTipPanel {
                background: #ffffff;
                border: 1px solid #c9c9d2;
                border-radius: 13px;
            }
            QLabel#HelpTipText {
                color: #1d1d1f;
                background: transparent;
                border: 0;
                font-size: 13px;
                font-weight: 650;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        panel = QFrame(self)
        panel.setObjectName("HelpTipPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 12, 16, 12)
        panel_layout.setSpacing(0)

        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 45))
        panel.setGraphicsEffect(shadow)

        self.label = QLabel(panel)
        self.label.setObjectName("HelpTipText")
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(420)
        panel_layout.addWidget(self.label)
        layout.addWidget(panel)

    def show_for(self, anchor: QWidget, text: str) -> None:
        self.label.setText(self._wrap_text(text))
        self.adjustSize()

        anchor_global = anchor.mapToGlobal(QPoint(0, anchor.height() + 8))
        popup_width = self.sizeHint().width()
        popup_height = self.sizeHint().height()
        screen = QApplication.screenAt(anchor_global) or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x = min(anchor_global.x(), available.right() - popup_width)
            y = anchor_global.y()
            if y + popup_height > available.bottom():
                y = anchor.mapToGlobal(QPoint(0, -popup_height - 8)).y()
            x = max(available.left(), x)
            y = max(available.top(), y)
            anchor_global = QPoint(x, y)

        self.move(anchor_global)
        self.show()
        self.raise_()

    def _wrap_text(self, text: str) -> str:
        metrics = self.label.fontMetrics()
        wrapped_lines: list[str] = []
        for raw_line in text.splitlines():
            if not raw_line.strip():
                wrapped_lines.append("")
                continue
            if self._contains_cjk(raw_line):
                wrapped_lines.extend(self._wrap_by_pixels(raw_line, metrics, self._max_content_width_px))
            else:
                wrapped_lines.extend(
                    textwrap.wrap(raw_line, width=52, break_long_words=False, break_on_hyphens=False) or [raw_line]
                )
        return "\n".join(wrapped_lines)

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= character <= "\u9fff" for character in text)

    @staticmethod
    def _wrap_by_pixels(text: str, metrics, max_width_px: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for character in text:
            candidate = f"{current}{character}"
            if current and metrics.horizontalAdvance(candidate) > max_width_px:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines


class ScrollPassthroughSpinBox(QSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:
        if not _route_wheel_to_ancestor_scroll_area(self, event):
            event.ignore()


class ScrollPassthroughDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:
        if not _route_wheel_to_ancestor_scroll_area(self, event):
            event.ignore()


class PanelSizeDialog(QDialog):
    def __init__(self, current_size: str, language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate_gui_text("ui.custom.panel.size", language))
        self.setModal(True)
        self.setMinimumWidth(320)

        width, height = self._parse_size(current_size)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel(translate_gui_text("ui.set.single.canvas.size", language))
        title.setObjectName("DialogTitle")
        layout.addWidget(title)

        subtitle = QLabel(translate_gui_text("ui.applies.when.panel.size.set.custom", language))
        subtitle.setObjectName("SubtleText")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 10000)
        self.width_spin.setValue(width)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 10000)
        self.height_spin.setValue(height)

        grid.addWidget(QLabel(translate_gui_text("ui.width", language)), 0, 0)
        grid.addWidget(self.width_spin, 0, 1)
        grid.addWidget(QLabel(translate_gui_text("ui.height", language)), 1, 0)
        grid.addWidget(self.height_spin, 1, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return f"{self.width_spin.value()}x{self.height_spin.value()}"

    def _parse_size(self, value: str) -> tuple[int, int]:
        normalized = value.lower().replace("×", "x").strip()
        if "x" not in normalized:
            return 1240, 1860
        width_text, height_text = normalized.split("x", 1)
        try:
            width = max(1, int(width_text))
            height = max(1, int(height_text))
        except ValueError:
            return 1240, 1860
        return width, height


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._settings_store = GuiSettingsStore()
        self._loaded_state = self._settings_store.load()
        preferred_language = getattr(self._loaded_state, "language", None) or resolve_language()
        self._language = normalize_ui_language(preferred_language, default="zh")
        self._loaded_state.language = self._language
        self._worker_thread: QThread | None = None
        self._worker: BuildWorker | None = None
        self._last_detection: DetectionResult | None = None
        self._last_summary: RunSummary | None = None
        self._loading_state = False
        self._syncing_controls = False
        self._startup_complete = False
        self._help_tip_popup = HelpTipPopup()
        self._help_tip_anchor: QWidget | None = None
        self._preview_pixmap: QPixmap | None = None
        self._preview_canvas_base_min_height = 340
        self._preview_canvas_image_floor_height = 300
        self._preview_canvas_applied_height: int | None = None
        self._preview_summary_text = self._tr("ui.preview.appears.input.folder.detected")
        self._preview_hint_text = self._tr("ui.preview.refreshes.automatically.when.input.valid.parameters")
        self._preview_selected_source_dir: Path | None = None
        self._preview_requested_page_number: int | None = None
        self._preview_current_page_number: int | None = None
        self._preview_total_pages = 0
        self._preview_available_page_numbers: tuple[int, ...] = ()
        self._syncing_preview_controls = False
        self._output_auto = not bool(self._loaded_state.output_location.strip())
        self._gamma_auto = self._loaded_state.gamma_auto
        self._jpeg_quality_auto = self._loaded_state.jpeg_quality_auto
        self._previous_panel_preset = self._loaded_state.panel_preset

        default_profile = self._settings_store.load_default_profile_name()
        default_state = self._settings_store.get_profile(default_profile) if default_profile else None
        if default_state is not None:
            default_state.input_dir = self._loaded_state.input_dir
            default_state.output_location = self._loaded_state.output_location
            default_state.language = self._language
            self._loaded_state = default_state
            self._previous_panel_preset = self._loaded_state.panel_preset

        self.setWindowTitle("img2kpf GUI")
        self.resize(1280, 820)
        self.setMinimumSize(1080, 720)
        self._build_ui()
        self._build_profile_menu()
        self._apply_styles()
        self._apply_icons()
        self._wire_signals()
        self._load_state(self._loaded_state)
        self._apply_localization_if_needed()
        self._app_event_filter_installed = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_event_filter_installed = True
        QTimer.singleShot(0, self._mark_startup_complete)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_preview_pixmap()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)

        root_layout.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_scroll = QScrollArea()
        left_scroll.setObjectName("PanelScroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(430)
        left_scroll.viewport().setObjectName("PanelViewport")
        left_scroll.viewport().setAutoFillBackground(True)

        left_panel = QWidget()
        left_panel.setObjectName("LeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        left_layout.addWidget(self._build_input_card())
        left_layout.addWidget(self._build_parameter_tabs())
        left_layout.addStretch(1)
        left_scroll.setWidget(left_panel)

        right_panel = QWidget()
        right_panel.setObjectName("RightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(self._build_preview_card(), 4)
        right_layout.addWidget(self._build_run_card())
        right_layout.addWidget(self._build_status_card())
        right_layout.addWidget(self._build_log_card(), 1)

        self.right_scroll = QScrollArea()
        self.right_scroll.setObjectName("PanelScroll")
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.right_scroll.viewport().setObjectName("PanelViewport")
        self.right_scroll.viewport().setAutoFillBackground(True)
        self.right_scroll.setWidget(right_panel)

        splitter.addWidget(left_scroll)
        splitter.addWidget(self.right_scroll)
        splitter.setSizes([450, 920])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, 1)
        root_layout.addLayout(self._build_actions_row())

        self._detect_timer = QTimer(self)
        self._detect_timer.setSingleShot(True)
        self._detect_timer.setInterval(250)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(220)

    def _build_header(self) -> QFrame:
        card = self._card("HeroCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)

        title = QLabel("img2kpf Studio")
        title.setObjectName("HeroTitle")
        title_font = QFont()
        title_font.setPointSize(19)
        title_font.setBold(True)
        title.setFont(title_font)

        subtitle = QLabel(self._txt("ui.hero.steps"))
        subtitle.setObjectName("HeroSubtitle")

        self.next_step_label = QLabel(self._txt("ui.next.choose.input.folder"))
        self.next_step_label.setObjectName("NextStepLabel")

        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        title_block.addWidget(self.next_step_label)

        self.mode_badge = QLabel(self._txt("ui.waiting"))
        self.mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mode_badge.setMinimumWidth(64)
        self.mode_badge.setMaximumWidth(120)
        self.mode_badge.setMaximumHeight(28)
        self.mode_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.mode_badge.setObjectName("ModeBadge")

        self.language_button = QToolButton()
        self.language_button.setObjectName("LanguageButton")
        self.language_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.language_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.language_button.setToolTip(self._txt(LANGUAGE_TOOLTIP))
        self._language_popup: DropdownPopup | None = None

        status_column_width = 120

        self.mode_summary = QLabel(self._txt("ui.no.folder.selected"))
        self.mode_summary.setObjectName("ModeSummary")
        self.mode_summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mode_summary.setFixedWidth(status_column_width)
        self.mode_summary.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.mode_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.mode_summary.setVisible(False)

        status_column = QWidget()
        status_column.setFixedWidth(status_column_width)
        right = QVBoxLayout(status_column)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.addStretch(1)
        top_row.addWidget(self.language_button, 0, Qt.AlignmentFlag.AlignVCenter)
        top_row.addWidget(self.mode_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        right.addLayout(top_row)
        right.addWidget(self.mode_summary, 0, Qt.AlignmentFlag.AlignRight)

        layout.addLayout(title_block, 1)
        layout.addWidget(status_column, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        return card

    def _toggle_language_popup(self) -> None:
        if self._close_language_popup():
            return
        self._show_language_popup()

    def _show_language_popup(self) -> None:
        options = ui_language_options()
        if not options:
            return
        self._close_language_popup()

        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        if hasattr(Qt.WindowType, "NoDropShadowWindowHint"):
            flags |= Qt.WindowType.NoDropShadowWindowHint

        popup = DropdownPopup(self, flags)
        popup.setObjectName("ComboFloatingPopup")
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        outer_layout = QVBoxLayout(popup)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        panel = QFrame(popup)
        panel.setObjectName("ComboFloatingPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        scroll = QScrollArea(panel)
        scroll.setObjectName("ComboFloatingScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget(scroll)
        content.setObjectName("ComboFloatingContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(2)

        selected_language = normalize_ui_language(self._language, default="zh")
        for language, label in options:
            item_button = QPushButton(label, content)
            item_button.setObjectName("ComboPopupItem")
            item_button.setCursor(Qt.CursorShape.PointingHandCursor)
            item_button.setProperty("selected", language == selected_language)
            item_button.clicked.connect(
                lambda _checked=False, selected=language: self._on_language_popup_selected(selected)
            )
            content_layout.addWidget(item_button)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        panel_layout.addWidget(scroll)
        outer_layout.addWidget(panel)

        text_width = max(self.fontMetrics().horizontalAdvance(label) for _, label in options)
        popup_width = max(160, text_width + 58)
        popup_position = self.language_button.mapToGlobal(
            QPoint(self.language_button.width() - popup_width, self.language_button.height() + 6)
        )
        screen = QApplication.screenAt(popup_position) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        visible_items = min(len(options), 7)
        button_height = 42
        popup_height = visible_items * button_height + 20
        if available is not None:
            popup_height = min(popup_height, max(180, int(available.height() * 0.48)))
        scroll.setFixedHeight(popup_height)
        popup.setFixedWidth(popup_width)
        popup.adjustSize()
        popup_height = popup.sizeHint().height()

        if available is not None:
            if popup_position.x() + popup_width > available.right():
                popup_position.setX(max(available.left(), available.right() - popup_width))
            if popup_position.x() < available.left():
                popup_position.setX(available.left())
            if popup_position.y() + popup_height > available.bottom():
                upward_y = self.language_button.mapToGlobal(QPoint(0, -popup_height - 6)).y()
                popup_position.setY(max(available.top(), upward_y))

        popup.move(popup_position)
        popup.destroyed.connect(lambda: setattr(self, "_language_popup", None))
        self._language_popup = popup
        popup.show()

    def _on_language_popup_selected(self, language: str) -> None:
        self._close_language_popup()
        self._on_language_selected(language)

    def _close_language_popup(self) -> bool:
        popup = getattr(self, "_language_popup", None)
        if popup is None:
            return False
        self._language_popup = None
        popup.close()
        popup.deleteLater()
        return True

    def _build_input_card(self) -> QFrame:
        card = self._section_card(self._txt("ui.input.output"))
        layout = card.layout()

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(self._txt("ui.choose.input.folder"))
        self.input_edit.setClearButtonEnabled(True)
        self.input_edit.setToolTip(
            self._txt(
                "ui.select.image.folder.single.root.contains.jpg",
            )
        )
        self.input_browse_button = QPushButton(self._txt("ui.action.browse"))
        self.detect_button = QPushButton(self._txt("ui.action.detect"))
        input_row = self._inline_row(self.input_edit, self.input_browse_button, self.detect_button)
        layout.addWidget(self._field(self._txt("ui.input.folder"), input_row, self.input_edit.toolTip()))

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText(self._txt("ui.auto.suggested.output"))
        self.output_edit.setClearButtonEnabled(True)
        self.output_browse_button = QPushButton(self._txt("ui.action.browse"))
        self.output_label = QLabel(self._txt("ui.status.output_location"))
        output_row = self._inline_row(self.output_edit, self.output_browse_button)
        layout.addWidget(
            self._field(
                self._txt("ui.status.output_location"),
                output_row,
                self._txt(
                    "ui.single.outputs.kpf.file.batch.outputs.directory",
                ),
            )
        )

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(self._txt("ui.optional.blank.uses.folder.name"))
        self.title_edit.setClearButtonEnabled(True)
        self.title_edit.setToolTip(
            self._cli_tip("title")
        )
        layout.addWidget(self._field(self._txt("ui.title"), self.title_edit, self.title_edit.toolTip()))
        return card

    def _build_parameter_tabs(self) -> QFrame:
        card = self._section_card(self._txt("ui.settings"))
        layout = card.layout()

        subtitle = QLabel(
            self._txt(
                "ui.common.key.options.advanced.quality.compatibility.tuning",
            )
        )
        subtitle.setObjectName("SubtleText")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.parameter_tabs = QTabWidget()
        self.parameter_tabs.setObjectName("ParameterTabs")
        self.parameter_tabs.tabBar().setDrawBase(False)
        self.parameter_tabs.addTab(self._build_common_tab(), self._txt("ui.common"))
        self.parameter_tabs.addTab(self._build_advanced_tab(), self._txt("ui.advanced"))
        layout.addWidget(self.parameter_tabs)
        return card

    def _build_common_tab(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("TabPageContent")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        self.image_preset_combo = self._build_option_combo(IMAGE_PRESET_OPTIONS)
        self.image_preset_combo.setToolTip(self._cli_tip("image_preset"))
        layout.addWidget(self._field(self._txt("ui.image.preset"), self.image_preset_combo, self._cli_tip("image_preset")))

        self.crop_mode_combo = self._build_option_combo(CROP_MODE_OPTIONS)
        self.crop_mode_combo.setToolTip(self._cli_tip("crop_mode"))
        layout.addWidget(self._field(self._txt("ui.crop"), self.crop_mode_combo, self._cli_tip("crop_mode")))

        self.reading_direction_combo = self._build_option_combo(READING_DIRECTION_OPTIONS)
        self.reading_direction_combo.setToolTip(READING_DIRECTION_TOOLTIP)
        layout.addWidget(self._field(self._txt("ui.reading.direction"), self.reading_direction_combo, self._txt(READING_DIRECTION_TOOLTIP)))

        self.page_layout_combo = self._build_option_combo(PAGE_LAYOUT_OPTIONS)
        self.page_layout_combo.setToolTip(PAGE_LAYOUT_TOOLTIP)
        layout.addWidget(self._field(self._txt("ui.layout"), self.page_layout_combo, self._txt(PAGE_LAYOUT_TOOLTIP)))

        self.virtual_panels_combo = self._build_option_combo(VIRTUAL_PANELS_OPTIONS)
        self.virtual_panels_combo.setToolTip(VIRTUAL_PANELS_TOOLTIP)
        layout.addWidget(self._field(self._txt("ui.virtual.panels"), self.virtual_panels_combo, self._txt(VIRTUAL_PANELS_TOOLTIP)))

        self.panel_movement_combo = self._build_option_combo(PANEL_MOVEMENT_OPTIONS)
        self.panel_movement_combo.setToolTip(PANEL_MOVEMENT_TOOLTIP)
        self.panel_movement_field = self._field(
            self._txt("ui.panel.movement"),
            self.panel_movement_combo,
            self._txt(PANEL_MOVEMENT_TOOLTIP),
        )
        layout.addWidget(self.panel_movement_field)

        self.panel_preset_combo = self._build_option_combo(PANEL_PRESET_OPTIONS)
        self.panel_preset_combo.setToolTip(
            self._txt(
                "ui.choose.target.device.panel.size.custom.size",
            )
        )
        layout.addWidget(self._field(self._txt("ui.panel.size"), self.panel_preset_combo, self.panel_preset_combo.toolTip()))

        self.shift_mode_combo = self._build_option_combo(SHIFT_MODE_OPTIONS)
        self.shift_mode_combo.setToolTip(self._cli_tip("shift"))
        layout.addWidget(self._field(self._txt("ui.first.shift"), self.shift_mode_combo, self._cli_tip("shift")))

        self.output_format_combo = self._build_option_combo(OUTPUT_FORMAT_OPTIONS)
        self.output_format_combo.setToolTip(
            self._txt(
                "ui.choose.output.format.available.kpf.kpf.kfx",
            )
        )
        layout.addWidget(self._field(self._txt("ui.output.format"), self.output_format_combo, self.output_format_combo.toolTip()))

        profile_tile = QFrame()
        profile_tile.setObjectName("FieldTile")
        profile_layout = QVBoxLayout(profile_tile)
        profile_layout.setContentsMargins(12, 10, 12, 12)
        profile_layout.setSpacing(8)

        profile_header = QHBoxLayout()
        profile_label = QLabel(self._txt("ui.profiles"))
        profile_label.setObjectName("FieldLabel")
        self.save_profile_button = QToolButton()
        self.save_profile_button.setText(self._txt("ui.new"))
        self.save_profile_button.setToolTip(self._txt("ui.save.settings.new.profile"))
        self.save_profile_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.load_profile_button = QToolButton()
        self.load_profile_button.setText(self._txt("ui.load"))
        self.load_profile_button.setToolTip(self._txt("ui.load.selected.profile"))
        self.load_profile_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.profile_actions_button = QToolButton()
        self.profile_actions_button.setObjectName("ActionMenuButton")
        self.profile_actions_button.setText(self._txt("ui.actions"))
        self.profile_actions_button.setToolTip(
            self._txt(
                "ui.perform.load.overwrite.delete.default.actions.selected",
            )
        )
        self.profile_actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.profile_actions_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for button in (self.save_profile_button, self.load_profile_button, self.profile_actions_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        profile_header.addWidget(profile_label)
        profile_header.addStretch(1)

        self.profile_combo = self._build_option_combo(())
        self.profile_combo.setPlaceholderText(self._txt("ui.no.saved.profiles"))
        self.profile_combo.setToolTip(self._txt("ui.select.saved.profile"))
        self.profile_status_label = QLabel()
        self.profile_status_label.setObjectName("SubtleText")
        self.profile_status_label.setWordWrap(True)

        profile_actions = QHBoxLayout()
        profile_actions.setContentsMargins(0, 0, 0, 0)
        profile_actions.setSpacing(6)
        profile_actions.addWidget(self.save_profile_button)
        profile_actions.addWidget(self.load_profile_button)
        profile_actions.addWidget(self.profile_actions_button)
        profile_layout.addLayout(profile_header)
        profile_layout.addWidget(self.profile_combo)
        profile_layout.addLayout(profile_actions)
        profile_layout.addWidget(self.profile_status_label)
        layout.addWidget(profile_tile)

        layout.addStretch(1)
        return widget

    def _build_advanced_tab(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("TabPageContent")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        self.target_size_edit = QLineEdit()
        self.target_size_edit.setPlaceholderText(self._txt("ui.follow.panel.preset"))
        self.target_size_edit.setReadOnly(True)
        self.target_size_edit.setToolTip(self._cli_tip("target_size"))
        self.target_size_button = QToolButton()
        self.target_size_button.setObjectName("InlineActionButton")
        self.target_size_button.setText(self._txt("ui.set"))
        self.target_size_button.setToolTip(self._txt("ui.open.custom.size.dialog"))
        self.target_size_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(
            self._field(
                self._txt("ui.custom.size.label"),
                self._inline_row(self.target_size_edit, self.target_size_button),
                self._cli_tip("target_size"),
            )
        )

        self.preserve_color_combo = self._build_option_combo(TRI_STATE_OPTIONS)
        self.preserve_color_combo.setToolTip(self._cli_tip("preserve_color"))
        layout.addWidget(self._field(self._txt("ui.preserve.color"), self.preserve_color_combo, self._cli_tip("preserve_color")))

        self.gamma_spin = ScrollPassthroughDoubleSpinBox()
        self.gamma_spin.setDecimals(2)
        self.gamma_spin.setRange(0.1, 8.0)
        self.gamma_spin.setSingleStep(0.1)
        self.gamma_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.gamma_spin.setToolTip(self._cli_tip("gamma"))
        self.gamma_reset_button = QToolButton()
        self.gamma_reset_button.setObjectName("InlineActionButton")
        self.gamma_reset_button.setText(self._txt("ui.preset"))
        self.gamma_reset_button.setToolTip(
            self._txt("ui.reset.default.gamma.image.preset")
        )
        self.gamma_reset_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(
            self._field(
                "Gamma",
                self._inline_row(self.gamma_spin, self.gamma_reset_button),
                self._cli_tip("gamma"),
            )
        )

        self.jpeg_quality_spin = ScrollPassthroughSpinBox()
        self.jpeg_quality_spin.setRange(1, 100)
        self.jpeg_quality_spin.setSingleStep(1)
        self.jpeg_quality_spin.setToolTip(self._cli_tip("jpeg_quality"))
        self.quality_reset_button = QToolButton()
        self.quality_reset_button.setObjectName("InlineActionButton")
        self.quality_reset_button.setText(self._txt("ui.preset"))
        self.quality_reset_button.setToolTip(
            self._txt("ui.reset.default.jpeg.quality.image.preset")
        )
        self.quality_reset_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(
            self._field(
                self._txt("ui.jpeg.quality"),
                self._inline_row(self.jpeg_quality_spin, self.quality_reset_button),
                self._cli_tip("jpeg_quality"),
            )
        )

        self.autocontrast_combo = self._build_option_combo(TRI_STATE_OPTIONS)
        self.autocontrast_combo.setToolTip(self._cli_tip("autocontrast"))
        layout.addWidget(self._field(self._txt("ui.auto.contrast"), self.autocontrast_combo, self._cli_tip("autocontrast")))

        self.autolevel_combo = self._build_option_combo(TRI_STATE_OPTIONS)
        self.autolevel_combo.setToolTip(self._cli_tip("autolevel"))
        layout.addWidget(self._field(self._txt("ui.light.black.boost"), self.autolevel_combo, self._cli_tip("autolevel")))

        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText(self._txt("ui.optional.template.kpf.zip"))
        self.template_edit.setToolTip(self._cli_tip("template"))
        self.template_edit.setClearButtonEnabled(True)
        self.template_browse_button = QPushButton(self._txt("ui.action.browse"))
        self.template_field = self._field(
            self._txt("ui.template.file"),
            self._inline_row(self.template_edit, self.template_browse_button),
            self._cli_tip("template"),
        )
        self.template_field.setVisible(False)
        layout.addWidget(self.template_field)

        self.kfx_plugin_edit = QLineEdit()
        self.kfx_plugin_edit.setPlaceholderText(self._txt("ui.default.kfx.output"))
        self.kfx_plugin_edit.setToolTip(self._cli_tip("kfx_plugin"))
        self.kfx_plugin_edit.setClearButtonEnabled(True)
        layout.addWidget(self._field(self._txt("ui.kfx.plugin"), self.kfx_plugin_edit, self._cli_tip("kfx_plugin")))

        self.jobs_spin = ScrollPassthroughSpinBox()
        self.jobs_spin.setRange(1, 64)
        self.jobs_spin.setToolTip(self._cli_tip("jobs"))
        layout.addWidget(self._field(self._txt("ui.parallel.volumes"), self.jobs_spin, self._cli_tip("jobs")))

        layout.addStretch(1)
        return widget

    def _build_preview_card(self) -> QFrame:
        card = self._section_card(self._txt("ui.live.preview"))
        layout = card.layout()

        self.preview_info_label = QLabel(
            self._txt(
                "ui.preview.always.uses.10.facing.automatically.includes",
            )
        )
        self.preview_info_label.setObjectName("SubtleText")
        self.preview_info_label.setWordWrap(True)
        self.preview_info_label.setVisible(False)

        self.preview_crop_checkbox = QCheckBox(self._txt("ui.crop.box"))
        self.preview_crop_checkbox.setObjectName("InlineCheck")
        self._set_help_tip(
            self.preview_crop_checkbox,
            self._txt(
                "ui.show.source.image.red.crop.box.off",
            ),
        )

        self.preview_volume_row = QWidget()
        volume_layout = QHBoxLayout(self.preview_volume_row)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(8)

        self.preview_volume_label = QLabel(self._txt("ui.preview.volume"))
        self.preview_volume_label.setObjectName("FieldLabel")
        self.preview_volume_label.setVisible(False)
        self.preview_volume_combo = self._build_option_combo(())
        self.preview_volume_combo.setToolTip(self._txt("ui.preview.volume.tooltip"))

        volume_layout.addWidget(self.preview_volume_combo, 1)
        self.preview_volume_row.setVisible(False)

        self.preview_canvas_label = QLabel(
            self._txt("ui.preview.appears.input.folder.detected")
        )
        self.preview_canvas_label.setObjectName("PreviewCanvas")
        self.preview_canvas_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_canvas_label.setWordWrap(True)
        self.preview_canvas_label.setMinimumHeight(self._preview_canvas_base_min_height)
        self.preview_canvas_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.preview_hint_label = QLabel(
            self._txt(
                "ui.preview.reflects.crop.color.single.facing.rtl",
            )
        )
        self.preview_hint_label.setObjectName("SubtleText")
        self.preview_hint_label.setWordWrap(True)
        self.preview_hint_label.setVisible(False)

        self.preview_body_widget = QWidget()
        self.preview_body_widget.setObjectName("PreviewBody")
        self.preview_body_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_body_layout = QVBoxLayout(self.preview_body_widget)
        preview_body_layout.setContentsMargins(0, 0, 0, 0)
        preview_body_layout.setSpacing(8)
        preview_body_layout.addWidget(self.preview_volume_row)
        preview_body_layout.addWidget(self.preview_canvas_label, 1)
        preview_body_layout.addWidget(self.preview_hint_label)

        self.preview_nav_row = QWidget()
        nav_layout = QHBoxLayout(self.preview_nav_row)
        nav_layout.setContentsMargins(0, 4, 0, 0)
        nav_layout.setSpacing(10)

        self.preview_prev_button = QPushButton("←")
        self.preview_prev_button.setObjectName("InlineActionButton")
        self.preview_prev_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_prev_button.setMinimumWidth(54)

        self.preview_page_spin = PreviewPageSpinBox()
        self.preview_page_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.preview_page_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_page_spin.setKeyboardTracking(False)
        self.preview_page_spin.setRange(1, 1)
        self.preview_page_spin.setValue(1)
        self.preview_page_spin.setMinimumWidth(74)
        self.preview_page_spin.setToolTip(self._txt("ui.preview.page.jump"))

        self.preview_total_label = QLabel("/ 0")
        self.preview_total_label.setObjectName("SubtleText")
        self.preview_total_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        page_container = QWidget()
        page_layout = QHBoxLayout(page_container)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(6)
        page_layout.addWidget(self.preview_page_spin)
        page_layout.addWidget(self.preview_total_label)

        self.preview_next_button = QPushButton("→")
        self.preview_next_button.setObjectName("InlineActionButton")
        self.preview_next_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_next_button.setMinimumWidth(54)

        crop_slot = QWidget()
        crop_slot_layout = QHBoxLayout(crop_slot)
        crop_slot_layout.setContentsMargins(0, 0, 0, 0)
        crop_slot_layout.setSpacing(0)
        crop_slot_layout.addWidget(self.preview_crop_checkbox, 0, Qt.AlignmentFlag.AlignCenter)
        crop_slot.setFixedWidth(180)

        nav_balance_slot = QWidget()
        nav_balance_slot.setFixedWidth(180)

        nav_layout.addStretch(1)
        nav_layout.addWidget(nav_balance_slot)
        nav_layout.addWidget(self.preview_prev_button)
        nav_layout.addWidget(page_container)
        nav_layout.addWidget(self.preview_next_button)
        nav_layout.addWidget(crop_slot)
        nav_layout.addStretch(1)
        preview_body_layout.addWidget(self.preview_nav_row)
        self._update_preview_navigation_controls()

        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.preview_body_widget)
        return card

    def _build_run_card(self) -> QFrame:
        card = self._section_card(self._txt("ui.task"))
        layout = card.layout()

        self.action_summary_label = QLabel(self._txt("ui.please.choose.input.folder"))
        self.action_summary_label.setObjectName("ActionSummary")
        self.action_summary_label.setWordWrap(True)
        self.action_summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.structure_hint_label = QLabel(
            self._txt(
                "ui.single.choose.folder.containing.images.batch.choose",
            )
        )
        self.structure_hint_label.setObjectName("SubtleText")
        self.structure_hint_label.setWordWrap(True)

        layout.addWidget(self.action_summary_label)
        layout.addWidget(self.structure_hint_label)
        return card

    def _build_status_card(self) -> QFrame:
        card = self._section_card(self._txt("ui.status.title"))
        layout = card.layout()

        self.status_label = QLabel(self._txt("ui.waiting.start"))
        self.status_label.setObjectName("StatusLabel")

        self.progress_detail_label = QLabel(self._txt("ui.not.started"))
        self.progress_detail_label.setObjectName("SubtleText")
        self.progress_detail_label.setWordWrap(True)
        self.progress_detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        self.result_summary = QLabel(self._txt("ui.result.summary.appears.completion"))
        self.result_summary.setObjectName("SubtleText")
        self.result_summary.setWordWrap(True)
        self.result_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)

        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_detail_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.result_summary)
        return card

    def _build_log_card(self) -> QFrame:
        card = self._section_card(self._txt("ui.logs"))
        layout = card.layout()

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        mono_font = QFont("Menlo")
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.log_edit.setFont(mono_font)
        self.log_edit.setPlaceholderText(self._txt("ui.live.logs.appear.clicking.start"))
        layout.addWidget(self.log_edit, 1)
        return card

    def _build_actions_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.start_button = QPushButton(self._txt("ui.action.start"))
        self.start_button.setObjectName("PrimaryButton")
        self.stop_button = QPushButton(self._txt("ui.action.stop"))
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setEnabled(False)
        self.open_output_button = QPushButton(self._txt("ui.action.open_output"))
        self.open_output_button.setObjectName("SecondaryButton")
        self.open_output_button.setEnabled(False)
        self.clear_log_button = QPushButton(self._txt("ui.action.clear_logs"))
        self.clear_log_button.setObjectName("SecondaryButton")

        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        row.addWidget(self.open_output_button)
        row.addWidget(self.clear_log_button)
        return row

    def _card(self, object_name: str = "Card") -> QFrame:
        card = QFrame()
        card.setObjectName(object_name)
        return card

    def _section_card(self, title: str) -> QFrame:
        card = self._card("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        label = QLabel(title)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        return card

    def _field(self, title: str, control: QWidget, tooltip: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("FieldTile")
        layout = QHBoxLayout(tile)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        title_container = QWidget()
        title_container.setMinimumWidth(104)
        title_container.setMaximumWidth(160)
        header = QHBoxLayout(title_container)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        label = QLabel(title)
        label.setObjectName("FieldLabel")
        label.setWordWrap(True)
        self._set_help_tip(label, tooltip)

        help_button = QToolButton()
        help_button.setObjectName("HelpButton")
        help_button.setText("?")
        help_button.setAutoRaise(True)
        help_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        help_button.setCursor(Qt.CursorShape.WhatsThisCursor)
        self._set_help_tip(help_button, tooltip)

        header.addWidget(label)
        header.addWidget(help_button)

        control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(title_container, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(control, 1)
        return tile

    def _inline_row(self, *widgets: QWidget) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for index, widget in enumerate(widgets):
            layout.addWidget(widget, 1 if index == 0 else 0)
        return container

    def _set_help_tip(self, widget: QWidget, text: str) -> None:
        widget.setProperty("HelpTipRawText", text)
        widget.setProperty("HelpTipText", self._tr(text))
        widget.setToolTip("")
        widget.installEventFilter(self)

    def _tr(self, text: str, **kwargs: object) -> str:
        return translate_gui_text(text, self._language, **kwargs)

    def _txt(self, key: str, **kwargs: object) -> str:
        return self._tr(key, **kwargs)

    def _list_separator(self) -> str:
        return self._tr("ui.list.separator")

    def _join_name_list(self, names: list[str], limit: int | None = None) -> str:
        target = names[:limit] if limit is not None else names
        joined = self._list_separator().join(target)
        if limit is not None and len(names) > limit:
            joined += self._tr("ui.list.ellipsis")
        return joined

    def _cli_tip(self, dest: str) -> str:
        return self._tr(CLI_PARAMETER_INFO[dest].tooltip)

    def _refresh_preview_ui_texts(self) -> None:
        if not hasattr(self, "preview_volume_label"):
            return
        self.preview_volume_label.setText(self._txt("ui.preview.volume"))
        self.preview_volume_combo.setToolTip(self._txt("ui.preview.volume.tooltip"))
        if self._preview_is_rtl():
            self.preview_prev_button.setToolTip(self._txt("ui.preview.next"))
            self.preview_next_button.setToolTip(self._txt("ui.preview.prev"))
        else:
            self.preview_prev_button.setToolTip(self._txt("ui.preview.prev"))
            self.preview_next_button.setToolTip(self._txt("ui.preview.next"))
        self.preview_page_spin.setToolTip(self._txt("ui.preview.page.jump"))

    def _apply_localization_if_needed(self) -> None:
        if self._language == "zh":
            return
        if hasattr(self, "profile_load_action"):
            self.profile_load_action.setText(self._tr("ui.load.profile"))
        if hasattr(self, "profile_overwrite_action"):
            self.profile_overwrite_action.setText(self._tr("ui.overwrite.settings"))
        if hasattr(self, "profile_set_default_action"):
            self.profile_set_default_action.setText(self._tr("ui.set.default.profile"))
        if hasattr(self, "profile_clear_default_action"):
            self.profile_clear_default_action.setText(self._tr("ui.clear.default.profile"))
        if hasattr(self, "profile_delete_action"):
            self.profile_delete_action.setText(self._tr("ui.delete.profile"))
        self._localize_widget_tree(self)
        self._localize_widget_tree(self._help_tip_popup)
        self._refresh_preview_ui_texts()

    def _localize_widget_tree(self, root: QWidget) -> None:
        root.setWindowTitle(self._tr(root.windowTitle()))

        if isinstance(root, QLabel):
            root.setText(self._tr(root.text()))
        if isinstance(root, (QPushButton, QToolButton, QCheckBox)):
            root.setText(self._tr(root.text()))
        if isinstance(root, QLineEdit):
            root.setPlaceholderText(self._tr(root.placeholderText()))
        if isinstance(root, QPlainTextEdit):
            root.setPlaceholderText(self._tr(root.placeholderText()))
        if isinstance(root, QComboBox):
            root.setPlaceholderText(self._tr(root.placeholderText()))
            for index in range(root.count()):
                root.setItemText(index, self._tr(root.itemText(index)))
        if isinstance(root, QTabWidget):
            for index in range(root.count()):
                root.setTabText(index, self._tr(root.tabText(index)))

        tooltip = root.toolTip()
        if tooltip:
            root.setToolTip(self._tr(tooltip))
        help_tip = root.property("HelpTipText")
        if isinstance(help_tip, str) and help_tip:
            raw_help_tip = root.property("HelpTipRawText")
            if isinstance(raw_help_tip, str) and raw_help_tip:
                root.setProperty("HelpTipText", self._tr(raw_help_tip))
            else:
                root.setProperty("HelpTipText", self._tr(help_tip))

        for child in root.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            self._localize_widget_tree(child)

    def _hide_help_tip(self) -> None:
        self._help_tip_anchor = None
        self._help_tip_popup.hide()

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.Type.Resize
            and isinstance(watched, QWidget)
            and watched.objectName() == "PreviewCanvas"
        ):
            QTimer.singleShot(0, self._apply_preview_pixmap)
        if isinstance(watched, QWidget):
            help_text = watched.property("HelpTipText")
            if isinstance(help_text, str) and help_text:
                event_type = event.type()
                if event_type == QEvent.Type.Enter:
                    self._help_tip_anchor = watched
                    self._help_tip_popup.show_for(watched, help_text)
                elif event_type == QEvent.Type.ToolTip:
                    return True
                elif event_type in {
                    QEvent.Type.Leave,
                    QEvent.Type.Hide,
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.FocusOut,
                }:
                    if self._help_tip_anchor is watched:
                        self._hide_help_tip()
            elif self._help_tip_popup.isVisible():
                event_type = event.type()
                if event_type in {
                    QEvent.Type.Enter,
                    QEvent.Type.MouseMove,
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.Wheel,
                    QEvent.Type.KeyPress,
                    QEvent.Type.WindowDeactivate,
                }:
                    anchor = self._help_tip_anchor
                    try:
                        anchor_visible = anchor is not None and anchor.isVisible()
                    except RuntimeError:
                        anchor_visible = False
                    if not anchor_visible:
                        self._hide_help_tip()
                    else:
                        try:
                            cursor_pos = QCursor.pos()
                            local_pos = anchor.mapFromGlobal(cursor_pos)
                            inside_anchor = anchor.rect().contains(local_pos)
                        except RuntimeError:
                            inside_anchor = False
                        if not inside_anchor:
                            self._hide_help_tip()
        return super().eventFilter(watched, event)

    def _apply_styles(self) -> None:
        chevron_path = Path(__file__).resolve().parents[1] / "assets" / "gui" / "chevron_down.svg"
        checkmark_path = Path(__file__).resolve().parents[1] / "assets" / "gui" / "checkmark_blue.svg"
        stylesheet = (
            """
            QMainWindow {
                background: #f2f2f7;
            }
            QToolTip {
                color: #1d1d1f;
                background: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QWidget#HelpTipPopup {
                background: transparent;
                border: 0;
            }
            QFrame#HelpTipPanel {
                background: #ffffff;
                border: 1px solid #c9c9d2;
                border-radius: 13px;
            }
            QLabel#HelpTipText {
                color: #1d1d1f;
                font-size: 13px;
                font-weight: 650;
            }
            QScrollArea#PanelScroll, QWidget#LeftPanel, QWidget#RightPanel {
                background: transparent;
            }
            QWidget#PanelViewport {
                background: #f2f2f7;
            }
            QWidget#TabPageContent {
                background: transparent;
            }
            QScrollArea#PanelScroll QScrollBar:vertical {
                background: transparent;
                border: 0;
                width: 10px;
                margin: 2px 0;
            }
            QScrollArea#PanelScroll QScrollBar::handle:vertical {
                background: #c7c7cc;
                border-radius: 5px;
                min-height: 32px;
            }
            QScrollArea#PanelScroll QScrollBar::handle:vertical:hover {
                background: #aeaeb2;
            }
            QScrollArea#PanelScroll QScrollBar::add-line:vertical,
            QScrollArea#PanelScroll QScrollBar::sub-line:vertical,
            QScrollArea#PanelScroll QScrollBar::add-page:vertical,
            QScrollArea#PanelScroll QScrollBar::sub-page:vertical {
                background: transparent;
                border: 0;
                height: 0;
            }
            QScrollArea#PanelScroll QScrollBar:horizontal {
                background: transparent;
                border: 0;
                height: 10px;
                margin: 0 2px;
            }
            QScrollArea#PanelScroll QScrollBar::handle:horizontal {
                background: #c7c7cc;
                border-radius: 5px;
                min-width: 32px;
            }
            QScrollArea#PanelScroll QScrollBar::add-line:horizontal,
            QScrollArea#PanelScroll QScrollBar::sub-line:horizontal,
            QScrollArea#PanelScroll QScrollBar::add-page:horizontal,
            QScrollArea#PanelScroll QScrollBar::sub-page:horizontal {
                background: transparent;
                border: 0;
                width: 0;
            }
            QFrame#HeroCard, QFrame#Card {
                background: #ffffff;
                border: 1px solid #e8e8ed;
                border-radius: 16px;
            }
            QFrame#HeroCard {
                background: #ffffff;
            }
            QFrame#FieldTile {
                background: #fbfbfd;
                border: 1px solid #ececf0;
                border-radius: 12px;
            }
            QSplitter::handle {
                background: #d9d9e0;
                margin: 12px 0;
            }
            QLabel#HeroTitle {
                color: #1d1d1f;
            }
            QLabel#HeroSubtitle, QLabel#ModeSummary, QLabel#SubtleText {
                color: #6e6e73;
            }
            QLabel#ModeSummary {
                font-size: 12px;
                font-weight: 650;
            }
            QLabel#NextStepLabel {
                color: #007aff;
                font-weight: 600;
            }
            QLabel#ModeBadge {
                padding: 4px 14px;
                border-radius: 13px;
                font-size: 12px;
                font-weight: 700;
                background: #f4f6fb;
                color: #5b6474;
            }
            QLabel#SectionTitle {
                color: #1d1d1f;
                font-size: 15px;
                font-weight: 800;
            }
            QLabel#FieldLabel {
                color: #1d1d1f;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#ActionSummary {
                color: #1d1d1f;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#StatusLabel {
                color: #1d1d1f;
                font-size: 15px;
                font-weight: 800;
            }
            QLabel#PreviewCanvas {
                color: #6e6e73;
                background: #ffffff;
                border: 1px solid #eceef4;
                border-radius: 12px;
                padding: 0;
            }
            QLabel#DialogTitle {
                color: #1d1d1f;
                font-size: 15px;
                font-weight: 800;
            }
            QToolButton#LanguageButton {
                color: #6e6e73;
                background: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 11px;
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
                padding: 0;
            }
            QToolButton#LanguageButton:hover {
                background: #f7f7fa;
                border-color: #c7c7cc;
            }
            QToolButton#LanguageButton:pressed,
            QToolButton#LanguageButton:open {
                background: #eef4ff;
                border-color: #c9d9ff;
            }
            QToolButton#LanguageButton::menu-indicator {
                image: none;
                width: 0;
                height: 0;
            }
            QToolButton#HelpButton {
                color: #6e6e73;
                background: #f2f2f7;
                border: 1px solid #d1d1d6;
                border-radius: 9px;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                padding: 0;
                font-weight: 700;
            }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
                color: #1d1d1f;
                background: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 9px;
                padding: 8px 10px;
                min-height: 22px;
            }
            QLineEdit:read-only {
                color: #6e6e73;
                background: #f7f7fa;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
                border: 1px solid #007aff;
            }
            QComboBox {
                background: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 10px;
                padding: 8px 34px 8px 12px;
                min-height: 22px;
            }
            QComboBox:hover {
                background: #fbfbfd;
                border-color: #c7c7cc;
            }
            QComboBox:on {
                background: #f7f7fa;
                border-color: #b8b8bf;
            }
            QComboBox::drop-down {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 32px;
                border: 0;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: url(__CHEVRON_PATH__);
                width: 12px;
                height: 8px;
            }
            QComboBox QAbstractItemView, QListView#ComboPopup {
                color: #1d1d1f;
                background: #ffffff;
                selection-color: #0b57d0;
                selection-background-color: #eef4ff;
                border: 1px solid #d8d8de;
                border-radius: 12px;
                outline: 0;
                padding: 6px;
            }
            QListView#ComboPopup::item {
                min-height: 30px;
                padding: 7px 12px;
                border-radius: 8px;
                margin: 1px 0;
            }
            QListView#ComboPopup::item:hover {
                background: #f2f2f7;
                color: #1d1d1f;
            }
            QListView#ComboPopup::item:selected {
                background: #eef4ff;
                color: #0b57d0;
            }
            QWidget#ComboFloatingPopup {
                background: transparent;
                border: 0;
            }
            QFrame#ComboFloatingPanel {
                background: #ffffff;
                border: 1px solid #d8d8de;
                border-radius: 14px;
            }
            QScrollArea#ComboFloatingScroll {
                background: transparent;
                border: 0;
            }
            QWidget#ComboFloatingContent {
                background: transparent;
            }
            QScrollArea#ComboFloatingScroll QScrollBar:vertical {
                background: transparent;
                border: 0;
                width: 8px;
                margin: 10px 4px 10px 0;
            }
            QScrollArea#ComboFloatingScroll QScrollBar::handle:vertical {
                background: #cbd3df;
                border-radius: 4px;
                min-height: 28px;
            }
            QScrollArea#ComboFloatingScroll QScrollBar::add-line:vertical,
            QScrollArea#ComboFloatingScroll QScrollBar::sub-line:vertical,
            QScrollArea#ComboFloatingScroll QScrollBar::add-page:vertical,
            QScrollArea#ComboFloatingScroll QScrollBar::sub-page:vertical {
                background: transparent;
                border: 0;
                height: 0;
            }
            QPushButton#ComboPopupItem {
                color: #1d1d1f;
                background: transparent;
                border: 0;
                border-radius: 9px;
                min-height: 40px;
                padding: 0 16px;
                text-align: left;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton#ComboPopupItem:hover {
                background: #f2f2f7;
            }
            QPushButton#ComboPopupItem[selected="true"] {
                background: #eef4ff;
                color: #0b57d0;
            }
            QTabWidget::pane {
                border: 0;
                background: transparent;
                margin-top: 12px;
            }
            QTabWidget#ParameterTabs {
                background: transparent;
            }
            QTabBar {
                background: transparent;
                qproperty-drawBase: 0;
            }
            QTabBar::tab {
                background: #ebebf0;
                color: #48484a;
                border: 1px solid #d1d1d6;
                padding: 8px 16px;
                margin-right: 6px;
                border-radius: 11px;
                font-weight: 650;
            }
            QTabBar::tab:hover {
                background: #f4f4f7;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #1d1d1f;
                border-color: #c7c7cc;
            }
            QPushButton, QToolButton {
                color: #1d1d1f;
                background: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 9px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QToolButton#ActionMenuButton, QToolButton#InlineActionButton, QPushButton#InlineActionButton {
                background: #f7f7fa;
                min-width: 72px;
            }
            QToolButton#InlineActionButton:checked, QPushButton#InlineActionButton:checked {
                background: #eef4ff;
                color: #0b57d0;
                border-color: #c9d9ff;
            }
            QCheckBox#InlineCheck {
                color: #1d1d1f;
                font-weight: 600;
                spacing: 6px;
                padding: 2px 0;
                background: transparent;
                border: 0;
            }
            QCheckBox#InlineCheck::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #c7c7cc;
                border-radius: 5px;
                background: #ffffff;
            }
            QCheckBox#InlineCheck::indicator:hover {
                border-color: #8ab4ff;
            }
            QCheckBox#InlineCheck::indicator:unchecked {
                image: none;
                border-color: #c7c7cc;
                background: #ffffff;
            }
            QCheckBox#InlineCheck::indicator:checked {
                image: url(__CHECKMARK_PATH__);
                border-color: #007aff;
                background: #ffffff;
            }
            QPushButton#PrimaryButton {
                background: #007aff;
                color: #ffffff;
                border-color: #007aff;
            }
            QPushButton#PrimaryButton:hover {
                background: #006ee6;
            }
            QPushButton#SecondaryButton {
                background: #ffffff;
            }
            QPushButton#DangerButton {
                background: #fff5f5;
                color: #ff3b30;
                border-color: #ffd1ce;
            }
            QPushButton:disabled, QToolButton:disabled {
                color: #a1a1a6;
                background: #f2f2f7;
                border-color: #e5e5ea;
            }
            QProgressBar {
                background: #e5e5ea;
                border: 1px solid #d1d1d6;
                border-radius: 9px;
                text-align: center;
                min-height: 16px;
            }
            QProgressBar::chunk {
                background: #007aff;
                border-radius: 8px;
            }
            QMenu {
                background: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 12px;
                padding: 6px;
            }
            QMenu::item {
                color: #1d1d1f;
                padding: 7px 12px;
                border-radius: 8px;
            }
            QMenu::item:selected {
                background: #007aff;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #ececf0;
                margin: 6px 8px;
            }
            """
        )
        self.setStyleSheet(
            stylesheet
            .replace("__CHEVRON_PATH__", chevron_path.as_posix())
            .replace("__CHECKMARK_PATH__", checkmark_path.as_posix())
        )

    def _apply_icons(self) -> None:
        app_icon = load_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        style = self.style()
        language_icon_path = Path(__file__).resolve().parents[1] / "assets" / "gui" / "language_globe.svg"
        if language_icon_path.exists():
            self.language_button.setIcon(QIcon(str(language_icon_path)))
        self.input_browse_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.output_browse_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.template_browse_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.detect_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.save_profile_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.load_profile_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.profile_actions_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.profile_load_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.profile_overwrite_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        self.profile_set_default_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogYesButton))
        self.profile_clear_default_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        self.profile_delete_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.start_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.stop_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.open_output_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.clear_log_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        self.gamma_reset_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.quality_reset_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.target_size_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))

    def _wire_signals(self) -> None:
        self.language_button.clicked.connect(self._toggle_language_popup)
        self.input_browse_button.clicked.connect(self._browse_input_dir)
        self.detect_button.clicked.connect(self.refresh_detection)
        self.output_browse_button.clicked.connect(self._browse_output_location)
        self.template_browse_button.clicked.connect(self._browse_template)
        self.clear_log_button.clicked.connect(self.log_edit.clear)
        self.open_output_button.clicked.connect(self._open_output_location)
        self.start_button.clicked.connect(self._start_run)
        self.stop_button.clicked.connect(self._request_stop)
        self.save_profile_button.clicked.connect(self._save_current_profile)
        self.load_profile_button.clicked.connect(self._load_selected_profile)
        self.profile_combo.currentIndexChanged.connect(self._refresh_profile_buttons)
        self.preview_crop_checkbox.toggled.connect(self._schedule_preview_refresh)
        self.preview_volume_combo.currentIndexChanged.connect(self._on_preview_volume_changed)
        self.preview_prev_button.clicked.connect(lambda: self._step_preview_page(self._preview_left_step()))
        self.preview_next_button.clicked.connect(lambda: self._step_preview_page(self._preview_right_step()))
        self.preview_page_spin.valueChanged.connect(self._on_preview_page_changed)
        self.output_format_combo.currentIndexChanged.connect(self._on_output_format_changed)
        self.reading_direction_combo.currentIndexChanged.connect(self._on_layout_options_changed)
        self.page_layout_combo.currentIndexChanged.connect(self._on_layout_options_changed)
        self.shift_mode_combo.currentIndexChanged.connect(self._on_layout_options_changed)
        self.virtual_panels_combo.currentIndexChanged.connect(self._on_layout_options_changed)
        self.panel_movement_combo.currentIndexChanged.connect(self._on_layout_options_changed)
        self.panel_preset_combo.currentIndexChanged.connect(self._on_panel_preset_index_changed)
        self.panel_preset_combo.activated.connect(self._on_panel_preset_activated)
        self.target_size_button.clicked.connect(self._edit_custom_panel_size)

        self.input_edit.textChanged.connect(self._schedule_detection)
        self.input_edit.textChanged.connect(self._schedule_preview_refresh)
        self.output_edit.textChanged.connect(self._on_output_text_changed)
        self.target_size_edit.textChanged.connect(self._sync_custom_panel_label)
        self.target_size_edit.textChanged.connect(self._schedule_preview_refresh)
        self.image_preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.crop_mode_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        self.preserve_color_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        self.autocontrast_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        self.autolevel_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        self.gamma_spin.valueChanged.connect(self._on_gamma_changed)
        self.jpeg_quality_spin.valueChanged.connect(self._on_quality_changed)
        self.gamma_reset_button.clicked.connect(self._reset_gamma_to_preset)
        self.quality_reset_button.clicked.connect(self._reset_quality_to_preset)
        self._detect_timer.timeout.connect(self.refresh_detection)
        self._preview_timer.timeout.connect(self._refresh_preview)

    def _mark_startup_complete(self) -> None:
        self._startup_complete = True

    def _on_language_selected(self, language: str) -> None:
        if self._loading_state:
            return
        selected = normalize_ui_language(language, default=self._language)
        if selected == self._language:
            return
        if self._worker is not None:
            QMessageBox.information(
                self,
                self._tr("ui.running.title"),
                self._tr("ui.please.wait.task.finish.switching.language"),
            )
            return

        self._language = selected
        state = self._current_state()
        state.language = self._language
        self._settings_store.save(state)

        replacement_window = MainWindow()
        replacement_window.show()
        self._replacement_window = replacement_window
        self.close()

    def _on_output_format_changed(self) -> None:
        self._refresh_control_states()
        if self._last_detection is not None and (self._output_auto or not self.output_edit.text().strip()):
            self.refresh_detection()
        self._schedule_preview_refresh()

    def _on_layout_options_changed(self) -> None:
        if self._syncing_controls:
            return
        if str(self.page_layout_combo.currentData()) == "single" and str(self.shift_mode_combo.currentData()) != "off":
            self._syncing_controls = True
            self._set_combo_value(self.shift_mode_combo, "off")
            self._syncing_controls = False
        self._refresh_control_states()
        if self._last_detection is not None:
            self.refresh_detection()
        self._schedule_preview_refresh()

    def _load_state(self, state: GuiState) -> None:
        self._loading_state = True
        self._syncing_controls = True
        try:
            language = normalize_ui_language(getattr(state, "language", self._language), default=self._language)
            self._language = language
            self.input_edit.setText(state.input_dir)
            self.output_edit.setText(state.output_location)
            self.template_edit.setText("")
            self.title_edit.setText(state.title)
            self._set_combo_value(self.shift_mode_combo, state.shift_mode)
            self._set_combo_value(self.image_preset_combo, state.image_preset)
            self._set_combo_value(self.crop_mode_combo, state.crop_mode)
            self._set_combo_value(self.reading_direction_combo, state.reading_direction)
            self._set_combo_value(self.page_layout_combo, state.page_layout)
            self._set_combo_value(self.virtual_panels_combo, state.virtual_panels)
            self._set_combo_value(self.panel_movement_combo, state.panel_movement)
            self.target_size_edit.setText(state.target_size_text)
            self._set_combo_value(self.panel_preset_combo, state.panel_preset)
            self._set_combo_value(self.preserve_color_combo, state.preserve_color)
            self.gamma_spin.setValue(state.gamma_value)
            self._set_combo_value(self.autocontrast_combo, state.autocontrast)
            self._set_combo_value(self.autolevel_combo, state.autolevel)
            self.jpeg_quality_spin.setValue(state.jpeg_quality_value)
            self._set_combo_value(self.output_format_combo, state.output_format)
            self.kfx_plugin_edit.setText(state.kfx_plugin)
            self.jobs_spin.setValue(state.jobs)
        finally:
            self._syncing_controls = False
            self._loading_state = False

        self._apply_gamma_auto_state()
        self._apply_quality_auto_state()
        self._previous_panel_preset = str(self.panel_preset_combo.currentData())
        self._sync_custom_panel_label()
        self._reload_profile_combo()
        self._refresh_control_states()
        self.refresh_detection()
        self._schedule_preview_refresh()
        self._apply_localization_if_needed()

    def _build_profile_menu(self) -> None:
        self.profile_actions_menu = QMenu(self)
        self.profile_load_action = self.profile_actions_menu.addAction(self._txt("ui.load.profile"))
        self.profile_overwrite_action = self.profile_actions_menu.addAction(
            self._txt("ui.overwrite.settings")
        )
        self.profile_set_default_action = self.profile_actions_menu.addAction(
            self._txt("ui.set.default.profile")
        )
        self.profile_clear_default_action = self.profile_actions_menu.addAction(
            self._txt("ui.clear.default.profile")
        )
        self.profile_actions_menu.addSeparator()
        self.profile_delete_action = self.profile_actions_menu.addAction(self._txt("ui.delete.profile"))

        self.profile_load_action.triggered.connect(self._load_selected_profile)
        self.profile_overwrite_action.triggered.connect(self._overwrite_selected_profile)
        self.profile_set_default_action.triggered.connect(self._set_selected_profile_default)
        self.profile_clear_default_action.triggered.connect(self._clear_default_profile)
        self.profile_delete_action.triggered.connect(self._delete_selected_profile)
        self.profile_actions_button.setMenu(self.profile_actions_menu)

    def _reload_profile_combo(self) -> None:
        current = self.profile_combo.currentData()
        default_profile = self._settings_store.load_default_profile_name()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for name in sorted(self._settings_store.load_profiles()):
            label = f"★ {name}" if name == default_profile else name
            self.profile_combo.addItem(label, name)
        preferred = current or default_profile
        if preferred is not None:
            index = self.profile_combo.findData(preferred)
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        elif self.profile_combo.count() == 0:
            self.profile_combo.setCurrentIndex(-1)
        self.profile_combo.blockSignals(False)
        self._refresh_profile_buttons()
        self._apply_localization_if_needed()

    def _save_current_profile(self) -> None:
        name, accepted = QInputDialog.getText(self, self._tr("ui.save.profile"), self._tr("ui.profile.name"))
        if not accepted:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, self._tr("ui.empty.name"), self._tr("ui.please.enter.profile.name"))
            return
        if name in self._settings_store.load_profiles():
            answer = QMessageBox.question(
                self,
                self._tr("ui.overwrite.existing.profile"),
                self._tr("ui.profile.already.exists.overwrite.it", name=name),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._settings_store.save_profile(name, self._current_state())
        self._reload_profile_combo()
        self._set_combo_value(self.profile_combo, name)

    def _load_selected_profile(self) -> None:
        name = self.profile_combo.currentData()
        if not name:
            QMessageBox.information(self, self._tr("ui.no.profile"), self._tr("ui.please.save.profile.first"))
            return
        profiles = self._settings_store.load_profiles()
        state = profiles.get(str(name))
        if state is None:
            QMessageBox.warning(self, self._tr("ui.profile.not.found"), self._tr("ui.selected.profile.does.not.exist.please.choose"))
            self._reload_profile_combo()
            return
        state.input_dir = self.input_edit.text().strip()
        state.output_location = self.output_edit.text().strip()
        state.language = self._language
        self._load_state(state)

    def _overwrite_selected_profile(self) -> None:
        name = self.profile_combo.currentData()
        if not name:
            QMessageBox.information(self, self._tr("ui.no.profile"), self._tr("ui.please.select.profile.first"))
            return
        answer = QMessageBox.question(
            self,
            self._tr("ui.overwrite.profile"),
            self._tr("ui.overwrite.profile.settings", name=name),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._settings_store.save_profile(str(name), self._current_state())
        self._reload_profile_combo()
        self._set_combo_value(self.profile_combo, str(name))

    def _delete_selected_profile(self) -> None:
        name = self.profile_combo.currentData()
        if not name:
            QMessageBox.information(self, self._tr("ui.no.profile"), self._tr("ui.please.select.profile.first"))
            return
        answer = QMessageBox.question(
            self,
            self._tr("ui.profile.delete.title"),
            self._tr("ui.delete.profile.generated.files.not.affected", name=name),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._settings_store.delete_profile(str(name))
        self._reload_profile_combo()

    def _set_selected_profile_default(self) -> None:
        name = self.profile_combo.currentData()
        if not name:
            QMessageBox.information(self, self._tr("ui.no.profile"), self._tr("ui.please.select.profile.first"))
            return
        self._settings_store.set_default_profile(str(name))
        self._reload_profile_combo()
        self._set_combo_value(self.profile_combo, str(name))

    def _clear_default_profile(self) -> None:
        default_profile = self._settings_store.load_default_profile_name()
        if default_profile is None:
            QMessageBox.information(self, self._tr("ui.no.default.set"), self._tr("ui.no.default.profile.set"))
            return
        self._settings_store.set_default_profile(None)
        self._reload_profile_combo()

    def _refresh_profile_buttons(self) -> None:
        selected_profile = self.profile_combo.currentData()
        default_profile = self._settings_store.load_default_profile_name()
        has_profile = selected_profile is not None
        self.load_profile_button.setEnabled(has_profile)
        self.profile_actions_button.setEnabled(has_profile)
        self.profile_load_action.setEnabled(has_profile)
        self.profile_overwrite_action.setEnabled(has_profile)
        self.profile_delete_action.setEnabled(has_profile)
        self.profile_set_default_action.setEnabled(has_profile and selected_profile != default_profile)
        self.profile_clear_default_action.setEnabled(default_profile is not None)

        if not has_profile:
            self.profile_status_label.setText(self._txt("ui.profile.none.saved.yet"))
            return
        if default_profile is None:
            self.profile_status_label.setText(self._txt("ui.no.default.profile.selected"))
            return
        if selected_profile == default_profile:
            self.profile_status_label.setText(
                self._tr(
                    "ui.default.profile.selected",
                    profile=default_profile,
                )
            )
            return
        self.profile_status_label.setText(
            self._tr("ui.default.profile", profile=default_profile)
        )

    def _on_panel_preset_index_changed(self) -> None:
        self._sync_custom_panel_label()
        self._refresh_control_states()

    def _on_panel_preset_activated(self) -> None:
        panel_preset = str(self.panel_preset_combo.currentData())
        if self._syncing_controls or not self._startup_complete:
            self._refresh_control_states()
            return

        if panel_preset == "custom":
            if not self._open_custom_panel_dialog():
                self._syncing_controls = True
                self._set_combo_value(self.panel_preset_combo, self._previous_panel_preset)
                self._syncing_controls = False
                self._refresh_control_states()
                return

        self._previous_panel_preset = panel_preset
        self._sync_custom_panel_label()
        self._refresh_control_states()
        self._schedule_preview_refresh()

    def _sync_custom_panel_label(self) -> None:
        index = self.panel_preset_combo.findData("custom")
        if index < 0:
            return
        custom_size = self.target_size_edit.text().strip().replace("x", "×")
        if custom_size:
            self.panel_preset_combo.setItemText(
                index,
                self._tr("ui.custom", size=custom_size),
            )
        else:
            self.panel_preset_combo.setItemText(index, self._txt("ui.custom.size"))

    def _open_custom_panel_dialog(self) -> bool:
        dialog = PanelSizeDialog(self.target_size_edit.text().strip(), self._language, self)
        self._localize_widget_tree(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.target_size_edit.setText(dialog.value())
        return True

    def _edit_custom_panel_size(self) -> None:
        if str(self.panel_preset_combo.currentData()) != "custom":
            return
        self._open_custom_panel_dialog()

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _build_option_combo(self, options: tuple[tuple[str, str], ...]) -> QComboBox:
        combo = DropdownOnlyComboBox()
        popup = DropdownPopupListView(combo)
        popup.setObjectName("ComboPopup")
        popup.setFrameShape(QFrame.Shape.NoFrame)
        popup.setLineWidth(0)
        popup.setMidLineWidth(0)
        popup.setSpacing(1)
        popup.setUniformItemSizes(True)
        popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        popup.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        combo.setView(popup)
        combo.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        palette = combo.palette()
        palette.setColor(QPalette.ColorRole.Text, QColor("#111827"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111827"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#111827"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563eb"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        combo.setPalette(palette)
        popup.setPalette(palette)
        popup.viewport().setPalette(palette)
        popup.viewport().setAutoFillBackground(True)
        for value, text in options:
            combo.addItem(self._tr(text), value)
        return combo

    def _schedule_detection(self) -> None:
        self._detect_timer.start()

    def _on_output_text_changed(self, value: str) -> None:
        if self._loading_state:
            return
        self._output_auto = not value.strip()
        self._schedule_detection()

    def _on_preset_changed(self) -> None:
        if self._gamma_auto:
            self._syncing_controls = True
            self.gamma_spin.setValue(preset_default_gamma(self._current_preset()))
            self._syncing_controls = False
        if self._jpeg_quality_auto:
            self._syncing_controls = True
            self.jpeg_quality_spin.setValue(preset_default_jpeg_quality(self._current_preset()))
            self._syncing_controls = False
        self._apply_gamma_auto_state()
        self._apply_quality_auto_state()
        self._schedule_preview_refresh()

    def _on_gamma_changed(self) -> None:
        if self._syncing_controls:
            return
        self._gamma_auto = False
        self._apply_gamma_auto_state()
        self._schedule_preview_refresh()

    def _on_quality_changed(self) -> None:
        if self._syncing_controls:
            return
        self._jpeg_quality_auto = False
        self._apply_quality_auto_state()
        self._schedule_preview_refresh()

    def _reset_gamma_to_preset(self) -> None:
        self._gamma_auto = True
        self._syncing_controls = True
        self.gamma_spin.setValue(preset_default_gamma(self._current_preset()))
        self._syncing_controls = False
        self._apply_gamma_auto_state()
        self._schedule_preview_refresh()

    def _reset_quality_to_preset(self) -> None:
        self._jpeg_quality_auto = True
        self._syncing_controls = True
        self.jpeg_quality_spin.setValue(preset_default_jpeg_quality(self._current_preset()))
        self._syncing_controls = False
        self._apply_quality_auto_state()
        self._schedule_preview_refresh()

    def _apply_gamma_auto_state(self) -> None:
        if self._gamma_auto:
            self.gamma_spin.setSuffix(self._txt("ui.auto.suffix"))
            self.gamma_reset_button.setEnabled(False)
        else:
            self.gamma_spin.setSuffix("")
            self.gamma_reset_button.setEnabled(True)

    def _apply_quality_auto_state(self) -> None:
        if self._jpeg_quality_auto:
            self.jpeg_quality_spin.setSuffix(self._txt("ui.auto.suffix"))
            self.quality_reset_button.setEnabled(False)
        else:
            self.jpeg_quality_spin.setSuffix("")
            self.quality_reset_button.setEnabled(True)

    def _schedule_preview_refresh(self) -> None:
        if not hasattr(self, "_preview_timer"):
            return
        self._preview_timer.start()

    def _preview_source_key(self, source_dir: Path | None) -> str | None:
        if source_dir is None:
            return None
        try:
            return str(source_dir.expanduser().resolve())
        except OSError:
            return str(source_dir)

    def _preview_is_rtl(self) -> bool:
        if not hasattr(self, "reading_direction_combo"):
            return True
        return str(self.reading_direction_combo.currentData()) == "rtl"

    def _preview_left_step(self) -> int:
        return 1 if self._preview_is_rtl() else -1

    def _preview_right_step(self) -> int:
        return -1 if self._preview_is_rtl() else 1

    def _sync_preview_volume_options(self) -> None:
        if not hasattr(self, "preview_volume_combo"):
            return

        detection = self._last_detection
        selected_path: Path | None = None
        previous_key = self._preview_source_key(self._preview_selected_source_dir)

        self.preview_volume_combo.blockSignals(True)
        self.preview_volume_combo.clear()
        if detection is not None and detection.mode == "batch" and detection.image_subdirs:
            self.preview_volume_row.setVisible(True)
            for path in detection.image_subdirs:
                self.preview_volume_combo.addItem(path.name, self._preview_source_key(path) or str(path))

            preferred_key = previous_key
            index = -1
            if preferred_key is not None:
                index = self.preview_volume_combo.findData(preferred_key)
            if index < 0:
                index = 0
            self.preview_volume_combo.setCurrentIndex(index)
            selected_data = self.preview_volume_combo.currentData()
            if isinstance(selected_data, str) and selected_data:
                selected_path = Path(selected_data)
        else:
            self.preview_volume_row.setVisible(False)
            if detection is not None and detection.mode == "single":
                selected_path = detection.input_dir
        self.preview_volume_combo.blockSignals(False)

        selected_key = self._preview_source_key(selected_path)
        self._preview_selected_source_dir = selected_path
        if selected_key != previous_key:
            self._preview_requested_page_number = None
            self._preview_current_page_number = None

    def _update_preview_navigation_controls(
        self,
        current_page_number: int | None = None,
        total_pages: int = 0,
        available_page_numbers: tuple[int, ...] = (),
    ) -> None:
        self._preview_current_page_number = current_page_number
        self._preview_total_pages = total_pages
        self._preview_available_page_numbers = available_page_numbers

        self._syncing_preview_controls = True
        try:
            spin_max = max(1, total_pages)
            self.preview_page_spin.setRange(1, spin_max)
            self.preview_page_spin.setValue(current_page_number or min(self.preview_page_spin.value(), spin_max))
            self.preview_total_label.setText(f"/ {total_pages}")
        finally:
            self._syncing_preview_controls = False

        current_index = -1
        if current_page_number is not None:
            try:
                current_index = available_page_numbers.index(current_page_number)
            except ValueError:
                current_index = -1
        nav_enabled = total_pages > 0 and bool(available_page_numbers)
        self.preview_page_spin.setEnabled(nav_enabled)
        left_target_index = current_index + self._preview_left_step() if current_index >= 0 else -1
        right_target_index = current_index + self._preview_right_step() if current_index >= 0 else -1
        self.preview_prev_button.setEnabled(nav_enabled and 0 <= left_target_index < len(available_page_numbers))
        self.preview_next_button.setEnabled(nav_enabled and 0 <= right_target_index < len(available_page_numbers))
        self._refresh_preview_ui_texts()

    def _on_preview_volume_changed(self) -> None:
        if self._syncing_preview_controls:
            return
        selected_data = self.preview_volume_combo.currentData()
        if not isinstance(selected_data, str) or not selected_data:
            return
        self._preview_selected_source_dir = Path(selected_data)
        self._preview_requested_page_number = None
        self._schedule_preview_refresh()

    def _on_preview_page_changed(self, page_number: int) -> None:
        if self._syncing_preview_controls or self._preview_total_pages <= 0:
            return
        self._preview_requested_page_number = page_number
        self._schedule_preview_refresh()

    def _step_preview_page(self, step: int) -> None:
        if not self._preview_available_page_numbers or self._preview_current_page_number is None:
            return
        try:
            current_index = self._preview_available_page_numbers.index(self._preview_current_page_number)
        except ValueError:
            current_index = 0
        target_index = max(0, min(len(self._preview_available_page_numbers) - 1, current_index + step))
        target_page_number = self._preview_available_page_numbers[target_index]
        if target_page_number == self._preview_current_page_number:
            return
        self._preview_requested_page_number = target_page_number
        self._schedule_preview_refresh()

    def _show_preview_placeholder(self, title: str, hint: str = "") -> None:
        self._preview_pixmap = None
        self._preview_canvas_applied_height = None
        self._preview_summary_text = title
        self._preview_hint_text = hint or self._txt(
            "ui.preview.refreshes.automatically.when.input.valid.parameters",
        )
        self.preview_canvas_label.setMinimumHeight(self._preview_canvas_base_min_height)
        self.preview_canvas_label.setMaximumHeight(self._preview_height_cap())
        self.preview_canvas_label.clear()
        self.preview_canvas_label.setText(title)
        self.preview_info_label.setText(title)
        self.preview_hint_label.setText(self._preview_hint_text)
        self._update_preview_navigation_controls()

    def _set_preview_image(self, image) -> None:
        from PIL.ImageQt import ImageQt

        qimage = ImageQt(image.convert("RGBA"))
        self._preview_pixmap = QPixmap.fromImage(qimage)
        self._apply_preview_pixmap()

    def _preview_height_cap(self) -> int:
        viewport_height = 0
        right_scroll = getattr(self, "right_scroll", None)
        if isinstance(right_scroll, QScrollArea):
            viewport_height = right_scroll.viewport().height()
        if viewport_height <= 0:
            viewport_height = self.height()
        cap_by_ratio = int(viewport_height * 0.9)
        reserved_height = 130
        nav_row = getattr(self, "preview_nav_row", None)
        if isinstance(nav_row, QWidget):
            reserved_height += nav_row.sizeHint().height() + 8
        volume_row = getattr(self, "preview_volume_row", None)
        if isinstance(volume_row, QWidget) and volume_row.isVisible():
            reserved_height += volume_row.sizeHint().height() + 8
        cap_by_budget = viewport_height - reserved_height
        cap = cap_by_ratio if cap_by_budget <= 0 else min(cap_by_ratio, cap_by_budget)
        return max(self._preview_canvas_base_min_height, min(cap, 980))

    def _apply_preview_pixmap(self) -> None:
        if self._preview_pixmap is None:
            return
        target_width = self.preview_canvas_label.contentsRect().width()
        if target_width <= 0:
            return
        height_cap = self._preview_height_cap()
        scaled = self._preview_pixmap.scaled(
            target_width,
            height_cap,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        desired_height = min(
            max(self._preview_canvas_image_floor_height, scaled.height()),
            height_cap,
        )
        if self._preview_canvas_applied_height != desired_height:
            self._preview_canvas_applied_height = desired_height
            self.preview_canvas_label.setMinimumHeight(desired_height)
            self.preview_canvas_label.setMaximumHeight(desired_height)
        self.preview_canvas_label.setPixmap(scaled)
        self.preview_canvas_label.setText("")

    def _resolve_preview_source_dir(self) -> tuple[Path | None, str]:
        detection = self._last_detection
        if detection is None:
            return None, self._txt("ui.choose.input.folder.first")
        if detection.mode == "single":
            self._preview_selected_source_dir = detection.input_dir
            return detection.input_dir, self._txt(
                "ui.single.preview.current.folder",
            )
        if detection.mode == "batch":
            if detection.image_subdirs:
                selected_data = self.preview_volume_combo.currentData() if hasattr(self, "preview_volume_combo") else ""
                selected_dir = detection.image_subdirs[0]
                if isinstance(selected_data, str) and selected_data:
                    matched_dir = next(
                        (path for path in detection.image_subdirs if self._preview_source_key(path) == selected_data),
                        None,
                    )
                    if matched_dir is not None:
                        selected_dir = matched_dir
                self._preview_selected_source_dir = selected_dir
                return selected_dir, self._tr(
                    "ui.batch.preview.current.volume",
                    volume=selected_dir.name,
                )
            return None, self._txt("ui.no.previewable.volume.found.batch.folder")
        if detection.mode == "invalid":
            return None, self._txt("ui.folder.structure.conflict.preview.unavailable")
        return None, self._txt("ui.no.previewable.images.found")

    def _build_preview_config(self) -> AppRunConfig:
        state = self._current_state()
        target_size_text = state.target_size_text if state.panel_preset == "custom" else ""
        return AppRunConfig(
            input_dir=state.input_dir,
            output_location="",
            template_path="",
            title="",
            shift=state.shift,
            reading_direction=state.reading_direction,
            page_layout=state.page_layout,
            virtual_panels=state.virtual_panels == "enabled",
            panel_movement=state.panel_movement,
            image_preset=state.image_preset,
            crop_mode=state.crop_mode,
            target_size_text=target_size_text,
            scribe_panel=state.scribe_panel,
            preserve_color=state.preserve_color,
            gamma_value=state.gamma_value,
            gamma_auto=state.gamma_auto,
            autocontrast=state.autocontrast,
            autolevel=state.autolevel,
            jpeg_quality_value=state.jpeg_quality_value,
            jpeg_quality_auto=state.jpeg_quality_auto,
            output_format="kpf",
            jobs=1,
        )

    def _refresh_preview(self) -> None:
        source_dir, source_hint = self._resolve_preview_source_dir()
        if source_dir is None:
            self._show_preview_placeholder(
                self._txt("ui.preview.temporarily.unavailable"),
                source_hint,
            )
            return

        try:
            preview_config = self._build_preview_config()
            image_processing = build_image_processing_options(preview_config)
            layout_options = build_layout_options(preview_config)
            preview = render_preview(
                source_dir=source_dir,
                image_processing=image_processing,
                layout_options=layout_options,
                shift_first_page=preview_config.shift,
                show_crop_boxes=self.preview_crop_checkbox.isChecked(),
                anchor_page_number=self._preview_requested_page_number,
                language=self._language,
            )
        except Exception as exc:
            self._show_preview_placeholder(
                self._txt("ui.preview.generation.failed"),
                self._tr(str(exc)),
            )
            return

        self._preview_selected_source_dir = source_dir
        self._preview_requested_page_number = preview.current_page_number
        self._update_preview_navigation_controls(
            current_page_number=preview.current_page_number,
            total_pages=preview.total_pages,
            available_page_numbers=preview.available_page_numbers,
        )
        self._preview_summary_text = preview.summary
        self._preview_hint_text = f"{source_hint} {preview.hint}"
        self.preview_info_label.setText(self._preview_summary_text)
        self.preview_hint_label.setText(self._preview_hint_text)
        self._set_preview_image(preview.image)

    def _current_preset(self) -> str:
        return str(self.image_preset_combo.currentData())

    def _layout_summary_text(self) -> str:
        direction_label = self.reading_direction_combo.currentText() if hasattr(self, "reading_direction_combo") else "RTL"
        layout_label = self.page_layout_combo.currentText() if hasattr(self, "page_layout_combo") else self._tr("ui.facing.spread")
        if hasattr(self, "virtual_panels_combo") and str(self.virtual_panels_combo.currentData()) == "enabled":
            panels_label = f"Virtual Panels {self.panel_movement_combo.currentText()}"
        else:
            panels_label = self._txt("ui.virtual.panels.disabled")
        return f"{direction_label} · {layout_label} · {panels_label}"

    def _refresh_control_states(self) -> None:
        mode = self._last_detection.mode if self._last_detection else "empty"
        is_batch = mode == "batch"
        is_single = mode == "single"
        self.jobs_spin.setEnabled(is_batch)
        self.title_edit.setEnabled(is_single)
        output_format = str(self.output_format_combo.currentData())
        panel_preset = str(self.panel_preset_combo.currentData())
        page_layout = str(self.page_layout_combo.currentData())
        virtual_panels_enabled = str(self.virtual_panels_combo.currentData()) == "enabled"
        if page_layout != "facing" and str(self.shift_mode_combo.currentData()) != "off":
            was_syncing = self._syncing_controls
            self._syncing_controls = True
            self._set_combo_value(self.shift_mode_combo, "off")
            self._syncing_controls = was_syncing
        self.kfx_plugin_edit.setEnabled(output_format in {"kpf_kfx", "kfx_only"})
        self.target_size_edit.setEnabled(panel_preset == "custom")
        self.target_size_button.setEnabled(panel_preset == "custom")
        self.shift_mode_combo.setEnabled(page_layout == "facing")
        self.panel_movement_combo.setEnabled(virtual_panels_enabled)
        self.panel_movement_field.setVisible(virtual_panels_enabled)
        self.open_output_button.setEnabled(bool(self._existing_output_location()))

        shift_tooltip = self._txt(
            self._cli_tip("shift"),
        )
        if page_layout != "facing":
            shift_tooltip = self._txt(
                "ui.single.layout.does.not.use.first.shift",
            )
        self._set_help_tip(self.shift_mode_combo, shift_tooltip)

        panel_movement_tooltip = self._txt(
            PANEL_MOVEMENT_TOOLTIP,
        )
        if not virtual_panels_enabled:
            panel_movement_tooltip = self._txt(
                "ui.panel.movement.applies.when.virtual.panels.enabled",
            )
        self._set_help_tip(self.panel_movement_combo, panel_movement_tooltip)

        if is_single:
            self.title_edit.setPlaceholderText(self._txt("ui.optional.blank.uses.folder.name"))
            self._set_help_tip(
                self.title_edit,
                self._txt(
                    self._cli_tip("title"),
                ),
            )
        elif is_batch:
            self.title_edit.setPlaceholderText(
                self._txt("ui.batch.auto.uses.subfolder.names")
            )
            self._set_help_tip(
                self.title_edit,
                self._txt(
                    "ui.batch.each.book.uses.its.subfolder.name",
                ),
            )
        else:
            self.title_edit.setPlaceholderText(self._txt("ui.optional.blank.uses.folder.name"))
            self._set_help_tip(
                self.title_edit,
                self._txt(
                    self._cli_tip("title"),
                ),
            )

        if is_single:
            extension = primary_output_suffix(output_format)
            folder_suffix = output_directory_suffix(output_format)
            self.output_edit.setPlaceholderText(
                self._tr("ui.blank.auto.output.subfolder", suffix=folder_suffix)
            )
            self._set_help_tip(
                self.output_edit,
                self._tr(
                    "ui.single.output.file.blank.defaults.input.dir",
                    suffix=folder_suffix,
                    extension=extension,
                ),
            )
        elif is_batch:
            folder_suffix = output_directory_suffix(output_format)
            self.output_edit.setPlaceholderText(
                self._tr("ui.blank.auto.output.subfolder", suffix=folder_suffix)
            )
            self._set_help_tip(
                self.output_edit,
                self._tr(
                    "ui.batch.output.directory.blank.defaults.input.dir",
                    suffix=folder_suffix,
                ),
            )
        else:
            self.output_edit.setPlaceholderText(self._txt("ui.auto.suggested.output"))
            self._set_help_tip(
                self.output_edit,
                self._txt(
                    "ui.single.use.output.file.batch.use.output",
                ),
            )

    def refresh_detection(self) -> None:
        input_text = self.input_edit.text().strip()
        if not input_text:
            self._last_detection = None
            self._preview_selected_source_dir = None
            self._preview_requested_page_number = None
            self._sync_preview_volume_options()
            self._set_mode_badge(self._txt("ui.waiting"), "neutral")
            self.mode_summary.clear()
            self.mode_summary.setVisible(False)
            self.next_step_label.setText(self._txt("ui.next.choose.input.folder"))
            self.action_summary_label.setText(self._txt("ui.please.choose.input.folder"))
            self.structure_hint_label.setText(
                self._txt(
                    "ui.single.choose.folder.containing.images.batch.choose",
                )
            )
            self._refresh_control_states()
            self._schedule_preview_refresh()
            return

        input_dir = Path(input_text).expanduser()
        ignored_paths = set()
        output_text = self.output_edit.text().strip()
        if output_text:
            output_path = Path(output_text).expanduser()
            if output_path.suffix.lower() != ".kpf":
                ignored_paths.add(output_path)

        try:
            detection = detect_input_mode(input_dir, extra_ignored_paths=ignored_paths)
        except (FileNotFoundError, NotADirectoryError) as exc:
            self._last_detection = None
            self._preview_selected_source_dir = None
            self._preview_requested_page_number = None
            self._sync_preview_volume_options()
            self._set_mode_badge(self._txt("ui.invalid.path"), "danger")
            self.mode_summary.clear()
            self.mode_summary.setVisible(False)
            self.next_step_label.setText(self._txt("ui.fix.input.folder.continue"))
            self.action_summary_label.setText(self._txt("ui.path.unavailable"))
            self.structure_hint_label.setText(self._txt("ui.please.choose.existing.folder"))
            self._refresh_control_states()
            self._schedule_preview_refresh()
            return

        self._last_detection = detection
        self._sync_preview_volume_options()
        if detection.mode == "single":
            self.mode_summary.setText(self._tr("ui.mode.count.images", count=len(detection.root_images)))
            self.mode_summary.setVisible(True)
        elif detection.mode == "batch":
            self.mode_summary.setText(self._tr("ui.mode.count.volumes", count=len(detection.image_subdirs)))
            self.mode_summary.setVisible(True)
        elif detection.mode == "invalid":
            self.mode_summary.clear()
            self.mode_summary.setVisible(False)
        else:
            self.mode_summary.clear()
            self.mode_summary.setVisible(False)

        if detection.mode == "single":
            self._set_mode_badge(self._txt("ui.single"), "success")
            self.next_step_label.setText(
                self._txt("ui.confirm.output.file.then.start")
            )
            self.action_summary_label.setText(
                self._tr(
                    "ui.single.task.images.1.output.file",
                    count=len(detection.root_images),
                    layout=self._layout_summary_text(),
                )
            )
            self.structure_hint_label.setText(
                self._txt(
                    "ui.output.path.auto.suggested.layout.reading.options",
                )
            )
        elif detection.mode == "batch":
            self._set_mode_badge(self._txt("ui.batch"), "primary")
            self.next_step_label.setText(
                self._txt("ui.confirm.output.directory.then.start")
            )
            self.action_summary_label.setText(
                self._tr(
                    "ui.batch.task.volumes.multiple.output.files",
                    count=len(detection.image_subdirs),
                    layout=self._layout_summary_text(),
                )
            )
            names = self._join_name_list([path.name for path in detection.image_subdirs], limit=6)
            self.structure_hint_label.setText(self._tr("ui.volumes", names=names))
        elif detection.mode == "invalid":
            self._set_mode_badge(self._txt("ui.folder.conflict"), "danger")
            self.next_step_label.setText(
                self._txt("ui.fix.folder.structure.then.run.again")
            )
            self.action_summary_label.setText(
                self._txt(
                    "ui.root.images.image.subfolders.coexist.cannot.decide",
                )
            )
            names = self._join_name_list([path.name for path in detection.image_subdirs], limit=6)
            self.structure_hint_label.setText(
                self._tr(
                    "ui.root.images.conflicting.subfolders",
                    count=len(detection.root_images),
                    names=names,
                )
            )
        else:
            self._set_mode_badge(self._txt("ui.no.images"), "warning")
            self.next_step_label.setText(
                self._txt("ui.choose.folder.containing.jpg.jpeg.png.images")
            )
            self.action_summary_label.setText(self._txt("ui.no.processable.images.found"))
            self.structure_hint_label.setText(self._txt("ui.supported.formats.jpg.jpeg.png"))

        suggestion = suggest_output_location(
            input_dir,
            detection.mode,
            str(self.output_format_combo.currentData()),
        )
        if suggestion is not None and self.output_edit.text().strip() == str(suggestion):
            self._output_auto = True
        if suggestion is not None and (self._output_auto or not self.output_edit.text().strip()):
            self._loading_state = True
            self.output_edit.setText(str(suggestion))
            self._loading_state = False
            self._output_auto = True

        self._refresh_control_states()
        self._schedule_preview_refresh()

    def _set_mode_badge(self, text: str, tone: str) -> None:
        self.mode_badge.setText(text)
        styles = {
            "neutral": ("#f5f6f9", "#5f6775"),
            "primary": ("#edf4ff", "#2c64d8"),
            "success": ("#edf9f3", "#157a4a"),
            "warning": ("#fff8ea", "#966600"),
            "danger": ("#fef1f1", "#bd3c3c"),
        }
        background, foreground = styles[tone]
        self.mode_badge.setStyleSheet(
            f"background:{background};"
            f"color:{foreground};"
            "border:0;"
            "border-radius:999px;"
            "padding:4px 16px;"
            "font-size:12px;"
            "font-weight:700;"
        )

    def _current_state(self) -> GuiState:
        output_format = str(self.output_format_combo.currentData())
        return GuiState(
            input_dir=self.input_edit.text().strip(),
            output_location=self.output_edit.text().strip(),
            template_path="",
            title=self.title_edit.text().strip(),
            shift=str(self.shift_mode_combo.currentData()) == "on",
            shift_mode=str(self.shift_mode_combo.currentData()),
            reading_direction=str(self.reading_direction_combo.currentData()),
            page_layout=str(self.page_layout_combo.currentData()),
            virtual_panels=str(self.virtual_panels_combo.currentData()),
            panel_movement=str(self.panel_movement_combo.currentData()),
            image_preset=str(self.image_preset_combo.currentData()),
            crop_mode=str(self.crop_mode_combo.currentData()),
            target_size_text=self.target_size_edit.text().strip(),
            scribe_panel=str(self.panel_preset_combo.currentData()) == "scribe_1240x1860",
            panel_preset=str(self.panel_preset_combo.currentData()),
            preserve_color=str(self.preserve_color_combo.currentData()),
            gamma_value=self.gamma_spin.value(),
            gamma_auto=self._gamma_auto,
            autocontrast=str(self.autocontrast_combo.currentData()),
            autolevel=str(self.autolevel_combo.currentData()),
            jpeg_quality_value=self.jpeg_quality_spin.value(),
            jpeg_quality_auto=self._jpeg_quality_auto,
            emit_kfx=output_format in {"kpf_kfx", "kfx_only"},
            output_format=output_format,
            kfx_plugin=self.kfx_plugin_edit.text().strip(),
            jobs=self.jobs_spin.value(),
            language=normalize_ui_language(self._language, default="zh"),
        )

    def _build_run_config(self) -> AppRunConfig:
        state = self._current_state()
        if state.output_format in {"epub", "mobi"}:
            raise ValueError("ui.epub.mobi.generation.not.available.please.choose")
        if state.panel_preset == "custom" and not state.target_size_text.strip():
            raise ValueError("ui.selecting.custom.size.set.target.size.advanced")
        if state.page_layout == "single" and state.shift_mode == "on":
            raise ValueError("ui.single.layout.does.not.support.first.shift")

        target_size_text = state.target_size_text if state.panel_preset == "custom" else ""
        return AppRunConfig(
            input_dir=state.input_dir,
            output_location=state.output_location,
            template_path=state.template_path,
            title=state.title,
            shift=state.shift,
            reading_direction=state.reading_direction,
            page_layout=state.page_layout,
            virtual_panels=state.virtual_panels == "enabled",
            panel_movement=state.panel_movement,
            image_preset=state.image_preset,
            crop_mode=state.crop_mode,
            target_size_text=target_size_text,
            scribe_panel=state.scribe_panel,
            preserve_color=state.preserve_color,
            gamma_value=state.gamma_value,
            gamma_auto=state.gamma_auto,
            autocontrast=state.autocontrast,
            autolevel=state.autolevel,
            jpeg_quality_value=state.jpeg_quality_value,
            jpeg_quality_auto=state.jpeg_quality_auto,
            emit_kfx=state.emit_kfx,
            output_format=state.output_format,
            kfx_plugin=state.kfx_plugin,
            jobs=state.jobs,
        )

    def _start_run(self) -> None:
        try:
            config = self._build_run_config()
            detection = validate_run_config(config)
            output_location = resolve_output_location(config, detection.mode)
        except Exception as exc:
            QMessageBox.warning(self, self._tr("ui.validation.failed"), self._tr(str(exc)))
            return

        self._settings_store.save(self._current_state())
        self._last_summary = None
        self.result_summary.setText(self._txt("ui.running"))
        self._append_log("ui.run.started")
        self._append_log(encode_i18n_message("ui.summary.output", path=output_location))
        self.status_label.setText(self._txt("ui.preparing"))
        self.progress_detail_label.setText(self._txt("ui.background.task.about.start"))
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._set_running_state(True)

        self._worker_thread = QThread(self)
        self._worker = BuildWorker(config)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.log_message.connect(self._append_log)
        self._worker.status_changed.connect(self._update_status)
        self._worker.progress_changed.connect(self._update_progress)
        self._worker.finished.connect(self._handle_finished)
        self._worker.failed.connect(self._handle_failed)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.failed.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker)
        self._worker_thread.start()

    def _request_stop(self) -> None:
        if self._worker is None:
            return
        self.stop_button.setEnabled(False)
        self._worker.request_stop()

    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._worker_thread is not None:
            self._worker_thread.deleteLater()
            self._worker_thread = None
        self._set_running_state(False)

    def _handle_finished(self, summary: RunSummary) -> None:
        self._last_summary = summary
        self._append_log("ui.run.completed")
        self._update_status(self._txt("ui.completed"))
        self._refresh_control_states()
        self._render_summary(summary)

    def _handle_failed(self, message: str) -> None:
        self._append_log(encode_i18n_message("ui.log.run.failed", reason=message))
        self.status_label.setText(self._txt("ui.run.failed"))
        self.result_summary.setText(self._tr(message))
        QMessageBox.critical(self, self._tr("ui.run.failed"), self._tr(message))

    def _update_status(self, text: str) -> None:
        self.status_label.setText(self._tr(text))

    def _update_progress(self, progress: RunProgress) -> None:
        total = max(progress.total, 1)
        current = min(progress.current, total)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)

        detail = progress.phase
        if progress.current_name:
            detail += f" · {progress.current_name}"
        if progress.mode == "batch":
            detail += self._tr(
                "ui.success.failed",
                successes=progress.successes,
                failures=progress.failures,
            )
        self.progress_detail_label.setText(self._tr(detail))

    def _append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(self._tr(message))
        scrollbar = self.log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _render_summary(self, summary: RunSummary) -> None:
        mode_label = self._tr("ui.single") if summary.mode == "single" else self._tr("ui.batch")
        lines = [
            self._tr("ui.summary.mode", mode=mode_label),
            self._tr("ui.summary.success", count=len(summary.successes)),
            self._tr("ui.summary.failed", count=len(summary.failures)),
            self._tr("ui.summary.output", path=summary.output_location),
        ]
        if summary.stopped:
            lines.append(self._txt("ui.status.stopped.remaining.tasks.request"))

        if summary.mode == "single" and summary.successes:
            result = summary.successes[0]
            primary_label = "KFX" if result.output_path.suffix.lower() == ".kfx" else "KPF"
            lines.append(
                self._tr(
                    "ui.summary.primary.output",
                    kind=primary_label,
                    path=result.output_path,
                )
            )
            if result.kfx_output_path is not None and result.kfx_output_path != result.output_path:
                lines.append(self._tr("ui.summary.kfx.output", path=result.kfx_output_path))
        elif summary.successes:
            success_names = self._join_name_list([result.input_dir.name for result in summary.successes[:6]])
            lines.append(self._tr("ui.succeeded", names=success_names))
            if len(summary.successes) > 6:
                lines.append(
                    self._tr("ui.more.succeeded.total.volumes", count=len(summary.successes))
                )

        if summary.failures:
            failure_lines = [
                self._tr(
                    "ui.summary.failure.item",
                    name=failure.volume_dir.name,
                    reason=self._tr(failure.reason),
                )
                for failure in summary.failures[:8]
            ]
            if len(summary.failures) > 8:
                failure_lines.append(
                    self._tr("ui.total.failures", count=len(summary.failures))
                )
            lines.append(self._txt("ui.failure.details"))
            lines.extend(failure_lines)

        self.result_summary.setText("\n".join(self._tr(line) for line in lines))

    def _set_running_state(self, running: bool) -> None:
        has_profile = self.profile_combo.currentData() is not None
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.detect_button.setEnabled(not running)
        self.save_profile_button.setEnabled(not running)
        self.load_profile_button.setEnabled(not running and has_profile)
        self.profile_actions_button.setEnabled(not running and has_profile)
        self.target_size_button.setEnabled(not running and str(self.panel_preset_combo.currentData()) == "custom")
        self.input_browse_button.setEnabled(not running)
        self.output_browse_button.setEnabled(not running)
        self.template_browse_button.setEnabled(not running)

    def _browse_input_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            self._tr("ui.choose.input.folder"),
            self.input_edit.text().strip() or str(Path.home()),
        )
        if path:
            self.input_edit.setText(path)
            self._output_auto = True

    def _browse_output_location(self) -> None:
        mode = self._last_detection.mode if self._last_detection else "single"
        current = self.output_edit.text().strip() or self.input_edit.text().strip() or str(Path.home())
        if mode == "batch":
            path = QFileDialog.getExistingDirectory(self, self._tr("ui.choose.output.directory"), current)
            if path:
                self.output_edit.setText(path)
                self._output_auto = False
            return

        output_format = str(self.output_format_combo.currentData())
        extension = primary_output_suffix(output_format)
        file_filter = "Kindle Format (*.kfx)" if extension == ".kfx" else "Kindle Package (*.kpf)"
        current_path = Path(current)
        if current_path.suffix:
            suggested_target = current if current.endswith(extension) else str(current_path.with_suffix(extension))
        else:
            suggested_target = str(current_path / f"output{extension}")
        path, _ = QFileDialog.getSaveFileName(
            self,
            self._tr("ui.choose.output.file"),
            suggested_target,
            file_filter,
        )
        if path:
            output_path = Path(path)
            if output_path.suffix.lower() != extension:
                output_path = output_path.with_suffix(extension)
            self.output_edit.setText(str(output_path))
            self._output_auto = False

    def _browse_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._tr("ui.choose.template.file"),
            self.template_edit.text().strip() or str(Path.home()),
            "Kindle Package (*.kpf *.zip);;All Files (*)",
        )
        if path:
            self.template_edit.setText(path)

    def _open_output_location(self) -> None:
        target = self._existing_output_location()
        if target is None:
            QMessageBox.information(self, self._tr("ui.no.path.available"), self._tr("ui.no.output.location.can.opened.right"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _existing_output_location(self) -> Path | None:
        if self._last_summary is not None and self._last_summary.output_location.exists():
            return self._last_summary.output_location

        if self._last_detection is not None:
            try:
                candidate = resolve_output_location(self._build_run_config(), self._last_detection.mode)
            except Exception:
                return None
            if candidate.exists():
                return candidate.parent if candidate.suffix.lower() in {".kpf", ".kfx"} else candidate
            if candidate.parent.exists():
                return candidate.parent if candidate.suffix.lower() in {".kpf", ".kfx"} else candidate
        return None

    def closeEvent(self, event) -> None:
        if getattr(self, "_app_event_filter_installed", False):
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._app_event_filter_installed = False
        self._settings_store.save(self._current_state())
        super().closeEvent(event)
