from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..plugin_registry import DEFAULT_KFX_PLUGIN_ID


TriStateValue = Literal["auto", "enabled", "disabled"]

TRI_STATE_OPTIONS = (
    ("auto", "ui.auto"),
    ("enabled", "ui.option.enabled"),
    ("disabled", "ui.option.disabled"),
)

IMAGE_PRESET_OPTIONS = (
    ("none", "ui.image.preset.none"),
    ("standard", "ui.image.preset.standard"),
    ("bright", "ui.image.preset.enhanced"),
)

CROP_MODE_OPTIONS = (
    ("off", "ui.crop.mode.off"),
    ("smart", "ui.crop.mode.smart"),
    ("spread-safe", "ui.crop.mode.facing.safe"),
    ("spread-fill", "ui.crop.mode.facing.fill"),
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
    shift: bool = False
    reading_direction: str = "rtl"
    page_layout: str = "facing"
    virtual_panels: str = "enabled"
    panel_movement: str = "vertical"
    image_preset: str = "bright"
    crop_mode: str = "off"
    target_size_text: str = ""
    scribe_panel: bool = True
    panel_preset: str = "scribe_1240x1860"
    preserve_color: TriStateValue = "auto"
    gamma_value: float = 1.8
    gamma_auto: bool = True
    autocontrast: TriStateValue = "auto"
    autolevel: TriStateValue = "auto"
    jpeg_quality_value: int = 90
    jpeg_quality_auto: bool = True
    emit_kfx: bool = False
    shift_mode: str = "off"
    output_format: str = "kpf"
    kfx_plugin: str = DEFAULT_KFX_PLUGIN_ID
    jobs: int = 1
    language: str = "zh"
