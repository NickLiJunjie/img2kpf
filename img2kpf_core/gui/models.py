from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TriStateValue = Literal["auto", "enabled", "disabled"]

TRI_STATE_OPTIONS = (
    ("auto", "ui.auto"),
    ("enabled", "ui.option.enabled"),
    ("disabled", "ui.option.disabled"),
)

PRESERVE_COLOR_OPTIONS = (
    ("enabled", "ui.option.enabled"),
    ("disabled", "ui.option.disabled"),
)

IMAGE_PRESET_OPTIONS = (
    ("none", "ui.image.preset.none"),
    ("kcc-current-like", "ui.image.preset.standard"),
    ("kcc-legacy-like", "ui.image.preset.enhanced"),
)

CROP_MODE_OPTIONS = (
    ("off", "ui.crop.mode.off"),
    ("smart", "ui.crop.mode.smart"),
    ("spread-fill", "ui.crop.mode.facing.linked"),
)

READING_DIRECTION_OPTIONS = (
    ("rtl", "ui.rtl.right.left"),
    ("ltr", "ui.ltr.left.right"),
)

PAGE_LAYOUT_OPTIONS = (
    ("facing", "ui.facing.spread"),
    ("single", "ui.layout.single.page"),
)

VIRTUAL_PANELS_OPTIONS = (
    ("enabled", "ui.enabled"),
    ("disabled", "ui.disabled"),
)

PANEL_MOVEMENT_OPTIONS = (
    ("vertical", "ui.vertical"),
    ("horizontal", "ui.horizontal"),
)

PANEL_PRESET_OPTIONS = (
    ("none", "ui.not.set"),
    ("scribe_1240x1860", "Kindle Scribe 1240×1860"),
    ("custom", "ui.custom.size"),
)

PERFORMANCE_MODE_OPTIONS = (
    ("eco", "ui.performance.mode.eco"),
    ("balanced", "ui.performance.mode.balanced"),
    ("max", "ui.performance.mode.max"),
)

JOBS_MIN = 1
JOBS_MAX = 16
JOBS_DEFAULT = 5
CROP_STRENGTH_DEFAULT = 1.00
SPREAD_CROP_STRENGTH_DEFAULT = 0.90
CROP_STRENGTH_SEMANTICS_VERSION = 4

SHIFT_MODE_OPTIONS = (
    ("off", "ui.no.shift"),
    ("on", "ui.enable.shift"),
)

OUTPUT_FORMAT_OPTIONS = (
    ("kfx_only", "KFX"),
    ("kpf", "KPF"),
    ("kpf_kfx", "KPF + KFX"),
)

@dataclass
class GuiState:
    input_dir: str = ""
    output_location: str = ""
    template_path: str = ""
    title: str = ""
    custom_title_enabled: bool = False
    volume_title_template: str = " 第 {volume} 卷"
    shift: bool = False
    reading_direction: str = "rtl"
    page_layout: str = "facing"
    virtual_panels: str = "enabled"
    panel_movement: str = "vertical"
    image_preset: str = "kcc-legacy-like"
    image_custom: bool = False
    crop_mode: str = "off"
    crop_edge_threshold: float = CROP_STRENGTH_DEFAULT
    spread_fill_edge_threshold: float = SPREAD_CROP_STRENGTH_DEFAULT
    spread_fill_inner_enabled: bool = False
    spread_fill_inner_edge_threshold: float = SPREAD_CROP_STRENGTH_DEFAULT
    crop_strength_semantics_version: int = CROP_STRENGTH_SEMANTICS_VERSION
    target_size_text: str = ""
    scribe_panel: bool = True
    panel_preset: str = "scribe_1240x1860"
    preserve_color: TriStateValue = "enabled"
    gamma_value: float = 1.8
    gamma_auto: bool = True
    contrast_value: float = 1.0
    contrast_auto: bool = True
    autocontrast: TriStateValue = "auto"
    autolevel: TriStateValue = "auto"
    jpeg_quality_value: int = 90
    jpeg_quality_auto: bool = True
    emit_kfx: bool = False
    shift_mode: str = "off"
    output_format: str = "kpf"
    kfx_plugin: str = ""
    jobs: int = JOBS_DEFAULT
    performance_mode: str = "balanced"
    language: str = "zh"
    theme_mode: str = "light"
