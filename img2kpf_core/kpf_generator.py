from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import math
import os
import shutil
import sqlite3
import struct
import sys
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "img2kpf_core"

from .kfx_direct import convert_kpf_to_kfx
from .plugin_registry import DEFAULT_KFX_PLUGIN_ID


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

IMAGE_PRESET_ALIASES = {
    "none": "none",
    "standard": "standard",
    "bright": "bright",
    "kcc-current-like": "standard",
    "kcc-legacy-like": "bright",
}

CROP_MODE_ALIASES = {
    "off": "off",
    "smart": "smart",
    "spread-fill": "spread-fill",
}

VALID_IMAGE_PRESETS = ("none", "standard", "bright")
VALID_CROP_MODES = ("off", "smart", "spread-fill")
PERFORMANCE_MODE_ALIASES = {
    "eco": "eco",
    "balanced": "balanced",
    "max": "max",
}
VALID_PERFORMANCE_MODES = ("eco", "balanced", "max")

SQLITE_FINGERPRINT_OFFSET = 1024
SQLITE_FINGERPRINT_RECORD_LEN = 1024
SQLITE_FINGERPRINT_DATA_RECORD_LEN = 1024
SQLITE_FINGERPRINT_DATA_RECORD_COUNT = 1024
SQLITE_FINGERPRINT_SIGNATURE = b"\xfa\x50\x0a\x5f"

ION_SIGNATURE = b"\xE0\x01\x00\xEA"
NAME_REF_ANNOTATION_SID = 598

SYMBOL_DEFAULT_READING_ORDER = 351
SYMBOL_SECTION = 260
SYMBOL_STORYLINE = 259
SYMBOL_STRUCTURE = 608
SYMBOL_EXTERNAL_RESOURCE = 164
SYMBOL_AUXILIARY_DATA = 597
SYMBOL_BOOK_METADATA = 490
SYMBOL_DOCUMENT_DATA = 538
SYMBOL_METADATA = 258
SYMBOL_SECTION_POSITION_ID_MAP = 609
SYMBOL_SECTION_PID_COUNT_MAP = 611

SYMBOL_LAYOUT_SECTION_KIND = 441
SYMBOL_LAYOUT_SECTION_ROLE = 437
SYMBOL_LAYOUT_SECTION_TYPE = 270
SYMBOL_LAYOUT_HEAD_ALIGN = 320
SYMBOL_LAYOUT_HEAD_ROLE = 326
SYMBOL_LAYOUT_HEAD_SINGLE_ROLE = 323
SYMBOL_LAYOUT_HEAD_TYPE = 270
SYMBOL_LAYOUT_TAIL_TYPE = 271
SYMBOL_LAYOUT_TAIL_ROLE = 324
SYMBOL_LAYOUT_COMMON = 377
SYMBOL_CONTENT_FEATURES = 585

SYMBOL_IMAGE_JPEG = 285
SYMBOL_IMAGE_PNG = 284

TEMPLATE_STATIC_FRAGMENT_IDS = (
    "$ion_symbol_table",
    "max_id",
    "content_features",
    "book_navigation",
)

DEFAULT_TEMPLATE_ASSET_PATH = Path(__file__).with_name("assets") / "kc_comics_rtl_facing.json"

KCC_CROP_POWER = 2.0
KCC_CROP_EDGE_SLICE_RATIO = 0.02
KCC_CROP_SIDE_RATIO_LIMIT = 0.10
KCC_CROP_MIN_DIMENSION_RATIO = 0.60
KCC_CROP_PRESERVE_MARGIN_RATIO = 0.01
KCC_FILL_LOW_INFO_DARK_THRESHOLD = 28
KCC_FILL_LOW_INFO_LIGHT_THRESHOLD = 227
KCC_FILL_LOW_INFO_DOMINANT_RATIO = 0.985
KCC_FILL_LOW_INFO_STDDEV_MAX = 14.0
KCC_FILL_LOW_INFO_DARK_MEAN_MAX = 78.0
KCC_FILL_LOW_INFO_LIGHT_MEAN_MIN = 177.0
KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO = 0.96
KCC_FILL_OUTER_LOW_INFO_STDDEV_MAX = 8.0
KCC_FILL_OUTER_LOW_INFO_DARK_MEAN_MAX = 72.0
KCC_FILL_OUTER_LOW_INFO_LIGHT_MEAN_MIN = 232.0
KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN = 0.70
KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX = 1.00
KCC_CROP_STRENGTH_DEFAULT = 1.00
KCC_RATIO_FRAME_SCORE_SIZE = 256
KCC_RATIO_FRAME_CANDIDATE_STEPS = 24
KCC_INNER_TRADEOFF_BASE_IMPROVEMENT = 0.10
KCC_INNER_TRADEOFF_FULL_IMPROVEMENT = 0.45

ReadingDirection = Literal["rtl", "ltr"]
PageLayout = Literal["facing", "single"]
PanelMovement = Literal["vertical", "horizontal"]
PagePosition = Literal["first", "second"]
HorizontalAnchor = Literal["left", "center", "right"]


def _normalize_named_value(
    value: str,
    aliases: dict[str, str],
    *,
    argument_name: str,
) -> str:
    normalized = value.strip().lower()
    if normalized in aliases:
        return aliases[normalized]
    supported = ", ".join(sorted(key for key, canonical in aliases.items() if key == canonical))
    raise ValueError(f"Unsupported {argument_name}: {value}. Supported values: {supported}")


def normalize_image_preset(value: str) -> str:
    return _normalize_named_value(value, IMAGE_PRESET_ALIASES, argument_name="image preset")


def normalize_crop_mode(value: str) -> str:
    return _normalize_named_value(value, CROP_MODE_ALIASES, argument_name="crop mode")


def normalize_performance_mode(value: str) -> str:
    return _normalize_named_value(value, PERFORMANCE_MODE_ALIASES, argument_name="performance mode")


def resolve_preprocessing_workers(performance_mode: str) -> int:
    cpu_count = os.cpu_count() or 4
    mode = normalize_performance_mode(performance_mode)
    if mode == "eco":
        return 1
    if mode == "balanced":
        return max(2, min(8, cpu_count // 2))
    return max(2, min(16, cpu_count))


def resolve_parallel_jobs(performance_mode: str) -> int:
    cpu_count = os.cpu_count() or 4
    mode = normalize_performance_mode(performance_mode)
    if mode == "eco":
        return 1
    if mode == "balanced":
        return max(1, min(4, cpu_count // 4))
    return max(2, min(8, cpu_count // 2))


def parse_image_preset(value: str) -> str:
    try:
        return normalize_image_preset(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_crop_mode(value: str) -> str:
    try:
        return normalize_crop_mode(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_performance_mode(value: str) -> str:
    try:
        return normalize_performance_mode(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


@dataclass(frozen=True)
class LayoutOptions:
    reading_direction: ReadingDirection = "rtl"
    page_layout: PageLayout = "facing"
    virtual_panels: bool = True
    panel_movement: PanelMovement = "vertical"


@dataclass(frozen=True)
class BuildStageProgress:
    phase: str
    current: int
    total: int
    current_name: str = ""
    workers: int = 1
    indeterminate: bool = False


BuildProgressCallback = Callable[[BuildStageProgress], None]


class BuildCancelled(RuntimeError):
    pass


def raise_if_build_cancelled(stop_requested: Callable[[], bool] | None) -> None:
    if stop_requested is not None and stop_requested():
        raise BuildCancelled("ui.task.cancelled")


@dataclass(frozen=True)
class LayoutPageSlot:
    page_number: int
    page_position: PagePosition
    source_index: int | None
    is_shift_blank: bool = False


def build_layout_page_groups(
    image_count: int,
    shift_blank_count: int = 0,
    page_layout: PageLayout = "facing",
) -> list[tuple[LayoutPageSlot, ...]]:
    if page_layout not in {"facing", "single"}:
        raise ValueError(f"不支持的页面布局：{page_layout}")
    if image_count < 0:
        raise ValueError("image_count 不能为负数。")
    if shift_blank_count < 0:
        raise ValueError("shift_blank_count 不能为负数。")
    if shift_blank_count and page_layout != "facing":
        raise ValueError("Single 单页布局不支持首页偏移。")

    group_size = 1 if page_layout == "single" else 2
    total_pages = image_count + shift_blank_count
    page_groups: list[tuple[LayoutPageSlot, ...]] = []

    for group_start in range(0, total_pages, group_size):
        group: list[LayoutPageSlot] = []
        for offset in range(group_size):
            effective_index = group_start + offset
            if effective_index >= total_pages:
                break
            is_shift_blank = effective_index < shift_blank_count
            group.append(
                LayoutPageSlot(
                    page_number=effective_index + 1,
                    page_position="first" if page_layout == "single" or offset == 0 else "second",
                    source_index=None if is_shift_blank else effective_index - shift_blank_count,
                    is_shift_blank=is_shift_blank,
                )
            )
        if group:
            page_groups.append(tuple(group))

    return page_groups


def natural_sort_key(value: str) -> list[object]:
    import re

    parts = re.split(r"(\d+)", value.lower())
    key: list[object] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part)
    return key


def compute_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def current_timestamp() -> str:
    return datetime.now().strftime("%Y-%b-%d %H:%M:%S")


def current_utc_log_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S UTC %Y")


def validate_layout_options(layout_options: LayoutOptions) -> LayoutOptions:
    if layout_options.reading_direction not in {"rtl", "ltr"}:
        raise ValueError(f"不支持的阅读方向：{layout_options.reading_direction}")
    if layout_options.page_layout not in {"facing", "single"}:
        raise ValueError(f"不支持的页面布局：{layout_options.page_layout}")
    if layout_options.panel_movement not in {"vertical", "horizontal"}:
        raise ValueError(f"不支持的面板移动方式：{layout_options.panel_movement}")
    return layout_options


def resolve_book_state(template_book_state: dict[str, object], layout_options: LayoutOptions) -> dict[str, object]:
    state = dict(template_book_state)

    if not layout_options.virtual_panels:
        book_reading_direction = 2 if layout_options.reading_direction == "rtl" else 1
        book_reading_option = 1
        book_virtual_panelmovement = 0
    elif layout_options.panel_movement == "horizontal":
        book_reading_direction = 2 if layout_options.reading_direction == "rtl" else 1
        book_reading_option = 2
        book_virtual_panelmovement = 1
    elif layout_options.reading_direction == "rtl":
        book_reading_direction = 1
        book_reading_option = 2
        book_virtual_panelmovement = 3
    else:
        book_reading_direction = 1
        book_reading_option = 2
        book_virtual_panelmovement = 2

    state.update(
        {
            "book_reading_direction": book_reading_direction,
            "book_reading_option": book_reading_option,
            "book_virtual_panelmovement": book_virtual_panelmovement,
        }
    )
    return state


def content_feature_definitions(layout_options: LayoutOptions) -> list[tuple[str, tuple[int, int]]]:
    features: list[tuple[str, tuple[int, int]]] = [("yj_non_pdf_fixed_layout", (2, 0))]
    if layout_options.page_layout == "facing":
        features.append(("yj_double_page_spread", (1, 0)))
    if layout_options.page_layout == "single" or not layout_options.virtual_panels:
        features.append(("yj_publisher_panels", (2, 0)))
    return features


def build_content_features_blob(layout_options: LayoutOptions) -> bytes:
    feature_items = []
    for feature_name, version in content_feature_definitions(layout_options):
        feature_items.append(
            ion_struct(
                [
                    (586, ion_string("com.amazon.yjconversion")),
                    (492, ion_string(feature_name)),
                    (
                        589,
                        ion_struct(
                            [
                                (
                                    5,
                                    ion_struct(
                                        [
                                            (587, ion_int(version[0])),
                                            (588, ion_int(version[1])),
                                        ]
                                    ),
                                )
                            ]
                        ),
                    ),
                ]
            )
        )

    payload = ion_struct(
        [
            (598, ion_symbol(SYMBOL_CONTENT_FEATURES)),
            (590, ion_list(feature_items)),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_CONTENT_FEATURES], payload))


def apply_layout_options(template_assets: "TemplateAssets", layout_options: LayoutOptions) -> "TemplateAssets":
    resolved_options = validate_layout_options(layout_options)
    static_fragments = dict(template_assets.static_fragments)
    static_fragments["content_features"] = FragmentRow(
        fragment_id="content_features",
        payload_type="blob",
        payload_value=build_content_features_blob(resolved_options),
        element_type="content_features",
    )
    return replace(
        template_assets,
        book_state=resolve_book_state(template_assets.book_state, resolved_options),
        static_fragments=static_fragments,
        template_direction=resolved_options.reading_direction,
    )


def find_input_images(input_dir: Path) -> list[Path]:
    images = [
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(images, key=lambda path: natural_sort_key(path.name))


def unwrap_sqlite_fingerprint(data: bytes) -> bytes:
    if (
        len(data) < SQLITE_FINGERPRINT_OFFSET + SQLITE_FINGERPRINT_RECORD_LEN
        or data[
            SQLITE_FINGERPRINT_OFFSET : SQLITE_FINGERPRINT_OFFSET + len(SQLITE_FINGERPRINT_SIGNATURE)
        ]
        != SQLITE_FINGERPRINT_SIGNATURE
    ):
        return data

    unwrapped = data
    data_offset = SQLITE_FINGERPRINT_OFFSET
    while len(unwrapped) >= data_offset + SQLITE_FINGERPRINT_RECORD_LEN:
        if (
            unwrapped[data_offset : data_offset + len(SQLITE_FINGERPRINT_SIGNATURE)]
            != SQLITE_FINGERPRINT_SIGNATURE
        ):
            break
        unwrapped = (
            unwrapped[:data_offset]
            + unwrapped[data_offset + SQLITE_FINGERPRINT_RECORD_LEN :]
        )
        data_offset += SQLITE_FINGERPRINT_DATA_RECORD_LEN * SQLITE_FINGERPRINT_DATA_RECORD_COUNT
    return unwrapped


def read_png_size_from_bytes(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG 文件头无效。")
    return struct.unpack(">II", data[16:24])


def read_jpeg_size_from_bytes(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xFF\xD8":
        raise ValueError("JPEG 文件头无效。")

    offset = 2
    while offset + 1 < len(data):
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break

        marker = data[offset]
        offset += 1

        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        if offset + 2 > len(data):
            break

        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break

        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_length < 7:
                break
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height

        offset += segment_length

    raise ValueError("无法从 JPEG 中解析尺寸。")


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    format_symbol: int
    normalized_ext: str


@dataclass(frozen=True)
class CropMargins:
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @classmethod
    def from_box(cls, crop_box: tuple[int, int, int, int], image_size: tuple[int, int]) -> CropMargins:
        width, height = image_size
        left, top, right, bottom = crop_box
        return cls(
            left=max(0, left),
            top=max(0, top),
            right=max(0, width - right),
            bottom=max(0, height - bottom),
        )

    def to_box(self, image_size: tuple[int, int]) -> tuple[int, int, int, int]:
        width, height = image_size
        left = max(0, min(width, self.left))
        top = max(0, min(height, self.top))
        right = max(left, min(width, width - self.right))
        bottom = max(top, min(height, height - self.bottom))
        return left, top, right, bottom


@dataclass(frozen=True)
class ImageProcessingOptions:
    target_size: tuple[int, int] | None = None
    crop_mode: str = "off"
    crop_edge_threshold: float = KCC_CROP_STRENGTH_DEFAULT
    spread_fill_edge_threshold: float = KCC_CROP_STRENGTH_DEFAULT
    spread_fill_inner_enabled: bool = False
    spread_fill_inner_edge_threshold: float = KCC_CROP_STRENGTH_DEFAULT
    preserve_color: bool = True
    gamma: float = 1.0
    contrast: float = 1.0
    autocontrast: bool = False
    autolevel: bool = False
    jpeg_quality: int = 90
    preprocessing_workers: int = 1

    @property
    def enabled(self) -> bool:
        return (
            self.target_size is not None
            or self.crop_mode != "off"
            or not self.preserve_color
            or self.gamma != 1.0
            or self.contrast != 1.0
            or self.autocontrast
            or self.autolevel
            or self.jpeg_quality != 90
        )


def read_image_info(path: Path) -> ImageInfo:
    suffix = path.suffix.lower()
    data = path.read_bytes()
    if suffix in {".jpg", ".jpeg"}:
        width, height = read_jpeg_size_from_bytes(data)
        return ImageInfo(width=width, height=height, format_symbol=SYMBOL_IMAGE_JPEG, normalized_ext=".jpg")
    if suffix == ".png":
        width, height = read_png_size_from_bytes(data)
        return ImageInfo(width=width, height=height, format_symbol=SYMBOL_IMAGE_PNG, normalized_ext=".png")
    raise ValueError(f"暂不支持的图片格式：{path.name}")


def load_pillow():
    try:
        from PIL import Image, ImageFile, ImageOps, ImageStat
    except ImportError as exc:
        raise RuntimeError(
            "图像预处理需要 Pillow。请先运行 `uv pip install Pillow`，或使用 `pip install -r requirements.txt`。"
        ) from exc

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    return Image, ImageOps, ImageStat


def parse_size(value: str) -> tuple[int, int]:
    normalized = value.lower().replace("×", "x")
    if "x" not in normalized:
        raise argparse.ArgumentTypeError("尺寸格式必须是 WIDTHxHEIGHT，例如 1240x1860。")
    width_text, height_text = normalized.split("x", 1)
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("尺寸格式必须是 WIDTHxHEIGHT，例如 1240x1860。") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("尺寸宽高必须大于 0。")
    return width, height


def safe_autocontrast(image):
    _, ImageOps, _ = load_pillow()
    try:
        return ImageOps.autocontrast(image, preserve_tone=True)
    except TypeError:
        return ImageOps.autocontrast(image)


def gamma_correct_band(image_band, gamma: float):
    if gamma == 1.0:
        return image_band
    table = [
        max(0, min(255, int(round(255 * ((index / 255) ** gamma)))))
        for index in range(256)
    ]
    return image_band.point(table)


def contrast_correct_band(image_band, contrast: float):
    if contrast == 1.0:
        return image_band
    midpoint = 128
    return image_band.point(
        lambda value: max(0, min(255, int(round(midpoint + (value - midpoint) * contrast))))
    )


def autolevel_band(image_band):
    histogram = image_band.histogram()
    dark_histogram = histogram[:64]
    if not dark_histogram:
        return image_band
    darkest = max(range(len(dark_histogram)), key=dark_histogram.__getitem__)
    if darkest <= 0:
        return image_band
    return image_band.point(lambda value: darkest if value < darkest else value)


def low_contrast_band(image_band) -> bool:
    minimum, maximum = image_band.getextrema()
    return maximum - minimum < 159


def detect_border_background(gray_image) -> str | None:
    _, _, ImageStat = load_pillow()
    width, height = gray_image.size
    patch = max(8, min(width, height) // 50)
    boxes = [
        (0, 0, patch, patch),
        (width - patch, 0, width, patch),
        (0, height - patch, patch, height),
        (width - patch, height - patch, width, height),
    ]
    means = [ImageStat.Stat(gray_image.crop(box)).mean[0] for box in boxes]
    if sum(value >= 240 for value in means) >= 3:
        return "white"
    if sum(value <= 15 for value in means) >= 3:
        return "black"
    return None


def threshold_from_crop_power(power: float) -> int:
    clamped = max(0.0, min(3.0, power))
    return max(1, min(255, int(round(8 * (2**clamped)))))


def border_histogram_is_background(histogram: list[int], background: str) -> bool:
    total = sum(histogram)
    if total == 0:
        return True
    if background == "white":
        background_pixels = sum(histogram[245:])
    else:
        background_pixels = sum(histogram[:11])
    return background_pixels / total >= 0.985


def trim_border(gray_image, background: str) -> tuple[int, int, int, int]:
    width, height = gray_image.size
    max_trim_x = min(width // 6, 256)
    max_trim_y = min(height // 6, 256)

    left = 0
    while left < max_trim_x and border_histogram_is_background(
        gray_image.crop((left, 0, left + 1, height)).histogram(), background
    ):
        left += 1

    right = 0
    while right < max_trim_x and border_histogram_is_background(
        gray_image.crop((width - right - 1, 0, width - right, height)).histogram(), background
    ):
        right += 1

    top = 0
    while top < max_trim_y and border_histogram_is_background(
        gray_image.crop((0, top, width, top + 1)).histogram(), background
    ):
        top += 1

    bottom = 0
    while bottom < max_trim_y and border_histogram_is_background(
        gray_image.crop((0, height - bottom - 1, width, height - bottom)).histogram(), background
    ):
        bottom += 1

    if left + right >= width or top + bottom >= height:
        return 0, 0, width, height
    return left, top, width - right, height - bottom


def ignore_small_edge_noise(mask_image) -> None:
    width, height = mask_image.size
    edge_width = max(1, int(round(width * KCC_CROP_EDGE_SLICE_RATIO)))
    edge_height = max(1, int(round(height * KCC_CROP_EDGE_SLICE_RATIO)))
    boxes = [
        (0, 0, width, edge_height),
        (0, height - edge_height, width, height),
        (0, 0, edge_width, height),
        (width - edge_width, 0, width, height),
    ]
    for box in boxes:
        edge = mask_image.crop(box)
        total_pixels = max(1, edge.width * edge.height)
        content_ratio = edge.histogram()[255] / total_pixels
        if 0 < content_ratio < KCC_CROP_EDGE_SLICE_RATIO:
            mask_image.paste(0, box)


def clamp_crop_box_to_side_ratio(
    crop_box: tuple[int, int, int, int],
    image_size: tuple[int, int],
    side_ratio_limit: float = KCC_CROP_SIDE_RATIO_LIMIT,
) -> tuple[int, int, int, int]:
    width, height = image_size
    max_trim_x = int(width * side_ratio_limit)
    max_trim_y = int(height * side_ratio_limit)
    margins = CropMargins.from_box(crop_box, image_size)
    limited = CropMargins(
        left=min(margins.left, max_trim_x),
        top=min(margins.top, max_trim_y),
        right=min(margins.right, max_trim_x),
        bottom=min(margins.bottom, max_trim_y),
    )
    return limited.to_box(image_size)


def crop_box_is_safe(
    crop_box: tuple[int, int, int, int],
    image_size: tuple[int, int],
    minimum_dimension_ratio: float = KCC_CROP_MIN_DIMENSION_RATIO,
) -> bool:
    width, height = image_size
    left, top, right, bottom = crop_box
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        return False
    retained_width = right - left
    retained_height = bottom - top
    return (
        retained_width >= width * minimum_dimension_ratio
        and retained_height >= height * minimum_dimension_ratio
    )


def expand_crop_box(
    crop_box: tuple[int, int, int, int],
    image_size: tuple[int, int],
    preserve_margin_ratio: float = KCC_CROP_PRESERVE_MARGIN_RATIO,
) -> tuple[int, int, int, int]:
    width, height = image_size
    pad_x = max(8, int(round(width * preserve_margin_ratio)))
    pad_y = max(8, int(round(height * preserve_margin_ratio)))
    left, top, right, bottom = crop_box
    return (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )


def build_kcc_crop_box(image) -> tuple[int, int, int, int] | None:
    _, ImageOps, _ = load_pillow()
    from PIL import ImageFilter

    gray = image.convert("L")
    background = detect_border_background(gray)
    if background is None:
        return None

    working = gray if background == "white" else ImageOps.invert(gray)
    threshold = threshold_from_crop_power(KCC_CROP_POWER)
    processed = ImageOps.autocontrast(working, cutoff=1).filter(ImageFilter.BoxBlur(1))
    mask = processed.point(lambda value: 255 if value <= threshold else 0)
    ignore_small_edge_noise(mask)
    crop_box = mask.getbbox()
    if crop_box is None:
        return None

    crop_box = expand_crop_box(crop_box, image.size)
    crop_box = clamp_crop_box_to_side_ratio(crop_box, image.size)
    if crop_box == (0, 0, image.size[0], image.size[1]):
        return None
    if not crop_box_is_safe(crop_box, image.size):
        return None
    return crop_box


def apply_crop_box(image, crop_box: tuple[int, int, int, int] | None):
    if crop_box is None or crop_box == (0, 0, image.size[0], image.size[1]):
        return image
    return image.crop(crop_box)


def get_facing_page_horizontal_roles(
    page_position: str,
    template_direction: str | None,
) -> tuple[str, str]:
    is_rtl = template_direction == "rtl"
    if page_position == "first":
        return ("right", "left") if is_rtl else ("left", "right")
    return ("left", "right") if is_rtl else ("right", "left")


def get_outer_inner_horizontal_margins(
    margins: CropMargins,
    page_position: str,
    template_direction: str | None,
) -> tuple[int, int]:
    outer_edge, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
    outer = margins.right if outer_edge == "right" else margins.left
    inner = margins.right if inner_edge == "right" else margins.left
    return outer, inner


def horizontal_anchor_for_outer_edge(
    page_position: str,
    template_direction: str | None,
) -> HorizontalAnchor:
    outer_edge, _ = get_facing_page_horizontal_roles(page_position, template_direction)
    return "left" if outer_edge == "left" else "right"


def build_facing_crop_box(
    image_size: tuple[int, int],
    page_position: str,
    template_direction: str | None,
    outer_ratio: float,
    top_ratio: float,
    bottom_ratio: float,
    inner_ratio: float = 0.0,
) -> tuple[int, int, int, int]:
    width, height = image_size
    outer = int(round(width * outer_ratio))
    inner = int(round(width * inner_ratio))
    top = int(round(height * top_ratio))
    bottom = int(round(height * bottom_ratio))
    outer_edge, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
    margins = CropMargins(
        left=outer if outer_edge == "left" else inner if inner_edge == "left" else 0,
        top=top,
        right=outer if outer_edge == "right" else inner if inner_edge == "right" else 0,
        bottom=bottom,
    )
    return margins.to_box(image_size)


def build_outer_only_crop_box(
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int] | None,
    page_position: str,
    template_direction: str | None,
) -> tuple[int, int, int, int] | None:
    if crop_box is None:
        return None

    margins = CropMargins.from_box(crop_box, image_size)
    outer, _ = get_outer_inner_horizontal_margins(margins, page_position, template_direction)
    width, height = image_size
    outer_ratio = outer / width
    top_ratio = margins.top / height
    bottom_ratio = margins.bottom / height
    outer_only_crop_box = build_facing_crop_box(
        image_size,
        page_position,
        template_direction,
        outer_ratio=outer_ratio,
        top_ratio=top_ratio,
        bottom_ratio=bottom_ratio,
        inner_ratio=0.0,
    )
    if not crop_box_is_safe(outer_only_crop_box, image_size):
        return None
    return outer_only_crop_box


def measure_contiguous_background_margin(
    gray_image,
    side: str,
    background: str,
    max_trim: int,
) -> int:
    width, height = gray_image.size
    if width <= 1 or height <= 0:
        return 0

    limit = max(0, min(max_trim, width - 1))
    trim = 0
    while trim < limit:
        if side == "left":
            column_box = (trim, 0, trim + 1, height)
        else:
            column_box = (width - trim - 1, 0, width - trim, height)
        if not border_histogram_is_background(gray_image.crop(column_box).histogram(), background):
            break
        trim += 1
    return trim


def histogram_mean_and_stddev(histogram: list[int]) -> tuple[float, float]:
    total = sum(histogram)
    if total <= 0:
        return 0.0, 0.0
    mean = sum(index * count for index, count in enumerate(histogram)) / total
    variance = sum(((index - mean) ** 2) * count for index, count in enumerate(histogram)) / total
    return mean, math.sqrt(max(0.0, variance))


def column_histogram_is_low_information(histogram: list[int]) -> bool:
    total = sum(histogram)
    if total <= 0:
        return True

    dark_ratio = sum(histogram[: KCC_FILL_LOW_INFO_DARK_THRESHOLD + 1]) / total
    light_ratio = sum(histogram[KCC_FILL_LOW_INFO_LIGHT_THRESHOLD :]) / total
    mean, stddev = histogram_mean_and_stddev(histogram)

    if dark_ratio >= KCC_FILL_LOW_INFO_DOMINANT_RATIO or light_ratio >= KCC_FILL_LOW_INFO_DOMINANT_RATIO:
        return True
    if stddev <= KCC_FILL_LOW_INFO_STDDEV_MAX and (
        mean <= KCC_FILL_LOW_INFO_DARK_MEAN_MAX or mean >= KCC_FILL_LOW_INFO_LIGHT_MEAN_MIN
    ):
        return True
    return False


def column_histogram_is_outer_low_information(
    histogram: list[int],
    dominant_ratio_threshold: float = KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
) -> bool:
    total = sum(histogram)
    if total <= 0:
        return True

    dominant_ratio_threshold = max(
        KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN,
        min(KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX, dominant_ratio_threshold),
    )
    dark_ratio = sum(histogram[: KCC_FILL_LOW_INFO_DARK_THRESHOLD + 1]) / total
    light_ratio = sum(histogram[KCC_FILL_LOW_INFO_LIGHT_THRESHOLD :]) / total
    mean, stddev = histogram_mean_and_stddev(histogram)

    if dark_ratio >= dominant_ratio_threshold:
        return True
    if light_ratio >= dominant_ratio_threshold:
        return True
    return stddev <= KCC_FILL_OUTER_LOW_INFO_STDDEV_MAX and (
        mean <= KCC_FILL_OUTER_LOW_INFO_DARK_MEAN_MAX or mean >= KCC_FILL_OUTER_LOW_INFO_LIGHT_MEAN_MIN
    )


def column_histogram_is_light_low_information(
    histogram: list[int],
    dominant_ratio_threshold: float = KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
) -> bool:
    total = sum(histogram)
    if total <= 0:
        return True

    dominant_ratio_threshold = max(
        KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN,
        min(KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX, dominant_ratio_threshold),
    )
    light_ratio = sum(histogram[KCC_FILL_LOW_INFO_LIGHT_THRESHOLD :]) / total
    mean, stddev = histogram_mean_and_stddev(histogram)

    if light_ratio >= dominant_ratio_threshold:
        return True
    return stddev <= KCC_FILL_OUTER_LOW_INFO_STDDEV_MAX and mean >= KCC_FILL_OUTER_LOW_INFO_LIGHT_MEAN_MIN


def measure_low_information_margin(
    gray_image,
    side: str,
    max_trim: int,
) -> int:
    width, height = gray_image.size
    if width <= 1 or height <= 0:
        return 0

    limit = max(0, min(max_trim, width - 1))
    trim = 0
    while trim < limit:
        if side == "left":
            column_box = (trim, 0, trim + 1, height)
        else:
            column_box = (width - trim - 1, 0, width - trim, height)
        if not column_histogram_is_low_information(gray_image.crop(column_box).histogram()):
            break
        trim += 1
    return trim


def measure_outer_low_information_margin(
    gray_image,
    side: str,
    max_trim: int,
    dominant_ratio_threshold: float = KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
) -> int:
    width, height = gray_image.size
    if width <= 1 or height <= 0:
        return 0

    limit = max(0, min(max_trim, width - 1))
    cumulative_histogram = [0] * 256
    trim = 0
    while trim < limit:
        if side == "left":
            column_box = (trim, 0, trim + 1, height)
        else:
            column_box = (width - trim - 1, 0, width - trim, height)
        column_histogram = gray_image.crop(column_box).histogram()
        cumulative_histogram = [
            current + added
            for current, added in zip(cumulative_histogram, column_histogram)
        ]
        if not column_histogram_is_outer_low_information(
            cumulative_histogram,
            dominant_ratio_threshold=dominant_ratio_threshold,
        ):
            break
        trim += 1
    return trim


def measure_light_low_information_margin(
    gray_image,
    side: str,
    max_trim: int,
    dominant_ratio_threshold: float = KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
) -> int:
    width, height = gray_image.size
    if width <= 1 or height <= 0:
        return 0

    limit = max(0, min(max_trim, width - 1))
    cumulative_histogram = [0] * 256
    trim = 0
    while trim < limit:
        if side == "left":
            column_box = (trim, 0, trim + 1, height)
        else:
            column_box = (width - trim - 1, 0, width - trim, height)
        column_histogram = gray_image.crop(column_box).histogram()
        cumulative_histogram = [
            current + added
            for current, added in zip(cumulative_histogram, column_histogram)
        ]
        if not column_histogram_is_light_low_information(
            cumulative_histogram,
            dominant_ratio_threshold=dominant_ratio_threshold,
        ):
            break
        trim += 1
    return trim


def measure_vertical_low_information_margin(
    image,
    crop_box: tuple[int, int, int, int],
    side: str,
    dominant_ratio_threshold: float = KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
) -> int:
    gray = image.convert("L")
    left, top, right, bottom = crop_box
    focused = gray.crop((left, top, right, bottom))
    width, height = focused.size
    if width <= 0 or height <= 1:
        return 0
    max_trim = int(height * KCC_CROP_SIDE_RATIO_LIMIT)
    limit = max(0, min(max_trim, height - 1))
    cumulative_histogram = [0] * 256
    trim = 0
    while trim < limit:
        if side == "top":
            row_box = (0, trim, width, trim + 1)
        else:
            row_box = (0, height - trim - 1, width, height)
        row_histogram = focused.crop(row_box).histogram()
        cumulative_histogram = [
            current + added
            for current, added in zip(cumulative_histogram, row_histogram)
        ]
        if not column_histogram_is_outer_low_information(
            cumulative_histogram,
            dominant_ratio_threshold=dominant_ratio_threshold,
        ):
            break
        trim += 1
    return trim


def measure_inner_white_margin(
    image,
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
) -> int:
    gray = image.convert("L")
    background = detect_border_background(gray)
    if background != "white":
        return 0

    left, top, right, bottom = crop_box
    focused = gray.crop((left, top, right, bottom))
    _, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
    max_trim = int(focused.size[0] * KCC_CROP_SIDE_RATIO_LIMIT)
    return measure_contiguous_background_margin(focused, inner_edge, "white", max_trim)


def edge_name_for_role(
    page_position: str,
    template_direction: str | None,
    edge_role: str,
) -> str:
    outer_edge, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
    return inner_edge if edge_role == "inner" else outer_edge


def measure_detected_background_margin(
    image,
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    edge_role: str,
) -> int:
    gray = image.convert("L")
    background = detect_border_background(gray)
    if background is None:
        return 0

    left, top, right, bottom = crop_box
    focused = gray.crop((left, top, right, bottom))
    side = edge_name_for_role(page_position, template_direction, edge_role)
    max_trim = int(focused.size[0] * KCC_CROP_SIDE_RATIO_LIMIT)
    return measure_contiguous_background_margin(focused, side, background, max_trim)


def measure_low_information_edge_margin(
    image,
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    edge_role: str,
    dominant_ratio_threshold: float | None = None,
) -> int:
    gray = image.convert("L")
    left, top, right, bottom = crop_box
    focused = gray.crop((left, top, right, bottom))
    side = edge_name_for_role(page_position, template_direction, edge_role)
    max_trim = int(focused.size[0] * KCC_CROP_SIDE_RATIO_LIMIT)
    if dominant_ratio_threshold is not None:
        return measure_outer_low_information_margin(
            focused,
            side,
            max_trim,
            dominant_ratio_threshold=dominant_ratio_threshold,
        )
    return measure_low_information_margin(focused, side, max_trim)


def measure_low_information_edge_budget(
    image,
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    edge_role: str,
    max_trim: int,
) -> int:
    if max_trim <= 0:
        return 0

    gray = image.convert("L")
    background = detect_border_background(gray)
    left, top, right, bottom = crop_box
    focused = gray.crop((left, top, right, bottom))
    side = edge_name_for_role(page_position, template_direction, edge_role)
    limit = max(0, min(max_trim, focused.size[0] - 1))
    detected = 0
    if background is not None:
        detected = measure_contiguous_background_margin(focused, side, background, limit)
    if edge_role == "inner":
        light_information = measure_light_low_information_margin(
            focused,
            side,
            limit,
            dominant_ratio_threshold=KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
        )
        return max(detected if background == "white" else 0, light_information)
    low_information = measure_outer_low_information_margin(
        focused,
        side,
        limit,
        dominant_ratio_threshold=KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
    )
    return max(detected, low_information)


def expand_crop_box_towards_target_aspect(
    crop_box: tuple[int, int, int, int],
    image_size: tuple[int, int],
    target_size: tuple[int, int] | None,
) -> tuple[int, int, int, int]:
    if target_size is None:
        return crop_box

    image_width, image_height = image_size
    left, top, right, bottom = crop_box
    cropped_width = right - left
    cropped_height = bottom - top
    if cropped_width <= 0 or cropped_height <= 0:
        return crop_box

    target_aspect = target_size[0] / target_size[1]
    current_aspect = cropped_width / cropped_height
    if math.isclose(current_aspect, target_aspect, rel_tol=1e-4, abs_tol=1e-4):
        return crop_box

    if current_aspect > target_aspect:
        desired_height = math.ceil(cropped_width / target_aspect)
        extra_height = max(0, desired_height - cropped_height)
        if extra_height <= 0:
            return crop_box
        up_capacity = top
        down_capacity = image_height - bottom
        grow_top = min(up_capacity, extra_height // 2)
        grow_bottom = min(down_capacity, extra_height - grow_top)
        remaining = extra_height - (grow_top + grow_bottom)
        if remaining > 0:
            extra_top = min(up_capacity - grow_top, remaining)
            grow_top += extra_top
            remaining -= extra_top
        if remaining > 0:
            extra_bottom = min(down_capacity - grow_bottom, remaining)
            grow_bottom += extra_bottom
        return (
            left,
            max(0, top - grow_top),
            right,
            min(image_height, bottom + grow_bottom),
        )

    desired_width = math.ceil(cropped_height * target_aspect)
    extra_width = max(0, desired_width - cropped_width)
    if extra_width <= 0:
        return crop_box
    left_capacity = left
    right_capacity = image_width - right
    grow_left = min(left_capacity, extra_width // 2)
    grow_right = min(right_capacity, extra_width - grow_left)
    remaining = extra_width - (grow_left + grow_right)
    if remaining > 0:
        extra_left = min(left_capacity - grow_left, remaining)
        grow_left += extra_left
        remaining -= extra_left
    if remaining > 0:
        extra_right = min(right_capacity - grow_right, remaining)
        grow_right += extra_right
    return (
        max(0, left - grow_left),
        top,
        min(image_width, right + grow_right),
        bottom,
    )


def add_edge_trim_to_crop_box(
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    edge_role: str,
    trim_amount: int,
) -> tuple[int, int, int, int]:
    if trim_amount <= 0:
        return crop_box

    margins = CropMargins.from_box(crop_box, image_size)
    outer_edge, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
    updated = CropMargins(
        left=(
            margins.left + trim_amount
            if (edge_role == "inner" and inner_edge == "left") or (edge_role == "outer" and outer_edge == "left")
            else margins.left
        ),
        top=margins.top,
        right=(
            margins.right + trim_amount
            if (edge_role == "inner" and inner_edge == "right") or (edge_role == "outer" and outer_edge == "right")
            else margins.right
        ),
        bottom=margins.bottom,
    )
    return updated.to_box(image_size)


def _target_horizontal_trim_required(
    crop_box: tuple[int, int, int, int],
    target_size: tuple[int, int] | None,
) -> int:
    if target_size is None:
        return 0
    target_width, target_height = target_size
    if target_width <= 0 or target_height <= 0:
        return 0
    left, top, right, bottom = crop_box
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return 0
    target_aspect = target_width / target_height
    if width / height <= target_aspect:
        return 0
    return max(0, width - math.floor(height * target_aspect))


def _target_vertical_trim_required(
    crop_box: tuple[int, int, int, int],
    target_size: tuple[int, int] | None,
) -> int:
    if target_size is None:
        return 0
    target_width, target_height = target_size
    if target_width <= 0 or target_height <= 0:
        return 0
    left, top, right, bottom = crop_box
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return 0
    target_aspect = target_width / target_height
    if width / height >= target_aspect:
        return 0
    return max(0, height - math.floor(width / target_aspect))


def _add_direct_trim_to_crop_box(
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int],
    *,
    left_trim: int = 0,
    top_trim: int = 0,
    right_trim: int = 0,
    bottom_trim: int = 0,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = crop_box
    width, height = image_size
    adjusted = (
        max(0, min(width, left + max(0, left_trim))),
        max(0, min(height, top + max(0, top_trim))),
        max(0, min(width, right - max(0, right_trim))),
        max(0, min(height, bottom - max(0, bottom_trim))),
    )
    if not _crop_box_has_area_inside(adjusted, image_size):
        return crop_box
    return adjusted


def _scaled_budget_by_retention(budget: int, retention_ratio: float) -> int:
    if budget <= 0:
        return 0
    return max(0, min(budget, round(budget * _retention_to_trim_factor(retention_ratio))))


def _split_trim_by_budget_legacy(required: int, first_budget: int, second_budget: int) -> tuple[int, int]:
    if required <= 0:
        return 0, 0

    first_budget = max(0, first_budget)
    second_budget = max(0, second_budget)
    first = second = 0
    if first_budget > 0 or second_budget > 0:
        first_weight = first_budget * first_budget
        second_weight = second_budget * second_budget
        weight_total = first_weight + second_weight
        if weight_total > 0:
            first = min(first_budget, round(required * first_weight / weight_total))
            second = min(second_budget, required - first)

        remaining_budget = required - first - second
        while remaining_budget > 0:
            first_capacity = max(0, first_budget - first)
            second_capacity = max(0, second_budget - second)
            if first_capacity <= 0 and second_capacity <= 0:
                break
            if first_capacity >= second_capacity:
                added = min(first_capacity, remaining_budget)
                first += added
            else:
                added = min(second_capacity, remaining_budget)
                second += added
            remaining_budget -= added

    remaining = required - first - second
    if remaining > 0:
        first += remaining
    return first, second


def _split_trim_by_budget(required: int, first_budget: int, second_budget: int) -> tuple[int, int]:
    if required <= 0:
        return 0, 0

    first_budget = max(0, first_budget)
    second_budget = max(0, second_budget)
    available = first_budget + second_budget
    if available <= 0:
        return 0, 0

    required = min(required, available)
    first_weight = first_budget * first_budget
    second_weight = second_budget * second_budget
    weight_total = first_weight + second_weight
    if weight_total <= 0:
        return 0, 0

    first = min(first_budget, round(required * first_weight / weight_total))
    second = min(second_budget, required - first)

    remaining = required - first - second
    while remaining > 0:
        first_capacity = first_budget - first
        second_capacity = second_budget - second
        if first_capacity <= 0 and second_capacity <= 0:
            break
        if first_capacity >= second_capacity:
            added = min(first_capacity, remaining)
            first += added
        else:
            added = min(second_capacity, remaining)
            second += added
        remaining -= added

    return first, second


def _facing_budget_crop_box_to_target_aspect(
    image,
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    target_size: tuple[int, int] | None,
    outer_threshold: float,
    inner_enabled: bool,
    inner_threshold: float,
) -> tuple[int, int, int, int]:
    horizontal_required = _target_horizontal_trim_required(crop_box, target_size)
    vertical_required = _target_vertical_trim_required(crop_box, target_size)
    if horizontal_required <= 0 and vertical_required <= 0:
        return crop_box

    outer_edge, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
    outer_budget = _scaled_budget_by_retention(max(
        measure_detected_background_margin(image, crop_box, page_position, template_direction, "outer"),
        measure_low_information_edge_margin(
            image,
            crop_box,
            page_position,
            template_direction,
            "outer",
            dominant_ratio_threshold=KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
        ),
    ), outer_threshold)
    inner_budget = 0
    if inner_enabled:
        inner_budget = _scaled_budget_by_retention(max(
            measure_detected_background_margin(image, crop_box, page_position, template_direction, "inner"),
            measure_low_information_edge_margin(
                image,
                crop_box,
                page_position,
                template_direction,
                "inner",
                dominant_ratio_threshold=KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
            ),
        ), inner_threshold)

    outer_trim, _ = _split_trim_by_budget(horizontal_required, outer_budget, 0)
    inner_trim = 0
    remaining_horizontal = horizontal_required - outer_trim
    if inner_enabled and remaining_horizontal > 0:
        inner_trim, _ = _split_trim_by_budget(remaining_horizontal, inner_budget, 0)
    left_trim = right_trim = 0
    if outer_edge == "left":
        left_trim += outer_trim
    else:
        right_trim += outer_trim
    if inner_edge == "left":
        left_trim += inner_trim
    else:
        right_trim += inner_trim

    top_trim = bottom_trim = 0
    if vertical_required > 0:
        top_budget = _scaled_budget_by_retention(
            measure_vertical_low_information_margin(
                image,
                crop_box,
                "top",
                KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
            ),
            outer_threshold,
        )
        bottom_budget = _scaled_budget_by_retention(
            measure_vertical_low_information_margin(
                image,
                crop_box,
                "bottom",
                KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
            ),
            outer_threshold,
        )
        top_trim, bottom_trim = _split_trim_by_budget(vertical_required, top_budget, bottom_budget)

    return _add_direct_trim_to_crop_box(
        image.size,
        crop_box,
        left_trim=left_trim,
        top_trim=top_trim,
        right_trim=right_trim,
        bottom_trim=bottom_trim,
    )


def _clamp_retention_ratio(value: float) -> float:
    return max(
        KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN,
        min(KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX, value),
    )


def _target_ratio_frame_size(
    crop_box: tuple[int, int, int, int],
    target_size: tuple[int, int] | None,
) -> tuple[int, int] | None:
    left, top, right, bottom = crop_box
    crop_width = right - left
    crop_height = bottom - top
    if crop_width <= 0 or crop_height <= 0:
        return None
    if target_size is None:
        return crop_width, crop_height

    target_width, target_height = target_size
    if target_width <= 0 or target_height <= 0:
        return crop_width, crop_height

    target_aspect = target_width / target_height
    crop_aspect = crop_width / crop_height
    if crop_aspect > target_aspect:
        frame_height = crop_height
        frame_width = max(1, math.floor(frame_height * target_aspect))
    else:
        frame_width = crop_width
        frame_height = max(1, math.floor(frame_width / target_aspect))

    return min(frame_width, crop_width), min(frame_height, crop_height)


def _retention_to_trim_factor(value: float) -> float:
    retention = _clamp_retention_ratio(value)
    if retention >= KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX:
        return 0.0
    span = KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX - KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX - retention) / span))


def _scale_margins_by_factor(margins: CropMargins, factor: float) -> CropMargins:
    return CropMargins(
        left=round(margins.left * factor),
        top=round(margins.top * factor),
        right=round(margins.right * factor),
        bottom=round(margins.bottom * factor),
    )


def _interpolate_crop_box_by_retention(
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int],
    retention_ratio: float,
) -> tuple[int, int, int, int]:
    factor = _retention_to_trim_factor(retention_ratio)
    if factor <= 0:
        return _full_image_crop_box(image_size)
    margins = CropMargins.from_box(crop_box, image_size)
    return _scale_margins_by_factor(margins, factor).to_box(image_size)


def _build_facing_retention_crop_box(
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    outer_retention_ratio: float,
) -> tuple[int, int, int, int]:
    width, height = image_size
    margins = CropMargins.from_box(crop_box, image_size)
    outer_margin, _ = get_outer_inner_horizontal_margins(margins, page_position, template_direction)
    outer_factor = _retention_to_trim_factor(outer_retention_ratio)

    outer_ratio = round(outer_margin * outer_factor) / width if width > 0 else 0.0
    top_ratio = round(margins.top * outer_factor) / height if height > 0 else 0.0
    bottom_ratio = round(margins.bottom * outer_factor) / height if height > 0 else 0.0
    return build_facing_crop_box(
        image_size,
        page_position,
        template_direction,
        outer_ratio=outer_ratio,
        top_ratio=top_ratio,
        bottom_ratio=bottom_ratio,
        inner_ratio=0.0,
    )


def _candidate_positions(start: int, end: int, size: int) -> list[int]:
    last = max(start, end - size)
    travel = last - start
    if travel <= 0:
        return [start]

    positions = {start, last, start + travel // 2}
    steps = min(KCC_RATIO_FRAME_CANDIDATE_STEPS, max(1, travel))
    for index in range(steps + 1):
        positions.add(start + round(travel * index / steps))
    return sorted(positions)


def _build_information_integral(image):
    Image, _, _ = load_pillow()
    from PIL import ImageFilter

    gray = image.convert("L")
    background = detect_border_background(gray)
    width, height = gray.size
    scale = min(1.0, KCC_RATIO_FRAME_SCORE_SIZE / max(width, height))
    if scale < 1.0:
        resized_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        gray = gray.resize(resized_size, Image.Resampling.BILINEAR)

    edges = gray.filter(ImageFilter.FIND_EDGES)
    gray_pixels = gray.tobytes()
    edge_pixels = edges.tobytes()
    score_width, score_height = gray.size
    row_stride = score_width + 1
    integral = [0] * ((score_width + 1) * (score_height + 1))

    for y in range(score_height):
        row_total = 0
        base = y * score_width
        integral_base = (y + 1) * row_stride
        previous_integral_base = y * row_stride
        for x in range(score_width):
            gray_value = gray_pixels[base + x]
            edge_value = edge_pixels[base + x]
            if background == "black":
                tone_value = max(0, gray_value - 48)
            elif background == "white":
                tone_value = max(0, 220 - gray_value)
            else:
                tone_value = max(0, abs(gray_value - 128) - 48)
            row_total += min(255, edge_value * 2 + tone_value)
            integral[integral_base + x + 1] = integral[previous_integral_base + x + 1] + row_total

    return integral, score_width, score_height


def _scaled_region_sum(
    integral: list[int],
    score_width: int,
    score_height: int,
    image_size: tuple[int, int],
    box: tuple[int, int, int, int],
) -> int:
    image_width, image_height = image_size
    if image_width <= 0 or image_height <= 0:
        return 0

    left, top, right, bottom = box
    x0 = max(0, min(score_width, round(left * score_width / image_width)))
    y0 = max(0, min(score_height, round(top * score_height / image_height)))
    x1 = max(x0, min(score_width, round(right * score_width / image_width)))
    y1 = max(y0, min(score_height, round(bottom * score_height / image_height)))
    row_stride = score_width + 1
    return (
        integral[y1 * row_stride + x1]
        - integral[y0 * row_stride + x1]
        - integral[y1 * row_stride + x0]
        + integral[y0 * row_stride + x0]
    )


def _crop_box_has_area_inside(
    crop_box: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> bool:
    left, top, right, bottom = crop_box
    width, height = image_size
    return 0 <= left < right <= width and 0 <= top < bottom <= height


def optimize_ratio_frame_crop_box(
    image,
    crop_box: tuple[int, int, int, int],
    target_size: tuple[int, int] | None,
    *,
    page_position: str | None = None,
    template_direction: str | None = None,
    lock_inner: bool = False,
    inner_retention_ratio: float = 1.0,
    inner_trim_limit: int | None = None,
) -> tuple[int, int, int, int]:
    frame_size = _target_ratio_frame_size(crop_box, target_size)
    if frame_size is None:
        return crop_box

    frame_width, frame_height = frame_size
    left, top, right, bottom = crop_box
    if frame_width >= right - left and frame_height >= bottom - top:
        return crop_box

    x_positions = _candidate_positions(left, right, frame_width)
    inner_weight = _clamp_retention_ratio(inner_retention_ratio)
    if (lock_inner or inner_weight >= 0.99) and page_position is not None:
        inner_edge = edge_name_for_role(page_position, template_direction, "inner")
        if inner_edge == "left":
            x_positions = [left]
        else:
            x_positions = [max(left, right - frame_width)]
    elif page_position is not None:
        inner_edge = edge_name_for_role(page_position, template_direction, "inner")
        inner_factor = _retention_to_trim_factor(inner_weight)
        if inner_trim_limit is not None:
            allowed_inner_trim = inner_trim_limit
        elif inner_edge == "left":
            allowed_inner_trim = round(max(0, right - frame_width - left) * inner_factor)
        else:
            allowed_inner_trim = round(max(0, right - frame_width - left) * inner_factor)

        if allowed_inner_trim <= 0:
            if inner_edge == "left":
                x_positions = [left]
            else:
                x_positions = [max(left, right - frame_width)]
        else:
            if inner_edge == "left":
                x_positions = [x for x in x_positions if x - left <= allowed_inner_trim]
                if not x_positions:
                    x_positions = [left]
            else:
                x_positions = [x for x in x_positions if right - (x + frame_width) <= allowed_inner_trim]
                if not x_positions:
                    x_positions = [max(left, right - frame_width)]
    else:
        inner_edge = None
    y_positions = _candidate_positions(top, bottom, frame_height)
    inner_preserve_weight = max(
        0.0,
        (inner_weight - KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN)
        / (KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX - KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN),
    )
    inner_preserve_weight *= inner_preserve_weight

    integral, score_width, score_height = _build_information_integral(image)
    source_score = _scaled_region_sum(integral, score_width, score_height, image.size, crop_box)
    best_score: tuple[int, int, int] | None = None
    best_box = crop_box

    center_x = (left + right - frame_width) / 2
    center_y = (top + bottom - frame_height) / 2
    locked_inner_lost_score: int | None = None
    max_inner_penalty = max(1, right - frame_width - left)
    if inner_edge == "left":
        locked_box = (left, top, left + frame_width, top + frame_height)
        locked_inner_lost_score = source_score - _scaled_region_sum(
            integral,
            score_width,
            score_height,
            image.size,
            locked_box,
        )
    elif inner_edge == "right":
        locked_x = max(left, right - frame_width)
        locked_box = (locked_x, top, locked_x + frame_width, top + frame_height)
        locked_inner_lost_score = source_score - _scaled_region_sum(
            integral,
            score_width,
            score_height,
            image.size,
            locked_box,
        )

    for y in y_positions:
        for x in x_positions:
            candidate = (x, y, x + frame_width, y + frame_height)
            kept_score = _scaled_region_sum(integral, score_width, score_height, image.size, candidate)
            lost_score = source_score - kept_score
            center_penalty = int(abs(x - center_x) + abs(y - center_y))
            inner_penalty = 0
            if inner_edge == "left":
                inner_penalty = max(0, x - left)
            elif inner_edge == "right":
                inner_penalty = max(0, right - (x + frame_width))
            if inner_penalty > 0 and locked_inner_lost_score is not None:
                movement_ratio = min(1.0, inner_penalty / max_inner_penalty)
                improvement_ratio = (
                    KCC_INNER_TRADEOFF_BASE_IMPROVEMENT
                    + (KCC_INNER_TRADEOFF_FULL_IMPROVEMENT - KCC_INNER_TRADEOFF_BASE_IMPROVEMENT)
                    * movement_ratio
                )
                required_gain = int(locked_inner_lost_score * improvement_ratio)
                if lost_score > locked_inner_lost_score - required_gain:
                    continue
            score = (
                lost_score + int(inner_penalty * inner_preserve_weight * 10000),
                inner_penalty,
                center_penalty,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_box = candidate

    if not _crop_box_has_area_inside(best_box, image.size):
        return crop_box
    return best_box


def optimize_facing_spread_crop_box(
    image,
    crop_box: tuple[int, int, int, int],
    target_size: tuple[int, int] | None,
    page_position: str,
    template_direction: str | None,
    inner_enabled: bool,
) -> tuple[int, int, int, int]:
    frame_size = _target_ratio_frame_size(crop_box, target_size)
    if frame_size is None:
        return crop_box

    frame_width, frame_height = frame_size
    left, top, right, bottom = crop_box
    if frame_width >= right - left and frame_height >= bottom - top:
        return crop_box

    outer_edge, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
    horizontal_required = max(0, right - left - frame_width)
    outer_budget = measure_low_information_edge_budget(
        image,
        crop_box,
        page_position,
        template_direction,
        "outer",
        horizontal_required,
    )
    inner_budget = 0
    if inner_enabled:
        inner_budget = measure_low_information_edge_budget(
            image,
            crop_box,
            page_position,
            template_direction,
            "inner",
            horizontal_required,
        )
    top_budget = measure_vertical_low_information_margin(
        image,
        crop_box,
        "top",
        KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
    )
    bottom_budget = measure_vertical_low_information_margin(
        image,
        crop_box,
        "bottom",
        KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO,
    )

    x_positions = _candidate_positions(left, right, frame_width)
    if not inner_enabled:
        x_positions = [left if inner_edge == "left" else max(left, right - frame_width)]
    else:
        inner_limit_x = (
            left + inner_budget
            if inner_edge == "left"
            else right - frame_width - inner_budget
        )
        x_positions = sorted(set(x_positions + [max(left, min(right - frame_width, inner_limit_x))]))
    y_positions = _candidate_positions(top, bottom, frame_height)

    integral, score_width, score_height = _build_information_integral(image)
    source_score = _scaled_region_sum(integral, score_width, score_height, image.size, crop_box)
    center_x = (left + right - frame_width) / 2
    center_y = (top + bottom - frame_height) / 2
    best_score: tuple[int, int, int, int] | None = None
    best_box = crop_box

    for y in y_positions:
        for x in x_positions:
            candidate = (x, y, x + frame_width, y + frame_height)
            left_trim = x - left
            right_trim = right - (x + frame_width)
            top_trim = y - top
            bottom_trim = bottom - (y + frame_height)
            outer_trim = left_trim if outer_edge == "left" else right_trim
            inner_trim = left_trim if inner_edge == "left" else right_trim
            if inner_enabled and inner_trim > inner_budget:
                continue
            horizontal_over_budget = max(0, outer_trim - outer_budget)
            if inner_enabled:
                horizontal_over_budget += max(0, inner_trim - inner_budget)
            else:
                horizontal_over_budget += inner_trim
            vertical_over_budget = max(0, top_trim - top_budget) + max(0, bottom_trim - bottom_budget)
            kept_score = _scaled_region_sum(integral, score_width, score_height, image.size, candidate)
            lost_score = source_score - kept_score
            center_penalty = int(abs(x - center_x) + abs(y - center_y))
            score = (
                horizontal_over_budget + vertical_over_budget,
                lost_score,
                inner_trim if inner_enabled else 0,
                center_penalty,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_box = candidate

    if not _crop_box_has_area_inside(best_box, image.size):
        return crop_box
    return best_box


def trim_smart_crop_box_to_target_aspect(
    image,
    crop_box: tuple[int, int, int, int],
    target_size: tuple[int, int] | None,
    edge_threshold: float = KCC_CROP_STRENGTH_DEFAULT,
) -> tuple[int, int, int, int]:
    crop_box = _interpolate_crop_box_by_retention(image.size, crop_box, edge_threshold)
    return optimize_ratio_frame_crop_box(
        image,
        crop_box,
        target_size,
    )


def trim_facing_crop_box_to_target_aspect(
    image,
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    target_size: tuple[int, int] | None,
    outer_threshold: float = KCC_CROP_STRENGTH_DEFAULT,
    inner_enabled: bool = False,
    inner_threshold: float = KCC_CROP_STRENGTH_DEFAULT,
) -> tuple[int, int, int, int]:
    return optimize_facing_spread_crop_box(
        image,
        crop_box,
        target_size,
        page_position=page_position,
        template_direction=template_direction,
        inner_enabled=inner_enabled,
    )


def _full_image_crop_box(image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    return (0, 0, image_size[0], image_size[1])


def _crop_box_size(crop_box: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = crop_box
    return max(0, right - left), max(0, bottom - top)


def _split_growth(growth: int, first_capacity: int, second_capacity: int) -> tuple[int, int]:
    if growth <= 0:
        return 0, 0

    first = min(first_capacity, growth // 2)
    second = min(second_capacity, growth - first)
    remaining = growth - first - second
    if remaining > 0:
        extra_first = min(first_capacity - first, remaining)
        first += extra_first
        remaining -= extra_first
    if remaining > 0:
        second += min(second_capacity - second, remaining)
    return first, second


def _expand_crop_box_to_size(
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    target_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    image_width, image_height = image_size
    target_width = min(max(1, target_size[0]), image_width)
    target_height = min(max(1, target_size[1]), image_height)
    left, top, right, bottom = crop_box
    current_width, current_height = _crop_box_size(crop_box)

    grow_width = max(0, target_width - current_width)
    if grow_width > 0:
        outer_edge, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
        left_capacity = left
        right_capacity = image_width - right
        if outer_edge == "left":
            grow_left = min(left_capacity, grow_width)
            grow_right = min(right_capacity, grow_width - grow_left)
        else:
            grow_right = min(right_capacity, grow_width)
            grow_left = min(left_capacity, grow_width - grow_right)
        remaining = grow_width - grow_left - grow_right
        if remaining > 0 and inner_edge == "left":
            extra_left = min(left_capacity - grow_left, remaining)
            grow_left += extra_left
            remaining -= extra_left
        if remaining > 0 and inner_edge == "right":
            grow_right += min(right_capacity - grow_right, remaining)
        left -= grow_left
        right += grow_right

    grow_height = max(0, target_height - (bottom - top))
    if grow_height > 0:
        grow_top, grow_bottom = _split_growth(grow_height, top, image_height - bottom)
        top -= grow_top
        bottom += grow_bottom

    adjusted = (max(0, left), max(0, top), min(image_width, right), min(image_height, bottom))
    if not crop_box_is_safe(adjusted, image_size):
        return crop_box
    return adjusted


def align_facing_crop_boxes_to_shared_size(
    left_size: tuple[int, int],
    left_crop_box: tuple[int, int, int, int] | None,
    right_size: tuple[int, int],
    right_crop_box: tuple[int, int, int, int] | None,
    template_direction: str | None,
    left_page_position: str = "first",
    right_page_position: str = "second",
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    if left_crop_box is None or right_crop_box is None:
        return left_crop_box, right_crop_box

    left_width, left_height = _crop_box_size(left_crop_box)
    right_width, right_height = _crop_box_size(right_crop_box)
    target_width = max(left_width, right_width)
    target_height = max(left_height, right_height)

    if target_width > min(left_size[0], right_size[0]) or target_height > min(left_size[1], right_size[1]):
        return left_crop_box, right_crop_box

    shared_size = (target_width, target_height)
    aligned_left = _expand_crop_box_to_size(left_size, left_crop_box, left_page_position, template_direction, shared_size)
    aligned_right = _expand_crop_box_to_size(right_size, right_crop_box, right_page_position, template_direction, shared_size)
    if _crop_box_size(aligned_left) != _crop_box_size(aligned_right):
        return left_crop_box, right_crop_box
    return aligned_left, aligned_right


def _build_facing_fill_fallback_box(
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int] | None,
    page_position: str,
    template_direction: str | None,
) -> tuple[int, int, int, int]:
    if crop_box is None:
        return _full_image_crop_box(image_size)

    outer_only = build_outer_only_crop_box(
        image_size,
        crop_box,
        page_position=page_position,
        template_direction=template_direction,
    )
    if outer_only is not None:
        return outer_only
    if crop_box_is_safe(crop_box, image_size):
        return crop_box
    return _full_image_crop_box(image_size)


def build_facing_fill_crop_boxes(
    left_size: tuple[int, int],
    left_crop_box: tuple[int, int, int, int] | None,
    right_size: tuple[int, int],
    right_crop_box: tuple[int, int, int, int] | None,
    template_direction: str | None,
    left_page_position: str = "first",
    right_page_position: str = "second",
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    if left_crop_box is not None and right_crop_box is not None:
        synchronized_left, synchronized_right = synchronize_facing_crop_boxes(
            left_size,
            left_crop_box,
            right_size,
            right_crop_box,
            template_direction,
            left_page_position=left_page_position,
            right_page_position=right_page_position,
        )
        if synchronized_left is not None and synchronized_right is not None:
            return synchronized_left, synchronized_right

    return (
        _build_facing_fill_fallback_box(left_size, left_crop_box, left_page_position, template_direction),
        _build_facing_fill_fallback_box(right_size, right_crop_box, right_page_position, template_direction),
    )


def maybe_add_facing_fill_trim(
    left_image,
    left_crop_box: tuple[int, int, int, int] | None,
    right_image,
    right_crop_box: tuple[int, int, int, int] | None,
    template_direction: str | None,
    target_size: tuple[int, int] | None,
    edge_threshold: float = KCC_CROP_STRENGTH_DEFAULT,
    inner_enabled: bool = False,
    inner_edge_threshold: float = KCC_CROP_STRENGTH_DEFAULT,
    left_page_position: str = "first",
    right_page_position: str = "second",
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    if left_crop_box is None or right_crop_box is None:
        return left_crop_box, right_crop_box

    current_left = trim_facing_crop_box_to_target_aspect(
        left_image,
        left_crop_box,
        left_page_position,
        template_direction,
        target_size,
        outer_threshold=edge_threshold,
        inner_enabled=inner_enabled,
        inner_threshold=inner_edge_threshold,
    )
    current_right = trim_facing_crop_box_to_target_aspect(
        right_image,
        right_crop_box,
        right_page_position,
        template_direction,
        target_size,
        outer_threshold=edge_threshold,
        inner_enabled=inner_enabled,
        inner_threshold=inner_edge_threshold,
    )

    current_left, current_right = align_facing_crop_boxes_to_shared_size(
        left_image.size,
        current_left,
        right_image.size,
        current_right,
        template_direction,
        left_page_position=left_page_position,
        right_page_position=right_page_position,
    )

    return current_left, current_right


def is_spread_crop_mode(crop_mode: str) -> bool:
    return normalize_crop_mode(crop_mode) == "spread-fill"


def synchronize_facing_crop_boxes(
    left_size: tuple[int, int],
    left_crop_box: tuple[int, int, int, int] | None,
    right_size: tuple[int, int],
    right_crop_box: tuple[int, int, int, int] | None,
    template_direction: str | None,
    left_page_position: str = "first",
    right_page_position: str = "second",
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    if left_crop_box is None or right_crop_box is None:
        return None, None

    left_margins = CropMargins.from_box(left_crop_box, left_size)
    right_margins = CropMargins.from_box(right_crop_box, right_size)

    left_width, left_height = left_size
    right_width, right_height = right_size

    left_outer, _ = get_outer_inner_horizontal_margins(left_margins, left_page_position, template_direction)
    right_outer, _ = get_outer_inner_horizontal_margins(right_margins, right_page_position, template_direction)

    shared_top_ratio = min(left_margins.top / left_height, right_margins.top / right_height)
    shared_bottom_ratio = min(left_margins.bottom / left_height, right_margins.bottom / right_height)
    shared_outer_ratio = min(left_outer / left_width, right_outer / right_width)

    synchronized_left = build_facing_crop_box(
        left_size,
        left_page_position,
        template_direction,
        outer_ratio=shared_outer_ratio,
        top_ratio=shared_top_ratio,
        bottom_ratio=shared_bottom_ratio,
        inner_ratio=0.0,
    )
    synchronized_right = build_facing_crop_box(
        right_size,
        right_page_position,
        template_direction,
        outer_ratio=shared_outer_ratio,
        top_ratio=shared_top_ratio,
        bottom_ratio=shared_bottom_ratio,
        inner_ratio=0.0,
    )

    if not crop_box_is_safe(synchronized_left, left_size):
        return None, None
    if not crop_box_is_safe(synchronized_right, right_size):
        return None, None

    return align_facing_crop_boxes_to_shared_size(
        left_size,
        synchronized_left,
        right_size,
        synchronized_right,
        template_direction,
        left_page_position=left_page_position,
        right_page_position=right_page_position,
    )


def build_smart_crop_box(
    image,
    target_size: tuple[int, int] | None = None,
    edge_threshold: float = KCC_CROP_STRENGTH_DEFAULT,
) -> tuple[int, int, int, int] | None:
    _, _, ImageStat = load_pillow()
    gray = image.convert("L")
    background = detect_border_background(gray)
    if background is None:
        return None

    crop_box = trim_border(gray, background)
    if crop_box == (0, 0, image.size[0], image.size[1]):
        return None
    if not crop_box_is_safe(crop_box, image.size):
        return None

    cropped = image.crop(crop_box)
    if cropped.size[0] < image.size[0] * 0.6 or cropped.size[1] < image.size[1] * 0.6:
        return None

    edge_patch = max(8, min(cropped.size) // 100)
    edge_boxes = [
        (0, 0, edge_patch, edge_patch),
        (cropped.size[0] - edge_patch, 0, cropped.size[0], edge_patch),
        (0, cropped.size[1] - edge_patch, edge_patch, cropped.size[1]),
        (cropped.size[0] - edge_patch, cropped.size[1] - edge_patch, cropped.size[0], cropped.size[1]),
    ]
    cropped_gray = cropped.convert("L")
    edge_means = [ImageStat.Stat(cropped_gray.crop(box)).mean[0] for box in edge_boxes]
    if background == "white" and (min(edge_means) >= 215 or sum(value >= 225 for value in edge_means) >= 3):
        adjusted = trim_smart_crop_box_to_target_aspect(
            image,
            crop_box,
            target_size,
            edge_threshold=edge_threshold,
        )
        if adjusted == (0, 0, image.size[0], image.size[1]):
            return None
        return adjusted
    if background == "black" and (max(edge_means) <= 40 or sum(value <= 30 for value in edge_means) >= 3):
        adjusted = trim_smart_crop_box_to_target_aspect(
            image,
            crop_box,
            target_size,
            edge_threshold=edge_threshold,
        )
        if adjusted == (0, 0, image.size[0], image.size[1]):
            return None
        return adjusted
    return None


def smart_crop_image(
    image,
    target_size: tuple[int, int] | None = None,
    edge_threshold: float = KCC_CROP_STRENGTH_DEFAULT,
):
    crop_box = build_smart_crop_box(image, target_size=target_size, edge_threshold=edge_threshold)
    if crop_box is None:
        return image
    cropped = apply_crop_box(image, crop_box)
    if cropped is image:
        return image
    if crop_box_is_safe(crop_box, image.size):
        return cropped
    if cropped is not image:
        cropped.close()
    return image


def load_source_image(input_path: Path):
    Image, ImageOps, _ = load_pillow()

    with Image.open(input_path) as opened_image:
        image = ImageOps.exif_transpose(opened_image)
        if image.mode in {"RGBA", "LA"} or image.info.get("transparency") is not None:
            flattened = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(flattened, image.convert("RGBA"))
        return image.convert("RGB")


def apply_luminance_operations(image, options: ImageProcessingOptions):
    Image, _, _ = load_pillow()

    if options.preserve_color:
        working = image.convert("YCbCr")
        luminance, chroma_blue, chroma_red = working.split()
    else:
        luminance = image.convert("L")
        chroma_blue = chroma_red = None

    if options.autolevel:
        luminance = autolevel_band(luminance)
    if options.gamma != 1.0:
        luminance = gamma_correct_band(luminance, options.gamma)
    if options.autocontrast and not low_contrast_band(luminance):
        luminance = safe_autocontrast(luminance)
    if options.contrast != 1.0:
        luminance = contrast_correct_band(luminance, options.contrast)

    if options.preserve_color:
        return Image.merge("YCbCr", (luminance, chroma_blue, chroma_red)).convert("RGB")
    return luminance


def fit_image_to_canvas(
    image,
    target_size: tuple[int, int],
    preserve_color: bool,
    horizontal_anchor: HorizontalAnchor = "center",
):
    Image, ImageOps, _ = load_pillow()
    resized = ImageOps.contain(image, target_size, Image.Resampling.LANCZOS)
    canvas_mode = "RGB" if preserve_color else "L"
    background = (255, 255, 255) if preserve_color else 255
    canvas = Image.new(canvas_mode, target_size, background)
    remaining_width = target_size[0] - resized.size[0]
    if horizontal_anchor == "left":
        offset_x = 0
    elif horizontal_anchor == "right":
        offset_x = remaining_width
    else:
        offset_x = remaining_width // 2
    offset_y = (target_size[1] - resized.size[1]) // 2
    canvas.paste(resized, (offset_x, offset_y))
    return canvas


def save_processed_image(
    image,
    output_path: Path,
    options: ImageProcessingOptions,
    horizontal_anchor: HorizontalAnchor = "center",
) -> None:
    if normalize_crop_mode(options.crop_mode) == "smart":
        image = smart_crop_image(
            image,
            target_size=options.target_size,
            edge_threshold=options.crop_edge_threshold,
        )

    image = apply_luminance_operations(image, options)

    if options.target_size is not None:
        image = fit_image_to_canvas(image, options.target_size, options.preserve_color, horizontal_anchor)

    if not options.preserve_color:
        image = image.convert("L")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        output_path,
        format="JPEG",
        quality=options.jpeg_quality,
        optimize=True,
        subsampling=0,
    )


def process_single_image(input_path: Path, output_path: Path, options: ImageProcessingOptions) -> None:
    image = load_source_image(input_path)
    try:
        save_processed_image(image, output_path, options)
    finally:
        image.close()


def process_kcc_spread_group(
    input_paths: list[Path],
    output_paths: list[Path],
    options: ImageProcessingOptions,
    template_direction: str | None,
    allow_inner_white_fill: bool = False,
    page_positions: tuple[str, ...] | None = None,
) -> None:
    if len(input_paths) != len(output_paths):
        raise ValueError("输入页数与输出页数不匹配。")
    if not input_paths:
        return
    page_positions = page_positions or tuple("first" if index == 0 else "second" for index in range(len(input_paths)))
    if len(input_paths) == 1:
        process_kcc_facing_single_page(
            input_paths[0],
            output_paths[0],
            options,
            template_direction=template_direction,
            page_position=page_positions[0],
        )
        return

    loaded_images = [load_source_image(path) for path in input_paths]
    try:
        crop_boxes = [build_kcc_crop_box(image) for image in loaded_images]
        if allow_inner_white_fill:
            synchronized_boxes = list(
                build_facing_fill_crop_boxes(
                    loaded_images[0].size,
                    crop_boxes[0],
                    loaded_images[1].size,
                    crop_boxes[1],
                    template_direction,
                    left_page_position=page_positions[0],
                    right_page_position=page_positions[1],
                )
            )
            synchronized_boxes = list(
                maybe_add_facing_fill_trim(
                    loaded_images[0],
                    synchronized_boxes[0],
                    loaded_images[1],
                    synchronized_boxes[1],
                    template_direction,
                    options.target_size,
                    edge_threshold=options.spread_fill_edge_threshold,
                    inner_enabled=options.spread_fill_inner_enabled,
                    inner_edge_threshold=options.spread_fill_inner_edge_threshold,
                    left_page_position=page_positions[0],
                    right_page_position=page_positions[1],
                )
            )
        else:
            synchronized_boxes = list(
                synchronize_facing_crop_boxes(
                    loaded_images[0].size,
                    crop_boxes[0],
                    loaded_images[1].size,
                    crop_boxes[1],
                    template_direction,
                    left_page_position=page_positions[0],
                    right_page_position=page_positions[1],
                )
            )

        for image, crop_box, output_path, page_position in zip(
            loaded_images,
            synchronized_boxes,
            output_paths,
            page_positions,
        ):
            processed = apply_crop_box(image, crop_box)
            try:
                save_processed_image(
                    processed,
                    output_path,
                    replace(options, crop_mode="off"),
                    horizontal_anchor=horizontal_anchor_for_outer_edge(page_position, template_direction),
                )
            finally:
                if processed is not image:
                    processed.close()
    finally:
        for image in loaded_images:
            image.close()


def process_kcc_facing_single_page(
    input_path: Path,
    output_path: Path,
    options: ImageProcessingOptions,
    template_direction: str | None,
    page_position: str,
) -> None:
    image = load_source_image(input_path)
    try:
        crop_box = build_kcc_crop_box(image)
        outer_only_crop_box = build_outer_only_crop_box(
            image.size,
            crop_box,
            page_position=page_position,
            template_direction=template_direction,
        )
        if normalize_crop_mode(options.crop_mode) == "spread-fill" and outer_only_crop_box is not None:
            outer_only_crop_box = trim_facing_crop_box_to_target_aspect(
                image,
                outer_only_crop_box,
                page_position,
                template_direction,
                options.target_size,
                outer_threshold=options.spread_fill_edge_threshold,
                inner_enabled=options.spread_fill_inner_enabled,
                inner_threshold=options.spread_fill_inner_edge_threshold,
            )
        processed = apply_crop_box(image, outer_only_crop_box)
        try:
            save_processed_image(
                processed,
                output_path,
                replace(options, crop_mode="off"),
                horizontal_anchor=horizontal_anchor_for_outer_edge(page_position, template_direction),
            )
        finally:
            if processed is not image:
                processed.close()
    finally:
        image.close()


@dataclass(frozen=True)
class PreprocessGroup:
    slots: tuple[LayoutPageSlot, ...]
    input_paths: tuple[Path, ...]
    output_paths: tuple[Path, ...]


def build_preprocess_groups(
    image_paths: list[Path],
    processed_root: Path,
    options: ImageProcessingOptions,
    shift_first_page: bool,
) -> list[PreprocessGroup]:
    groups: list[PreprocessGroup] = []
    output_index = 1

    if is_spread_crop_mode(options.crop_mode):
        page_groups = build_layout_page_groups(
            len(image_paths),
            shift_blank_count=1 if shift_first_page and image_paths else 0,
            page_layout="facing",
        )
        for page_group in page_groups:
            source_slots = tuple(slot for slot in page_group if slot.source_index is not None)
            if not source_slots:
                continue
            output_paths = tuple(
                processed_root / f"{output_index + offset:05d}.jpg"
                for offset in range(len(source_slots))
            )
            groups.append(
                PreprocessGroup(
                    slots=source_slots,
                    input_paths=tuple(image_paths[slot.source_index] for slot in source_slots if slot.source_index is not None),
                    output_paths=output_paths,
                )
            )
            output_index += len(source_slots)
        return groups

    for index, image_path in enumerate(image_paths, start=1):
        slot = LayoutPageSlot(
            page_number=index,
            page_position="first",
            source_index=index - 1,
            is_shift_blank=False,
        )
        groups.append(
            PreprocessGroup(
                slots=(slot,),
                input_paths=(image_path,),
                output_paths=(processed_root / f"{index:05d}.jpg",),
            )
        )
    return groups


def process_preprocess_group(
    group: PreprocessGroup,
    options: ImageProcessingOptions,
    template_direction: str | None,
) -> None:
    if is_spread_crop_mode(options.crop_mode):
        page_positions = tuple(slot.page_position for slot in group.slots)
        if len(group.input_paths) == 1:
            process_kcc_facing_single_page(
                group.input_paths[0],
                group.output_paths[0],
                options,
                template_direction=template_direction,
                page_position=page_positions[0],
            )
            return
        process_kcc_spread_group(
            list(group.input_paths),
            list(group.output_paths),
            options,
            template_direction=template_direction,
            allow_inner_white_fill=normalize_crop_mode(options.crop_mode) == "spread-fill",
            page_positions=page_positions,
        )
        return

    process_single_image(group.input_paths[0], group.output_paths[0], options)


def preprocess_images(
    image_paths: list[Path],
    options: ImageProcessingOptions,
    shift_first_page: bool = False,
    template_direction: str | None = None,
    progress_callback: BuildProgressCallback | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> tuple[list[Path], Path | None]:
    raise_if_build_cancelled(stop_requested)
    if not options.enabled:
        return image_paths, None

    processed_root = Path(".analysis") / "tmp" / f"processed_{uuid.uuid4().hex}"
    processed_root.mkdir(parents=True, exist_ok=True)
    try:
        groups = build_preprocess_groups(image_paths, processed_root, options, shift_first_page)
        worker_count = max(1, min(int(options.preprocessing_workers), len(groups)))
        total_pages = sum(len(group.output_paths) for group in groups)
        completed_pages = 0
        if progress_callback is not None:
            progress_callback(
                BuildStageProgress(
                    "ui.progress.preprocess.images",
                    completed_pages,
                    total_pages,
                    workers=worker_count,
                )
        )
        if worker_count == 1:
            for group in groups:
                raise_if_build_cancelled(stop_requested)
                process_preprocess_group(group, options, template_direction)
                completed_pages += len(group.output_paths)
                if progress_callback is not None:
                    progress_callback(
                        BuildStageProgress(
                            "ui.progress.preprocess.images",
                            completed_pages,
                            total_pages,
                            current_name=group.input_paths[-1].name,
                            workers=worker_count,
                        )
                    )
                raise_if_build_cancelled(stop_requested)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(process_preprocess_group, group, options, template_direction): group
                    for group in groups
                }
                try:
                    for future in concurrent.futures.as_completed(futures):
                        raise_if_build_cancelled(stop_requested)
                        future.result()
                        group = futures[future]
                        completed_pages += len(group.output_paths)
                        if progress_callback is not None:
                            progress_callback(
                                BuildStageProgress(
                                    "ui.progress.preprocess.images",
                                    completed_pages,
                                    total_pages,
                                    current_name=group.input_paths[-1].name,
                                    workers=worker_count,
                                )
                            )
                        raise_if_build_cancelled(stop_requested)
                except BuildCancelled:
                    for pending in futures:
                        pending.cancel()
                    raise
    except Exception:
        shutil.rmtree(processed_root, ignore_errors=True)
        raise

    processed_paths = [output_path for group in groups for output_path in group.output_paths]
    return processed_paths, processed_root


def resolve_image_processing_options(args: argparse.Namespace) -> ImageProcessingOptions:
    presets = {
        "none": {
            "gamma": 1.0,
            "contrast": 1.0,
            "autocontrast": False,
            "autolevel": False,
            "jpeg_quality": 90,
            "preserve_color": True,
        },
        "standard": {
            "gamma": 1.0,
            "contrast": 1.0,
            "autocontrast": True,
            "autolevel": False,
            "jpeg_quality": 90,
            "preserve_color": True,
        },
        "bright": {
            "gamma": 1.8,
            "contrast": 1.0,
            "autocontrast": True,
            "autolevel": False,
            "jpeg_quality": 90,
            "preserve_color": True,
        },
    }

    image_preset = normalize_image_preset(args.image_preset)
    crop_mode = normalize_crop_mode(args.crop_mode)
    preset = presets[image_preset]
    target_size = args.target_size
    if args.scribe_panel:
        target_size = target_size or (1240, 1860)

    preserve_color = preset["preserve_color"] if args.preserve_color is None else args.preserve_color
    gamma = preset["gamma"] if args.gamma is None else args.gamma
    contrast = preset["contrast"] if getattr(args, "contrast", None) is None else args.contrast
    autocontrast = preset["autocontrast"] if args.autocontrast is None else args.autocontrast
    autolevel = preset["autolevel"] if args.autolevel is None else args.autolevel
    jpeg_quality = preset["jpeg_quality"] if args.jpeg_quality is None else args.jpeg_quality

    return ImageProcessingOptions(
        target_size=target_size,
        crop_mode=crop_mode,
        crop_edge_threshold=max(
            KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN,
            min(
                KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX,
                float(getattr(args, "crop_edge_threshold", KCC_CROP_STRENGTH_DEFAULT)),
            ),
        ),
        spread_fill_edge_threshold=max(
            KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN,
            min(
                KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX,
                float(getattr(args, "spread_fill_edge_threshold", KCC_CROP_STRENGTH_DEFAULT)),
            ),
        ),
        spread_fill_inner_enabled=bool(getattr(args, "spread_fill_inner_enabled", False)),
        spread_fill_inner_edge_threshold=max(
            KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MIN,
            min(
                KCC_FILL_OUTER_LOW_INFO_DOMINANT_RATIO_MAX,
                float(getattr(args, "spread_fill_inner_edge_threshold", KCC_CROP_STRENGTH_DEFAULT)),
            ),
        ),
        preserve_color=preserve_color,
        gamma=gamma,
        contrast=contrast,
        autocontrast=autocontrast,
        autolevel=autolevel,
        jpeg_quality=jpeg_quality,
        preprocessing_workers=resolve_preprocessing_workers(getattr(args, "performance_mode", "balanced")),
    )


def encode_varuint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varuint 不支持负数。")
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.append(value & 0x7F)
        value >>= 7
    parts.reverse()
    parts[-1] |= 0x80
    return bytes(parts)


def int_bytes(value: int) -> bytes:
    if value == 0:
        return b""
    length = (value.bit_length() + 7) // 8
    return value.to_bytes(length, "big")


def encode_typed(type_code: int, payload: bytes) -> bytes:
    length = len(payload)
    if length < 14:
        return bytes([(type_code << 4) | length]) + payload
    return bytes([(type_code << 4) | 0x0E]) + encode_varuint(length) + payload


def ion_int(value: int) -> bytes:
    if value < 0:
        return encode_typed(3, int_bytes(-value))
    return encode_typed(2, int_bytes(value))


def ion_float(value: float) -> bytes:
    return encode_typed(4, struct.pack(">d", value))


def ion_symbol(sid: int) -> bytes:
    return encode_typed(7, int_bytes(sid))


def ion_string(value: str) -> bytes:
    return encode_typed(8, value.encode("utf-8"))


def ion_list(items: list[bytes]) -> bytes:
    return encode_typed(11, b"".join(items))


def ion_struct(fields: list[tuple[int, bytes]]) -> bytes:
    payload = b"".join(encode_varuint(field_sid) + field_value for field_sid, field_value in fields)
    return encode_typed(13, payload)


def ion_annotation(annotations: list[int], wrapped: bytes) -> bytes:
    annotations_payload = b"".join(encode_varuint(annotation) for annotation in annotations)
    payload = encode_varuint(len(annotations_payload)) + annotations_payload + wrapped
    return encode_typed(14, payload)


def ion_name_ref(value: str) -> bytes:
    return ion_annotation([NAME_REF_ANNOTATION_SID], ion_string(value))


def ion_stream(value: bytes) -> bytes:
    return ION_SIGNATURE + value


def build_book_metadata_blob(title: str, cover_external_id: str, layout_options: LayoutOptions) -> bytes:
    capability_rows: list[tuple[str, bytes]] = []
    if layout_options.page_layout == "facing":
        capability_rows.append(("yj_double_page_spread", ion_int(1)))
    capability_rows.append(("yj_fixed_layout", ion_int(1)))
    capability_rows.append(("yj_publisher_panels", ion_int(0 if layout_options.page_layout == "facing" else 1)))

    categories = [
        (
            "kindle_audit_metadata",
            [
                ("file_creator", ion_string("KC")),
                ("creator_version", ion_string("1.110.0.0")),
            ],
        ),
        (
            "kindle_title_metadata",
            [
                ("book_id", ion_string(uuid.uuid4().hex[:22] + "0")),
                ("cover_image", ion_string(cover_external_id)),
                ("language", ion_string("en-US")),
                ("title", ion_string(title)),
            ],
        ),
        (
            "kindle_capability_metadata",
            capability_rows,
        ),
        (
            "kindle_ebook_metadata",
            [
                ("selection", ion_string("enabled")),
            ],
        ),
    ]

    payload = ion_struct(
        [
            (
                491,
                ion_list(
                    [
                        ion_struct(
                            [
                                (495, ion_string(category_name)),
                                (
                                    258,
                                    ion_list(
                                        [
                                            ion_struct(
                                                [
                                                    (492, ion_string(row_key)),
                                                    (307, row_value),
                                                ]
                                            )
                                            for row_key, row_value in rows
                                        ]
                                    ),
                                ),
                            ]
                        )
                        for category_name, rows in categories
                    ]
                ),
            )
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_BOOK_METADATA], payload))


def build_metadata_blob(section_ids: list[str]) -> bytes:
    reading_order = ion_struct(
        [
            (178, ion_symbol(SYMBOL_DEFAULT_READING_ORDER)),
            (170, ion_list([ion_name_ref(section_id) for section_id in section_ids])),
        ]
    )
    payload = ion_struct([(169, ion_list([reading_order]))])
    return ion_stream(ion_annotation([SYMBOL_METADATA], payload))


def build_document_data_blob(section_ids: list[str], global_aux_id: str) -> bytes:
    reading_order = ion_struct(
        [
            (178, ion_symbol(SYMBOL_DEFAULT_READING_ORDER)),
            (170, ion_list([ion_name_ref(section_id) for section_id in section_ids])),
        ]
    )
    payload = ion_struct(
        [
            (16, ion_float(16.0)),
            (560, ion_symbol(559)),
            (8, ion_int(3160)),
            (192, ion_symbol(376)),
            (581, ion_symbol(441)),
            (
                597,
                ion_list(
                    [
                        ion_struct(
                            [
                                (613, ion_name_ref(global_aux_id)),
                            ]
                        )
                    ]
                ),
            ),
            (169, ion_list([reading_order])),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_DOCUMENT_DATA], payload))


def build_global_aux_blob(global_aux_id: str, page_aux_ids: list[str]) -> bytes:
    payload = ion_struct(
        [
            (598, ion_name_ref(global_aux_id)),
            (
                258,
                ion_list(
                    [
                        ion_struct(
                            [
                                (492, ion_string("auxData_resource_list")),
                                (
                                    307,
                                    ion_list([ion_name_ref(aux_id) for aux_id in page_aux_ids]),
                                ),
                            ]
                        )
                    ]
                ),
            ),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_AUXILIARY_DATA], payload))


def build_section_pid_count_blob(section_pid_counts: list[tuple[str, int]]) -> bytes:
    entries = [
        ion_struct(
            [
                (174, ion_name_ref(section_id)),
                (144, ion_int(pid_count)),
            ]
        )
        for section_id, pid_count in section_pid_counts
    ]
    payload = ion_struct([(181, ion_list(entries))])
    return ion_stream(ion_annotation([SYMBOL_SECTION_PID_COUNT_MAP], payload))


def build_section_blob(
    section_id: str,
    anchor_id: str,
    storyline_id: str,
    width: int,
    height: int,
    role_symbol: int,
) -> bytes:
    inner = ion_annotation(
        [SYMBOL_STRUCTURE],
        ion_struct(
            [
                (598, ion_name_ref(anchor_id)),
                (176, ion_name_ref(storyline_id)),
                (66, ion_int(width)),
                (434, ion_symbol(SYMBOL_LAYOUT_SECTION_KIND)),
                (67, ion_int(height)),
                (156, ion_symbol(role_symbol)),
                (140, ion_symbol(SYMBOL_LAYOUT_HEAD_ALIGN)),
                (159, ion_symbol(SYMBOL_LAYOUT_SECTION_TYPE)),
            ]
        ),
    )
    payload = ion_struct(
        [
            (174, ion_name_ref(section_id)),
            (141, ion_list([inner])),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_SECTION], payload))


def build_section_position_id_map_blob(section_id: str, ordered_ids: list[str]) -> bytes:
    positions = [
        ion_list(
            [
                ion_int(index),
                ion_name_ref(target_id),
            ]
        )
        for index, target_id in enumerate(ordered_ids, start=1)
    ]
    payload = ion_struct(
        [
            (174, ion_name_ref(section_id)),
            (181, ion_list(positions)),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_SECTION_POSITION_ID_MAP], payload))


def build_storyline_blob(storyline_id: str, head_ids: list[str]) -> bytes:
    payload = ion_struct(
        [
            (176, ion_name_ref(storyline_id)),
            (146, ion_list([ion_name_ref(head_id) for head_id in head_ids])),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_STORYLINE], payload))


def build_head_blob(head_id: str, tail_id: str, width: int, height: int, single_page: bool) -> bytes:
    fields: list[tuple[int, bytes]] = [
        (598, ion_name_ref(head_id)),
        (546, ion_symbol(SYMBOL_LAYOUT_COMMON)),
    ]
    if single_page:
        fields.extend(
            [
                (56, ion_int(width)),
                (57, ion_int(height)),
                (156, ion_symbol(SYMBOL_LAYOUT_HEAD_SINGLE_ROLE)),
                (159, ion_symbol(SYMBOL_LAYOUT_HEAD_TYPE)),
                (146, ion_list([ion_name_ref(tail_id)])),
            ]
        )
    else:
        fields.extend(
            [
                (66, ion_int(width)),
                (67, ion_int(height)),
                (156, ion_symbol(SYMBOL_LAYOUT_HEAD_ROLE)),
                (140, ion_symbol(SYMBOL_LAYOUT_HEAD_ALIGN)),
                (159, ion_symbol(SYMBOL_LAYOUT_HEAD_TYPE)),
                (146, ion_list([ion_name_ref(tail_id)])),
            ]
        )
    return ion_stream(ion_annotation([SYMBOL_STRUCTURE], ion_struct(fields)))


def build_tail_blob(tail_id: str, external_id: str, width: int, height: int) -> bytes:
    payload = ion_struct(
        [
            (598, ion_name_ref(tail_id)),
            (56, ion_int(width)),
            (175, ion_name_ref(external_id)),
            (57, ion_int(height)),
            (546, ion_symbol(SYMBOL_LAYOUT_COMMON)),
            (159, ion_symbol(SYMBOL_LAYOUT_TAIL_TYPE)),
            (183, ion_symbol(SYMBOL_LAYOUT_TAIL_ROLE)),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_STRUCTURE], payload))


def build_external_resource_blob(
    external_id: str,
    resource_id: str,
    aux_id: str,
    source_name: str,
    width: int,
    height: int,
    format_symbol: int,
) -> bytes:
    payload = ion_struct(
        [
            (852, ion_string(source_name)),
            (161, ion_symbol(format_symbol)),
            (165, ion_string(resource_id)),
            (597, ion_name_ref(aux_id)),
            (422, ion_float(float(width))),
            (175, ion_name_ref(external_id)),
            (423, ion_float(float(height))),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_EXTERNAL_RESOURCE], payload))


def build_aux_blob(aux_id: str, resource_id: str, input_path: Path) -> bytes:
    metadata_rows = [
        ("location", ion_string(input_path.resolve().as_posix())),
        ("modified_time", ion_string(str(int(input_path.stat().st_mtime)))),
        ("size", ion_string(str(input_path.stat().st_size))),
        ("type", ion_string("resource")),
        ("resource_stream", ion_string(resource_id)),
    ]
    payload = ion_struct(
        [
            (598, ion_name_ref(aux_id)),
            (
                258,
                ion_list(
                    [
                        ion_struct(
                            [
                                (492, ion_string(key)),
                                (307, value),
                            ]
                        )
                        for key, value in metadata_rows
                    ]
                ),
            ),
        ]
    )
    return ion_stream(ion_annotation([SYMBOL_AUXILIARY_DATA], payload))


def build_manifest() -> bytes:
    return (
        b"AmazonYJManifest\n"
        b"digital_content_manifest::{\n"
        b"  version:1,\n"
        b"  storage_type:\"localSqlLiteDB\",\n"
        b"  digital_content_name:\"book.kdf\"\n"
        b"}\n"
    )


def build_action_log() -> bytes:
    stamp = current_utc_log_timestamp()
    lines = [
        f"[{stamp}][INFO] [Action] EE NewBook",
        f"[{stamp}][INFO] [Action] E SaveBook - Save",
        f"[{stamp}][INFO] [Action] E SaveBook - SaveforExport",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


@dataclass(frozen=True)
class FragmentRow:
    fragment_id: str
    payload_type: str
    payload_value: bytes
    element_type: str


@dataclass(frozen=True)
class TemplateAssets:
    book_state: dict[str, object]
    metadata: dict[str, object]
    tool_data: dict[str, object]
    manifest_bytes: bytes
    action_log_bytes: bytes
    static_fragments: dict[str, FragmentRow]
    capabilities: list[tuple[str, int]]
    template_direction: str | None


@dataclass(frozen=True)
class PagePlan:
    input_path: Path
    image_info: ImageInfo
    book_filename: str
    head_id: str
    tail_id: str
    external_id: str
    aux_id: str
    resource_id: str
    is_shift_blank: bool = False


@dataclass(frozen=True)
class SpreadPlan:
    section_id: str
    anchor_id: str
    storyline_id: str
    pages: list[PagePlan]

    @property
    def pid_count(self) -> int:
        return 1 + len(self.pages) * 2

    @property
    def spm_targets(self) -> list[str]:
        targets = [self.anchor_id]
        for page in self.pages:
            targets.append(page.head_id)
            targets.append(page.tail_id)
        return targets


@dataclass
class BuildResult:
    input_dir: Path
    output_path: Path
    title: str
    template_direction: str | None
    kfx_output_path: Path | None = None


def to_base36(value: int) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if value <= 0:
        raise ValueError("base36 序号必须大于 0。")
    parts: list[str] = []
    current = value
    while current:
        current, remainder = divmod(current, 36)
        parts.append(digits[remainder])
    return "".join(reversed(parts))


class IdAllocator:
    def __init__(self, reserved: set[str] | None = None):
        self.counters: dict[str, int] = {}
        self.used = set(reserved or set())

    def reserve(self, value: str) -> None:
        self.used.add(value)

    def next(self, prefix: str) -> str:
        counter = self.counters.get(prefix, 0)
        while True:
            counter += 1
            candidate = f"{prefix}{to_base36(counter)}"
            if candidate not in self.used:
                self.counters[prefix] = counter
                self.used.add(candidate)
                return candidate


def load_template_assets(template_path: Path) -> TemplateAssets:
    if not template_path.is_file():
        raise FileNotFoundError(f"模板文件不存在：{template_path}")

    with zipfile.ZipFile(template_path, "r") as archive:
        book_kcb = json.loads(archive.read("book.kcb"))
        raw_kdf = archive.read("resources/book.kdf")
        manifest_bytes = archive.read("resources/ManifestFile")
        action_log_bytes = archive.read("action.log")

    unwrapped_kdf = unwrap_sqlite_fingerprint(raw_kdf)
    temp_root = template_path.parent / ".analysis" / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_db_path = temp_root / f"template_{uuid.uuid4().hex}.kdf"
    temp_db_path.write_bytes(unwrapped_kdf)

    try:
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()

        static_fragments: dict[str, FragmentRow] = {}
        for fragment_id in TEMPLATE_STATIC_FRAGMENT_IDS:
            row = cursor.execute(
                "SELECT payload_type, payload_value FROM fragments WHERE id = ?",
                (fragment_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"模板缺少静态 fragment：{fragment_id}")

            element_type_row = cursor.execute(
                "SELECT value FROM fragment_properties WHERE id = ? AND key = 'element_type'",
                (fragment_id,),
            ).fetchone()
            if element_type_row is None:
                raise ValueError(f"模板缺少 fragment_properties.element_type：{fragment_id}")

            static_fragments[fragment_id] = FragmentRow(
                fragment_id=fragment_id,
                payload_type=row[0],
                payload_value=row[1],
                element_type=element_type_row[0],
            )

        capabilities = cursor.execute("SELECT key, version FROM capabilities ORDER BY key, version").fetchall()
    finally:
        conn.close()
        temp_db_path.unlink(missing_ok=True)

    template_direction = {2: "ltr", 3: "rtl"}.get(
        book_kcb.get("book_state", {}).get("book_virtual_panelmovement")
    )

    return TemplateAssets(
        book_state=book_kcb.get("book_state", {}),
        metadata=book_kcb.get("metadata", {}),
        tool_data=book_kcb.get("tool_data", {}),
        manifest_bytes=manifest_bytes if manifest_bytes else build_manifest(),
        action_log_bytes=action_log_bytes if action_log_bytes else build_action_log(),
        static_fragments=static_fragments,
        capabilities=capabilities,
        template_direction=template_direction,
    )


def load_bundled_template_assets(asset_path: Path = DEFAULT_TEMPLATE_ASSET_PATH) -> TemplateAssets:
    if not asset_path.is_file():
        raise FileNotFoundError(f"内置静态资产不存在：{asset_path}")

    payload = json.loads(asset_path.read_text(encoding="utf-8"))
    static_fragments = {
        item["fragment_id"]: FragmentRow(
            fragment_id=item["fragment_id"],
            payload_type=item["payload_type"],
            payload_value=base64.b64decode(item["payload_value_b64"]),
            element_type=item["element_type"],
        )
        for item in payload["static_fragments"]
    }

    return TemplateAssets(
        book_state=payload["book_state"],
        metadata=payload["metadata"],
        tool_data=payload["tool_data"],
        manifest_bytes=build_manifest(),
        action_log_bytes=build_action_log(),
        static_fragments=static_fragments,
        capabilities=[(key, version) for key, version in payload["capabilities"]],
        template_direction=payload.get("template_direction"),
    )


def inspect_image_infos(image_paths: list[Path]) -> list[ImageInfo]:
    return [read_image_info(path) for path in image_paths]


def resolve_title(input_dir: Path, title: str | None) -> str:
    return title if title is not None else input_dir.name


def build_volume_plan(
    image_paths: list[Path],
    image_infos: list[ImageInfo],
    shift_blank_count: int = 0,
    page_layout: PageLayout = "facing",
) -> tuple[list[SpreadPlan], list[PagePlan]]:
    reserved = {
        "$ion_symbol_table",
        "max_id",
        "content_features",
        "book_navigation",
        "book_metadata",
        "document_data",
        "metadata",
        "yj.section_pid_count_map",
        "d5",
    }
    allocator = IdAllocator(reserved=reserved)
    spreads: list[SpreadPlan] = []
    all_pages: list[PagePlan] = []
    page_groups = build_layout_page_groups(
        len(image_paths) - shift_blank_count,
        shift_blank_count=shift_blank_count,
        page_layout=page_layout,
    )

    for page_group in page_groups:
        spread_paths = [image_paths[slot.page_number - 1] for slot in page_group]
        spread_infos = [image_infos[slot.page_number - 1] for slot in page_group]

        section_id = allocator.next("c")
        anchor_id = allocator.next("t")
        storyline_id = allocator.next("l")
        allocator.reserve(f"{section_id}-ad")

        pages: list[PagePlan] = []
        for slot, image_path, image_info in zip(page_group, spread_paths, spread_infos):
            page_number = slot.page_number
            head_id = allocator.next("i")
            tail_id = allocator.next("i")
            external_id = allocator.next("e")
            aux_id = allocator.next("d")
            resource_id = allocator.next("rsrc")
            book_filename = f"book_{page_number}{image_info.normalized_ext}"

            page_plan = PagePlan(
                input_path=image_path,
                image_info=image_info,
                book_filename=book_filename,
                head_id=head_id,
                tail_id=tail_id,
                external_id=external_id,
                aux_id=aux_id,
                resource_id=resource_id,
                is_shift_blank=slot.is_shift_blank,
            )
            pages.append(page_plan)
            all_pages.append(page_plan)

        spreads.append(
            SpreadPlan(
                section_id=section_id,
                anchor_id=anchor_id,
                storyline_id=storyline_id,
                pages=pages,
            )
        )

    return spreads, all_pages


def create_shift_blank_image(
    reference_info: ImageInfo,
    image_processing: ImageProcessingOptions,
    temp_root: Path,
) -> tuple[Path, ImageInfo]:
    Image, _, _ = load_pillow()
    width, height = image_processing.target_size or (reference_info.width, reference_info.height)
    mode = "RGB" if image_processing.preserve_color else "L"
    background = (255, 255, 255) if image_processing.preserve_color else 255
    blank_path = temp_root / "00000_shift_blank.jpg"
    blank_path.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new(mode, (width, height), background)
    try:
        image.save(
            blank_path,
            format="JPEG",
            quality=image_processing.jpeg_quality,
            optimize=True,
            subsampling=0,
        )
    finally:
        image.close()

    return (
        blank_path,
        ImageInfo(
            width=width,
            height=height,
            format_symbol=SYMBOL_IMAGE_JPEG,
            normalized_ext=".jpg",
        ),
    )


def prepend_shift_blank(
    image_paths: list[Path],
    image_infos: list[ImageInfo],
    image_processing: ImageProcessingOptions,
    processed_root: Path | None,
) -> tuple[list[Path], list[ImageInfo], Path | None]:
    if not image_paths:
        return image_paths, image_infos, processed_root

    temp_root = processed_root or Path(".analysis") / "tmp" / f"shift_{uuid.uuid4().hex}"
    blank_path, blank_info = create_shift_blank_image(image_infos[0], image_processing, temp_root)
    return [blank_path, *image_paths], [blank_info, *image_infos], temp_root


def resolve_cover_external_id(pages: list[PagePlan]) -> str:
    for page in pages:
        if not page.is_shift_blank:
            return page.external_id
    raise ValueError("没有可用的真实图片作为封面。")


def write_book_kdf(
    output_path: Path,
    template_assets: TemplateAssets,
    title: str,
    spreads: list[SpreadPlan],
    pages: list[PagePlan],
    layout_options: LayoutOptions,
) -> bytes:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    conn = sqlite3.connect(output_path)
    cursor = conn.cursor()
    try:
        cursor.execute("CREATE TABLE fragments(id char(40), payload_type char(10), payload_value blob, primary key (id))")
        cursor.execute(
            "CREATE TABLE fragment_properties(id char(40), key char(40), value char(40), primary key (id, key, value)) without rowid"
        )
        cursor.execute(
            "CREATE TABLE capabilities(key char(20), version smallint, primary key (key, version)) without rowid"
        )
        cursor.execute(
            "CREATE TABLE gc_fragment_properties(id varchar(40), key varchar(40), value varchar(40), primary key (id, key, value)) without rowid"
        )
        cursor.execute(
            "CREATE TABLE gc_reachable(id varchar(40), primary key (id)) without rowid"
        )

        for key, version in template_assets.capabilities:
            cursor.execute("INSERT INTO capabilities(key, version) VALUES (?, ?)", (key, version))

        def insert_fragment(fragment_id: str, payload_type: str, payload_value: bytes, element_type: str) -> None:
            cursor.execute(
                "INSERT INTO fragments(id, payload_type, payload_value) VALUES (?, ?, ?)",
                (fragment_id, payload_type, payload_value),
            )
            cursor.execute(
                "INSERT INTO fragment_properties(id, key, value) VALUES (?, 'element_type', ?)",
                (fragment_id, element_type),
            )

        def add_child(parent_id: str, child_id: str) -> None:
            cursor.execute(
                "INSERT INTO fragment_properties(id, key, value) VALUES (?, 'child', ?)",
                (parent_id, child_id),
            )

        for fragment in template_assets.static_fragments.values():
            insert_fragment(
                fragment_id=fragment.fragment_id,
                payload_type=fragment.payload_type,
                payload_value=fragment.payload_value,
                element_type=fragment.element_type,
            )

        section_ids = [spread.section_id for spread in spreads]
        page_aux_ids = [page.aux_id for page in pages]
        cover_external_id = resolve_cover_external_id(pages)

        insert_fragment(
            "book_metadata",
            "blob",
            build_book_metadata_blob(title, cover_external_id, layout_options),
            "book_metadata",
        )
        insert_fragment("metadata", "blob", build_metadata_blob(section_ids), "metadata")
        insert_fragment(
            "document_data",
            "blob",
            build_document_data_blob(section_ids, "d5"),
            "document_data",
        )
        add_child("document_data", "d5")
        insert_fragment("d5", "blob", build_global_aux_blob("d5", page_aux_ids), "auxiliary_data")
        insert_fragment(
            "yj.section_pid_count_map",
            "blob",
            build_section_pid_count_blob([(spread.section_id, spread.pid_count) for spread in spreads]),
            "yj.section_pid_count_map",
        )

        for spread in spreads:
            width = max(page.image_info.width for page in spread.pages)
            height = max(page.image_info.height for page in spread.pages)
            section_role = SYMBOL_LAYOUT_SECTION_ROLE if len(spread.pages) == 2 else SYMBOL_LAYOUT_HEAD_ROLE

            insert_fragment(
                spread.section_id,
                "blob",
                build_section_blob(
                    section_id=spread.section_id,
                    anchor_id=spread.anchor_id,
                    storyline_id=spread.storyline_id,
                    width=width,
                    height=height,
                    role_symbol=section_role,
                ),
                "section",
            )
            add_child(spread.section_id, f"{spread.section_id}-ad")
            add_child(spread.section_id, spread.storyline_id)

            insert_fragment(
                f"{spread.section_id}-spm",
                "blob",
                build_section_position_id_map_blob(spread.section_id, spread.spm_targets),
                "section_position_id_map",
            )

            insert_fragment(
                spread.storyline_id,
                "blob",
                build_storyline_blob(spread.storyline_id, [page.head_id for page in spread.pages]),
                "storyline",
            )
            add_child(spread.storyline_id, spread.storyline_id)
            for page in spread.pages:
                add_child(spread.storyline_id, page.head_id)

            single_page = len(spread.pages) == 1
            for page in spread.pages:
                insert_fragment(
                    page.head_id,
                    "blob",
                    build_head_blob(
                        head_id=page.head_id,
                        tail_id=page.tail_id,
                        width=page.image_info.width,
                        height=page.image_info.height,
                        single_page=single_page,
                    ),
                    "structure",
                )
                add_child(page.head_id, page.tail_id)

                insert_fragment(
                    page.tail_id,
                    "blob",
                    build_tail_blob(
                        tail_id=page.tail_id,
                        external_id=page.external_id,
                        width=page.image_info.width,
                        height=page.image_info.height,
                    ),
                    "structure",
                )
                add_child(page.tail_id, page.external_id)

                insert_fragment(
                    page.external_id,
                    "blob",
                    build_external_resource_blob(
                        external_id=page.external_id,
                        resource_id=page.resource_id,
                        aux_id=page.aux_id,
                        source_name=page.input_path.name,
                        width=page.image_info.width,
                        height=page.image_info.height,
                        format_symbol=page.image_info.format_symbol,
                    ),
                    "external_resource",
                )
                add_child(page.external_id, page.aux_id)
                add_child(page.external_id, page.resource_id)

                insert_fragment(
                    page.aux_id,
                    "blob",
                    build_aux_blob(
                        aux_id=page.aux_id,
                        resource_id=page.resource_id,
                        input_path=page.input_path,
                    ),
                    "auxiliary_data",
                )

                insert_fragment(
                    page.resource_id,
                    "path",
                    f"res/{page.resource_id}".encode("utf-8"),
                    "bcRawMedia",
                )

        conn.commit()
    finally:
        conn.close()

    return output_path.read_bytes()


def build_book_kcb(
    template_assets: TemplateAssets,
    pages: list[PagePlan],
    book_kdf_bytes: bytes,
    manifest_bytes: bytes,
    action_log_bytes: bytes,
    journal_bytes: bytes,
) -> bytes:
    content_hash: dict[str, str] = {}
    for page in pages:
        page_hash = compute_md5(page.input_path)
        content_hash[page.book_filename] = page_hash
        content_hash[f"resources/res/{page.resource_id}"] = page_hash

    content_hash["resources/book.kdf"] = compute_md5_bytes(book_kdf_bytes)
    content_hash["resources/ManifestFile"] = compute_md5_bytes(manifest_bytes)
    content_hash["resources/book.kdf-journal"] = compute_md5_bytes(journal_bytes)
    content_hash["action.log"] = compute_md5_bytes(action_log_bytes)

    metadata = dict(template_assets.metadata)
    metadata["id"] = str(uuid.uuid4())

    tool_data = dict(template_assets.tool_data)
    tool_data["created_on"] = current_timestamp()
    tool_data["last_modified_time"] = current_timestamp()

    payload = {
        "book_state": dict(template_assets.book_state),
        "content_hash": content_hash,
        "metadata": metadata,
        "tool_data": tool_data,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=3) + "\n").encode("utf-8")


def build_kpf(
    template_assets: TemplateAssets,
    input_dir: Path,
    output_path: Path,
    title: str | None,
    image_processing: ImageProcessingOptions | None = None,
    shift_first_page: bool = False,
    layout_options: LayoutOptions | None = None,
    progress_callback: BuildProgressCallback | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> BuildResult:
    raise_if_build_cancelled(stop_requested)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"输入目录不存在：{input_dir}")

    effective_layout = validate_layout_options(layout_options or LayoutOptions())
    if shift_first_page and effective_layout.page_layout != "facing":
        raise ValueError("Single 单页布局不支持首页偏移。")

    effective_template_assets = apply_layout_options(template_assets, effective_layout)
    effective_image_processing = image_processing or ImageProcessingOptions()
    image_paths = find_input_images(input_dir)
    if not image_paths:
        raise ValueError("输入目录中没有找到 JPG/PNG 图片。")
    if progress_callback is not None:
        progress_callback(
            BuildStageProgress(
                "ui.progress.collect.images",
                len(image_paths),
                len(image_paths),
                current_name=input_dir.name,
            )
        )

    resolved_title = resolve_title(input_dir, title)
    temp_root = Path(".analysis") / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_kdf_path = temp_root / f"{uuid.uuid4().hex}.kdf"
    processed_root: Path | None = None

    try:
        effective_image_paths, processed_root = preprocess_images(
            image_paths,
            effective_image_processing,
            shift_first_page=shift_first_page,
            template_direction=effective_template_assets.template_direction,
            progress_callback=progress_callback,
            stop_requested=stop_requested,
        )
        raise_if_build_cancelled(stop_requested)
        image_infos = inspect_image_infos(effective_image_paths)
        raise_if_build_cancelled(stop_requested)
        if progress_callback is not None:
            progress_callback(
                BuildStageProgress(
                    "ui.progress.inspect.images",
                    len(effective_image_paths),
                    len(effective_image_paths),
                    current_name=input_dir.name,
                )
            )
        shift_blank_count = 0
        if shift_first_page:
            raise_if_build_cancelled(stop_requested)
            effective_image_paths, image_infos, processed_root = prepend_shift_blank(
                effective_image_paths,
                image_infos,
                effective_image_processing,
                processed_root,
            )
            shift_blank_count = 1

        raise_if_build_cancelled(stop_requested)
        spreads, pages = build_volume_plan(
            effective_image_paths,
            image_infos,
            shift_blank_count=shift_blank_count,
            page_layout=effective_layout.page_layout,
        )
        raise_if_build_cancelled(stop_requested)
        if progress_callback is not None:
            progress_callback(
                BuildStageProgress(
                    "ui.progress.build.layout",
                    len(pages),
                    len(pages),
                    current_name=input_dir.name,
                )
            )
        book_kdf_bytes = write_book_kdf(
            output_path=temp_kdf_path,
            template_assets=effective_template_assets,
            title=resolved_title,
            spreads=spreads,
            pages=pages,
            layout_options=effective_layout,
        )
        raise_if_build_cancelled(stop_requested)
        manifest_bytes = effective_template_assets.manifest_bytes or build_manifest()
        action_log_bytes = build_action_log()
        journal_bytes = b""
        book_kcb_bytes = build_book_kcb(
            template_assets=effective_template_assets,
            pages=pages,
            book_kdf_bytes=book_kdf_bytes,
            manifest_bytes=manifest_bytes,
            action_log_bytes=action_log_bytes,
            journal_bytes=journal_bytes,
        )
        raise_if_build_cancelled(stop_requested)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        archive_total = len(pages) * 2 + 6
        archive_current = 0

        def report_archive_progress(current_name: str = "") -> None:
            if progress_callback is None:
                return
            progress_callback(
                BuildStageProgress(
                    "ui.progress.write.kpf",
                    archive_current,
                    archive_total,
                    current_name=current_name,
                )
            )

        report_archive_progress(output_path.name)
        try:
            with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                raise_if_build_cancelled(stop_requested)
                archive.writestr("book.kcb", book_kcb_bytes)
                archive_current += 1
                report_archive_progress("book.kcb")
                raise_if_build_cancelled(stop_requested)
                archive.writestr("action.log", action_log_bytes)
                archive_current += 1
                report_archive_progress("action.log")
                for page in pages:
                    raise_if_build_cancelled(stop_requested)
                    archive.write(page.input_path, page.book_filename, compress_type=zipfile.ZIP_STORED)
                    archive_current += 1
                    report_archive_progress(page.input_path.name)
                raise_if_build_cancelled(stop_requested)
                archive.writestr("resources/ManifestFile", manifest_bytes)
                archive_current += 1
                report_archive_progress("ManifestFile")
                raise_if_build_cancelled(stop_requested)
                archive.writestr("resources/book.kdf", book_kdf_bytes)
                archive_current += 1
                report_archive_progress("book.kdf")
                raise_if_build_cancelled(stop_requested)
                archive.writestr("resources/book.kdf-journal", journal_bytes)
                archive_current += 1
                report_archive_progress("book.kdf-journal")
                for page in pages:
                    raise_if_build_cancelled(stop_requested)
                    archive.write(page.input_path, f"resources/res/{page.resource_id}", compress_type=zipfile.ZIP_STORED)
                    archive_current += 1
                    report_archive_progress(page.input_path.name)
                archive_current += 1
                report_archive_progress(output_path.name)
        except BuildCancelled:
            output_path.unlink(missing_ok=True)
            raise

        panel_label = "off"
        if effective_layout.virtual_panels:
            panel_label = effective_layout.panel_movement
        print(f"配置方向: {effective_template_assets.template_direction or 'unknown'}")
        print(f"页面布局: {effective_layout.page_layout}")
        print(f"虚拟面板: {panel_label}")
        print(f"输出标题: {resolved_title}")
        print(f"图片数量: {len(image_paths)}")
        if shift_first_page:
            print("shift: on（首个 spread 前补白页）")
        print(f"spread 数量: {len(spreads)}")
        print(f"已生成: {output_path}")
        if effective_image_processing.enabled:
            print(
                "图像预处理: "
                f"crop={effective_image_processing.crop_mode}, "
                f"target={effective_image_processing.target_size}, "
                f"color={effective_image_processing.preserve_color}, "
                f"gamma={effective_image_processing.gamma}, "
                f"autocontrast={effective_image_processing.autocontrast}, "
                f"autolevel={effective_image_processing.autolevel}, "
                f"jpeg_quality={effective_image_processing.jpeg_quality}"
            )
        return BuildResult(
            input_dir=input_dir,
            output_path=output_path,
            title=resolved_title,
            template_direction=effective_template_assets.template_direction,
        )
    finally:
        temp_kdf_path.unlink(missing_ok=True)
        if processed_root is not None:
            shutil.rmtree(processed_root, ignore_errors=True)


def find_batch_directories(batch_dir: Path, output_dir: Path | None = None) -> list[Path]:
    ignored_paths: set[Path] = set()
    if output_dir is not None:
        ignored_paths.add(output_dir.resolve())

    subdirs = [
        path
        for path in batch_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and path.name != "_kpf_output"
        and path.resolve() not in ignored_paths
    ]
    return sorted(subdirs, key=lambda path: natural_sort_key(path.name))


def build_batch(
    template_assets: TemplateAssets,
    batch_dir: Path,
    output_dir: Path | None,
    image_processing: ImageProcessingOptions | None,
    shift_first_page: bool,
    layout_options: LayoutOptions | None = None,
    emit_kfx: bool = False,
    kfx_plugin_ref: str = DEFAULT_KFX_PLUGIN_ID,
    jobs: int = 1,
) -> tuple[list[BuildResult], list[tuple[Path, str]]]:
    if not batch_dir.is_dir():
        raise FileNotFoundError(f"批量输入目录不存在：{batch_dir}")
    if jobs <= 0:
        raise ValueError("并行任务数 `jobs` 必须大于 0。")

    resolved_output_dir = output_dir if output_dir is not None else batch_dir / "_kpf_output"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    batch_subdirs = find_batch_directories(batch_dir, resolved_output_dir)
    successes: list[BuildResult] = []
    failures: list[tuple[Path, str]] = []

    worker_count = min(jobs, len(batch_subdirs)) if batch_subdirs else 1
    if worker_count == 1:
        for subdir in batch_subdirs:
            output_path = resolved_output_dir / f"{subdir.name}.kpf"
            print(f"开始处理: {subdir}")
            try:
                result = build_kpf(
                    template_assets=template_assets,
                    input_dir=subdir,
                    output_path=output_path,
                    title=subdir.name,
                    image_processing=image_processing,
                    shift_first_page=shift_first_page,
                    layout_options=layout_options,
                )
                if emit_kfx:
                    kfx_result = convert_kpf_to_kfx(result.output_path, plugin_ref=kfx_plugin_ref)
                    result.kfx_output_path = kfx_result.kfx_path
                successes.append(result)
            except Exception as exc:
                failures.append((subdir, str(exc)))
                print(f"处理失败: {subdir}")
                print(f"失败原因: {exc}")
        return successes, failures

    try:
        executor: concurrent.futures.Executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=worker_count
        )
        executor_label = "进程"
    except (OSError, PermissionError) as exc:
        print(f"批量并行: 当前环境无法启用多进程（{exc}），自动回退到线程池")
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
        executor_label = "线程"

    print(f"批量并行: {worker_count} 个{executor_label}")
    worker_image_processing = (
        replace(image_processing, preprocessing_workers=1)
        if image_processing is not None
        else None
    )
    with executor:
        future_to_subdir = {
            executor.submit(
                _build_batch_volume_worker,
                template_assets,
                subdir,
                resolved_output_dir / f"{subdir.name}.kpf",
                worker_image_processing,
                shift_first_page,
                layout_options,
                emit_kfx,
                kfx_plugin_ref,
            ): subdir
            for subdir in batch_subdirs
        }

        for future in concurrent.futures.as_completed(future_to_subdir):
            subdir = future_to_subdir[future]
            try:
                result = future.result()
                successes.append(result)
            except Exception as exc:
                failures.append((subdir, str(exc)))
                print(f"处理失败: {subdir}")
                print(f"失败原因: {exc}")

    successes.sort(key=lambda result: natural_sort_key(result.input_dir.name))
    failures.sort(key=lambda item: natural_sort_key(item[0].name))

    return successes, failures


def _build_batch_volume_worker(
    template_assets: TemplateAssets,
    input_dir: Path,
    output_path: Path,
    image_processing: ImageProcessingOptions | None,
    shift_first_page: bool,
    layout_options: LayoutOptions | None,
    emit_kfx: bool,
    kfx_plugin_ref: str,
) -> BuildResult:
    result = build_kpf(
        template_assets=template_assets,
        input_dir=input_dir,
        output_path=output_path,
        title=input_dir.name,
        image_processing=image_processing,
        shift_first_page=shift_first_page,
        layout_options=layout_options,
    )
    if emit_kfx:
        kfx_result = convert_kpf_to_kfx(result.output_path, plugin_ref=kfx_plugin_ref)
        result.kfx_output_path = kfx_result.kfx_path
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="基于仓库内置的 Kindle Create Comics 静态资产重建 KPF（默认不依赖外部模板）。"
    )
    parser.add_argument(
        "--template",
        type=Path,
        help="实验/兼容入口：Kindle Create 导出的模板 .kpf/.zip；默认留空并使用仓库内置 RTL 双页漫画静态资产",
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--input", type=Path, help="包含页面图片的输入文件夹")
    mode_group.add_argument("--batch", type=Path, help="批量输入目录，目录下每个子文件夹视为一卷")

    parser.add_argument("--output", type=Path, help="单卷模式输出 .kpf 文件路径")
    parser.add_argument("--output-dir", type=Path, help="批量模式输出目录，默认使用 `_kpf_output`")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="批量模式并行任务数；1 为串行，>1 时按卷启用多进程。",
    )
    parser.add_argument(
        "--performance-mode",
        type=parse_performance_mode,
        default="balanced",
        metavar="{eco,balanced,max}",
        help="单卷图片预处理性能模式：eco 串行，balanced 约半数核心，max 尽量吃满 CPU。",
    )
    parser.add_argument(
        "--emit-kfx",
        action="store_true",
        help="在生成 KPF 后，直接使用 KFX Output 插件源码继续生成同名 .kfx，不调用 calibre CLI。",
    )
    parser.add_argument(
        "--kfx-plugin",
        type=str,
        default=DEFAULT_KFX_PLUGIN_ID,
        help="插件 ID、插件目录或 zip 路径；默认使用 `img2kpf_core/plugins/kfx_output`。",
    )
    parser.add_argument("--title", type=str, help="单卷模式可选标题，默认使用输入文件夹名")
    parser.add_argument(
        "--shift",
        action="store_true",
        help="Facing 双页偏移：在最前面补一张白页，让第一张真实图片落到第二个槽位，用于跨页对齐。",
    )
    parser.add_argument(
        "--reading-direction",
        choices=("rtl", "ltr"),
        default="rtl",
        help="阅读方向：rtl 为右到左，ltr 为左到右。",
    )
    parser.add_argument(
        "--page-layout",
        choices=("facing", "single"),
        default="facing",
        help="页面布局：facing 为双页 spread，single 为单页 section。",
    )
    parser.add_argument(
        "--virtual-panels",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否启用 Virtual Panels。",
    )
    parser.add_argument(
        "--panel-movement",
        choices=("vertical", "horizontal"),
        default="vertical",
        help="Virtual Panels 的移动方向：vertical 或 horizontal。",
    )
    parser.add_argument(
        "--image-preset",
        type=parse_image_preset,
        metavar="{none,standard,bright}",
        default="bright",
        help="图像预处理预设：none（中性）/ standard（标准增强）/ bright（增强提亮）；旧的 kcc-* 名称仍兼容。",
    )
    parser.add_argument(
        "--crop-mode",
        type=parse_crop_mode,
        metavar="{off,smart,spread-fill}",
        default="off",
        help="图像裁边模式：off 为关闭，smart 为智能单页裁边，spread-fill 为双页联动裁边。",
    )
    parser.add_argument(
        "--crop-edge-threshold",
        type=float,
        default=KCC_CROP_STRENGTH_DEFAULT,
        help="智能单页保留比例，范围 0.70-1.00；1.00 表示使用满足目标比例的最大裁切框。",
    )
    parser.add_argument(
        "--spread-fill-edge-threshold",
        type=float,
        default=KCC_CROP_STRENGTH_DEFAULT,
        help="双页联动外边保留比例，范围 0.70-1.00；越高越接近原图。",
    )
    parser.add_argument(
        "--spread-fill-inner-enabled",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="双页联动裁边是否允许裁切内边。",
    )
    parser.add_argument(
        "--spread-fill-inner-edge-threshold",
        type=float,
        default=KCC_CROP_STRENGTH_DEFAULT,
        help="双页联动内边保留比例，范围 0.70-1.00；越高越保护中缝。",
    )
    parser.add_argument(
        "--target-size",
        type=parse_size,
        help="将单页适配到固定画布尺寸，格式如 1240x1860。",
    )
    parser.add_argument(
        "--scribe-panel",
        action="store_true",
        help="使用 Kindle Scribe 横屏双页的单页槽位 1240x1860。",
    )
    parser.add_argument(
        "--preserve-color",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="是否保留彩色处理链，默认由 image preset 决定。",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        help="亮度 gamma，1.0 为关闭，1.8 为偏亮的旧版风格基线。",
    )
    parser.add_argument(
        "--autocontrast",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="是否启用自动对比度，默认由 image preset 决定。",
    )
    parser.add_argument(
        "--autolevel",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="是否启用轻量黑位提升，默认由 image preset 决定。",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        help="处理后 JPEG 输出质量，建议 Scribe 使用 90。",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    template_assets = load_template_assets(args.template) if args.template is not None else load_bundled_template_assets()
    image_processing = resolve_image_processing_options(args)
    layout_options = validate_layout_options(
        LayoutOptions(
            reading_direction=args.reading_direction,
            page_layout=args.page_layout,
            virtual_panels=bool(args.virtual_panels),
            panel_movement=args.panel_movement,
        )
    )

    if args.shift and layout_options.page_layout != "facing":
        parser.error("Single 单页布局不支持 --shift。")

    if args.input is not None:
        if args.output is None:
            parser.error("单卷模式必须提供 --output。")
        if args.output_dir is not None:
            parser.error("单卷模式不能使用 --output-dir。")

        result = build_kpf(
            template_assets=template_assets,
            input_dir=args.input,
            output_path=args.output,
            title=args.title,
            image_processing=image_processing,
            shift_first_page=args.shift,
            layout_options=layout_options,
        )
        if args.emit_kfx:
            kfx_result = convert_kpf_to_kfx(result.output_path, plugin_ref=args.kfx_plugin)
            result.kfx_output_path = kfx_result.kfx_path
        return

    if args.output is not None:
        parser.error("批量模式不能使用 --output，请改用 --output-dir。")
    if args.title is not None:
        parser.error("批量模式不需要 --title，脚本会自动使用子文件夹名作为标题。")
    if args.jobs < 1:
        parser.error("批量模式的 --jobs 必须大于 0。")

    successes, failures = build_batch(
        template_assets=template_assets,
        batch_dir=args.batch,
        output_dir=args.output_dir,
        image_processing=image_processing,
        shift_first_page=args.shift,
        layout_options=layout_options,
        emit_kfx=args.emit_kfx,
        kfx_plugin_ref=args.kfx_plugin,
        jobs=args.jobs,
    )

    print(f"批量完成：成功 {len(successes)}，失败 {len(failures)}")
    if successes:
        print("成功条目：")
        for result in successes:
            if result.kfx_output_path is not None:
                print(f"- {result.input_dir.name} -> {result.output_path} -> {result.kfx_output_path}")
            else:
                print(f"- {result.input_dir.name} -> {result.output_path}")
    if failures:
        print("失败条目：")
        for input_dir, error_message in failures:
            print(f"- {input_dir.name}: {error_message}")


if __name__ == "__main__":
    main()
