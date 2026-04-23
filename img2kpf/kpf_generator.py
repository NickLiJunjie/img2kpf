from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import math
import shutil
import sqlite3
import struct
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    "spread-safe": "spread-safe",
    "spread-fill": "spread-fill",
    "kcc-spread": "spread-safe",
    "kcc-spread-fill": "spread-fill",
}

VALID_IMAGE_PRESETS = ("none", "standard", "bright")
VALID_CROP_MODES = ("off", "smart", "spread-safe", "spread-fill")

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

ReadingDirection = Literal["rtl", "ltr"]
PageLayout = Literal["facing", "single"]
PanelMovement = Literal["vertical", "horizontal"]
PagePosition = Literal["first", "second"]


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


@dataclass(frozen=True)
class LayoutOptions:
    reading_direction: ReadingDirection = "rtl"
    page_layout: PageLayout = "facing"
    virtual_panels: bool = True
    panel_movement: PanelMovement = "vertical"


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
    return datetime.now(UTC).strftime("%a %b %d %H:%M:%S UTC %Y")


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
    preserve_color: bool = True
    gamma: float = 1.0
    autocontrast: bool = False
    autolevel: bool = False
    jpeg_quality: int = 90

    @property
    def enabled(self) -> bool:
        return (
            self.target_size is not None
            or self.crop_mode != "off"
            or not self.preserve_color
            or self.gamma != 1.0
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


def compute_inner_trim_needed_for_height_fill(
    crop_box: tuple[int, int, int, int],
    target_size: tuple[int, int] | None,
) -> int:
    if target_size is None:
        return 0

    left, top, right, bottom = crop_box
    cropped_width = right - left
    cropped_height = bottom - top
    if cropped_width <= 0 or cropped_height <= 0:
        return 0

    target_aspect = target_size[0] / target_size[1]
    current_aspect = cropped_width / cropped_height
    if current_aspect <= target_aspect:
        return 0

    return max(0, math.ceil(cropped_width - cropped_height * target_aspect))


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


def add_inner_trim_to_crop_box(
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int],
    page_position: str,
    template_direction: str | None,
    inner_trim: int,
) -> tuple[int, int, int, int]:
    margins = CropMargins.from_box(crop_box, image_size)
    outer_edge, inner_edge = get_facing_page_horizontal_roles(page_position, template_direction)
    updated = CropMargins(
        left=margins.left + inner_trim if inner_edge == "left" else margins.left,
        top=margins.top,
        right=margins.right + inner_trim if inner_edge == "right" else margins.right,
        bottom=margins.bottom,
    )
    return updated.to_box(image_size)


def maybe_add_inner_white_trim(
    left_image,
    left_crop_box: tuple[int, int, int, int] | None,
    right_image,
    right_crop_box: tuple[int, int, int, int] | None,
    template_direction: str | None,
    target_size: tuple[int, int] | None,
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    if left_crop_box is None or right_crop_box is None:
        return left_crop_box, right_crop_box

    left_required = compute_inner_trim_needed_for_height_fill(left_crop_box, target_size)
    right_required = compute_inner_trim_needed_for_height_fill(right_crop_box, target_size)
    if left_required <= 0 and right_required <= 0:
        return left_crop_box, right_crop_box

    left_available = measure_inner_white_margin(left_image, left_crop_box, "first", template_direction)
    right_available = measure_inner_white_margin(right_image, right_crop_box, "second", template_direction)
    shared_inner_trim = min(max(left_required, right_required), left_available, right_available)
    if shared_inner_trim <= 0:
        return left_crop_box, right_crop_box

    adjusted_left = add_inner_trim_to_crop_box(
        left_image.size,
        left_crop_box,
        "first",
        template_direction,
        shared_inner_trim,
    )
    adjusted_right = add_inner_trim_to_crop_box(
        right_image.size,
        right_crop_box,
        "second",
        template_direction,
        shared_inner_trim,
    )
    if not crop_box_is_safe(adjusted_left, left_image.size):
        return left_crop_box, right_crop_box
    if not crop_box_is_safe(adjusted_right, right_image.size):
        return left_crop_box, right_crop_box
    return adjusted_left, adjusted_right


def is_spread_crop_mode(crop_mode: str) -> bool:
    return normalize_crop_mode(crop_mode) in {"spread-safe", "spread-fill"}


def synchronize_facing_crop_boxes(
    left_size: tuple[int, int],
    left_crop_box: tuple[int, int, int, int] | None,
    right_size: tuple[int, int],
    right_crop_box: tuple[int, int, int, int] | None,
    template_direction: str | None,
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    if left_crop_box is None or right_crop_box is None:
        return None, None

    left_margins = CropMargins.from_box(left_crop_box, left_size)
    right_margins = CropMargins.from_box(right_crop_box, right_size)

    left_width, left_height = left_size
    right_width, right_height = right_size

    left_outer, _ = get_outer_inner_horizontal_margins(left_margins, "first", template_direction)
    right_outer, _ = get_outer_inner_horizontal_margins(right_margins, "second", template_direction)

    shared_top_ratio = min(left_margins.top / left_height, right_margins.top / right_height)
    shared_bottom_ratio = min(left_margins.bottom / left_height, right_margins.bottom / right_height)
    shared_outer_ratio = min(left_outer / left_width, right_outer / right_width)

    synchronized_left = build_facing_crop_box(
        left_size,
        "first",
        template_direction,
        outer_ratio=shared_outer_ratio,
        top_ratio=shared_top_ratio,
        bottom_ratio=shared_bottom_ratio,
        inner_ratio=0.0,
    )
    synchronized_right = build_facing_crop_box(
        right_size,
        "second",
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

    return synchronized_left, synchronized_right


def build_smart_crop_box(
    image,
    target_size: tuple[int, int] | None = None,
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
        adjusted = expand_crop_box_towards_target_aspect(crop_box, image.size, target_size)
        if adjusted == (0, 0, image.size[0], image.size[1]):
            return None
        return adjusted
    if background == "black" and (max(edge_means) <= 40 or sum(value <= 30 for value in edge_means) >= 3):
        adjusted = expand_crop_box_towards_target_aspect(crop_box, image.size, target_size)
        if adjusted == (0, 0, image.size[0], image.size[1]):
            return None
        return adjusted
    return None


def smart_crop_image(image, target_size: tuple[int, int] | None = None):
    crop_box = build_smart_crop_box(image, target_size=target_size)
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

    if options.preserve_color:
        return Image.merge("YCbCr", (luminance, chroma_blue, chroma_red)).convert("RGB")
    return luminance


def fit_image_to_canvas(image, target_size: tuple[int, int], preserve_color: bool):
    Image, ImageOps, _ = load_pillow()
    resized = ImageOps.contain(image, target_size, Image.Resampling.LANCZOS)
    canvas_mode = "RGB" if preserve_color else "L"
    background = (255, 255, 255) if preserve_color else 255
    canvas = Image.new(canvas_mode, target_size, background)
    offset_x = (target_size[0] - resized.size[0]) // 2
    offset_y = (target_size[1] - resized.size[1]) // 2
    canvas.paste(resized, (offset_x, offset_y))
    return canvas


def save_processed_image(image, output_path: Path, options: ImageProcessingOptions) -> None:
    if normalize_crop_mode(options.crop_mode) == "smart":
        image = smart_crop_image(image, target_size=options.target_size)

    image = apply_luminance_operations(image, options)

    if options.target_size is not None:
        image = fit_image_to_canvas(image, options.target_size, options.preserve_color)

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
) -> None:
    if len(input_paths) != len(output_paths):
        raise ValueError("输入页数与输出页数不匹配。")
    if not input_paths:
        return
    if len(input_paths) == 1:
        process_single_image(input_paths[0], output_paths[0], options)
        return

    loaded_images = [load_source_image(path) for path in input_paths]
    try:
        crop_boxes = [build_kcc_crop_box(image) for image in loaded_images]
        synchronized_boxes = list(
            synchronize_facing_crop_boxes(
                loaded_images[0].size,
                crop_boxes[0],
                loaded_images[1].size,
                crop_boxes[1],
                template_direction,
            )
        )
        if allow_inner_white_fill:
            synchronized_boxes = list(
                maybe_add_inner_white_trim(
                    loaded_images[0],
                    synchronized_boxes[0],
                    loaded_images[1],
                    synchronized_boxes[1],
                    template_direction,
                    options.target_size,
                )
            )

        for image, crop_box, output_path in zip(loaded_images, synchronized_boxes, output_paths):
            processed = apply_crop_box(image, crop_box)
            try:
                save_processed_image(processed, output_path, replace(options, crop_mode="off"))
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
        processed = apply_crop_box(image, outer_only_crop_box)
        try:
            save_processed_image(processed, output_path, replace(options, crop_mode="off"))
        finally:
            if processed is not image:
                processed.close()
    finally:
        image.close()


def preprocess_images(
    image_paths: list[Path],
    options: ImageProcessingOptions,
    shift_first_page: bool = False,
    template_direction: str | None = None,
) -> tuple[list[Path], Path | None]:
    if not options.enabled:
        return image_paths, None

    processed_root = Path(".analysis") / "tmp" / f"processed_{uuid.uuid4().hex}"
    processed_root.mkdir(parents=True, exist_ok=True)
    processed_paths: list[Path] = []
    try:
        if is_spread_crop_mode(options.crop_mode):
            output_index = 1
            page_groups = build_layout_page_groups(
                len(image_paths),
                shift_blank_count=1 if shift_first_page and image_paths else 0,
                page_layout="facing",
            )
            for page_group in page_groups:
                source_slots = [slot for slot in page_group if slot.source_index is not None]
                spread_paths = [image_paths[slot.source_index] for slot in source_slots]
                spread_output_paths = [
                    processed_root / f"{output_index + offset:05d}.jpg"
                    for offset in range(len(spread_paths))
                ]
                if len(source_slots) == 1:
                    process_kcc_facing_single_page(
                        spread_paths[0],
                        spread_output_paths[0],
                        options,
                        template_direction=template_direction,
                        page_position=source_slots[0].page_position,
                    )
                else:
                    process_kcc_spread_group(
                        spread_paths,
                        spread_output_paths,
                        options,
                        template_direction=template_direction,
                        allow_inner_white_fill=normalize_crop_mode(options.crop_mode) == "spread-fill",
                    )
                processed_paths.extend(spread_output_paths)
                output_index += len(spread_paths)
        else:
            for index, image_path in enumerate(image_paths, start=1):
                processed_path = processed_root / f"{index:05d}.jpg"
                process_single_image(image_path, processed_path, options)
                processed_paths.append(processed_path)
    except Exception:
        shutil.rmtree(processed_root, ignore_errors=True)
        raise

    return processed_paths, processed_root


def resolve_image_processing_options(args: argparse.Namespace) -> ImageProcessingOptions:
    presets = {
        "none": {
            "gamma": 1.0,
            "autocontrast": False,
            "autolevel": False,
            "jpeg_quality": 90,
            "preserve_color": True,
        },
        "standard": {
            "gamma": 1.0,
            "autocontrast": True,
            "autolevel": False,
            "jpeg_quality": 90,
            "preserve_color": True,
        },
        "bright": {
            "gamma": 1.8,
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
    autocontrast = preset["autocontrast"] if args.autocontrast is None else args.autocontrast
    autolevel = preset["autolevel"] if args.autolevel is None else args.autolevel
    jpeg_quality = preset["jpeg_quality"] if args.jpeg_quality is None else args.jpeg_quality

    return ImageProcessingOptions(
        target_size=target_size,
        crop_mode=crop_mode,
        preserve_color=preserve_color,
        gamma=gamma,
        autocontrast=autocontrast,
        autolevel=autolevel,
        jpeg_quality=jpeg_quality,
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
) -> BuildResult:
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
        )
        image_infos = inspect_image_infos(effective_image_paths)
        shift_blank_count = 0
        if shift_first_page:
            effective_image_paths, image_infos, processed_root = prepend_shift_blank(
                effective_image_paths,
                image_infos,
                effective_image_processing,
                processed_root,
            )
            shift_blank_count = 1

        spreads, pages = build_volume_plan(
            effective_image_paths,
            image_infos,
            shift_blank_count=shift_blank_count,
            page_layout=effective_layout.page_layout,
        )
        book_kdf_bytes = write_book_kdf(
            output_path=temp_kdf_path,
            template_assets=effective_template_assets,
            title=resolved_title,
            spreads=spreads,
            pages=pages,
            layout_options=effective_layout,
        )
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

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("book.kcb", book_kcb_bytes)
            archive.writestr("action.log", action_log_bytes)
            for page in pages:
                archive.write(page.input_path, page.book_filename)
            archive.writestr("resources/ManifestFile", manifest_bytes)
            archive.writestr("resources/book.kdf", book_kdf_bytes)
            archive.writestr("resources/book.kdf-journal", journal_bytes)
            for page in pages:
                archive.write(page.input_path, f"resources/res/{page.resource_id}")

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
    with executor:
        future_to_subdir = {
            executor.submit(
                _build_batch_volume_worker,
                template_assets,
                subdir,
                resolved_output_dir / f"{subdir.name}.kpf",
                image_processing,
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
        help="批量模式并行卷数；1 为串行，>1 时按卷启用多进程。",
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
        help="插件 ID、插件目录或 zip 路径；默认使用 `img2kpf/plugins/kfx_output`。",
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
        metavar="{off,smart,spread-safe,spread-fill}",
        default="off",
        help="图像裁边模式，off 为关闭，smart 为单页保守裁边，spread-safe 为双页联动裁边，spread-fill 为在安全前提下可裁内侧白边的双页联动裁边；旧的 kcc-* 名称仍兼容。",
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
