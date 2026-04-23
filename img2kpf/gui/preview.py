from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .i18n import translate_gui_text
from ..kpf_generator import (
    ImageProcessingOptions,
    LayoutPageSlot,
    LayoutOptions,
    apply_crop_box,
    apply_luminance_operations,
    build_smart_crop_box,
    build_layout_page_groups,
    build_kcc_crop_box,
    build_outer_only_crop_box,
    find_input_images,
    fit_image_to_canvas,
    get_facing_page_horizontal_roles,
    is_spread_crop_mode,
    load_pillow,
    load_source_image,
    maybe_add_inner_white_trim,
    synchronize_facing_crop_boxes,
)


PREVIEW_TARGET_PAGE = 1
PREVIEW_BG = "#ffffff"
PREVIEW_PANEL = "#f6f7fb"
PREVIEW_BORDER = "#d9dbe3"
PREVIEW_CROP = "#ff453a"
PREVIEW_MAX_SPREAD_WIDTH = 5200
PREVIEW_MAX_SINGLE_WIDTH = 2800


def _tr(text: str, language: str, **kwargs: object) -> str:
    return translate_gui_text(text, language, **kwargs)


@dataclass(frozen=True)
class PreviewPage:
    page_number: int | None
    label: str
    source_image: object
    processed_image: object
    crop_box: tuple[int, int, int, int] | None
    page_position: str
    is_blank: bool = False


@dataclass(frozen=True)
class PreviewRenderResult:
    image: object
    summary: str
    hint: str
    current_page_number: int
    total_pages: int
    available_page_numbers: tuple[int, ...]


@dataclass(frozen=True)
class PreviewSelection:
    selected_group: tuple[LayoutPageSlot, ...]
    current_page_number: int
    total_pages: int
    available_page_numbers: tuple[int, ...]


def _group_leading_page_number(group: tuple[LayoutPageSlot, ...]) -> int:
    return next(slot.source_index + 1 for slot in group if slot.source_index is not None)


def render_preview(
    source_dir: Path,
    image_processing: ImageProcessingOptions,
    layout_options: LayoutOptions,
    shift_first_page: bool,
    show_crop_boxes: bool,
    anchor_page_number: int | None = None,
    language: str = "zh",
) -> PreviewRenderResult:
    image_paths = find_input_images(source_dir)
    if not image_paths:
        raise ValueError(_tr("ui.no.previewable.images.selected.folder", language))

    selection = resolve_preview_selection(
        image_paths=image_paths,
        layout_options=layout_options,
        shift_first_page=shift_first_page,
        anchor_page_number=anchor_page_number,
        language=language,
    )
    logical_pages = _select_preview_pages(
        image_paths=image_paths,
        selection=selection,
        layout_options=layout_options,
        image_processing=image_processing,
        language=language,
    )
    ordered_pages = _visual_order(logical_pages, layout_options)
    rendered = _render_preview_image(ordered_pages, show_crop_boxes=show_crop_boxes)

    ordered_labels = " / ".join(page.label for page in ordered_pages)
    summary = _tr(
        "ui.preview.sample.anchor.l.r",
        language,
        source=source_dir.name,
        page=selection.current_page_number,
        labels=ordered_labels,
    )
    hint = _tr(
        "ui.source.preview.red.boxes.show.retained.crop"
        if show_crop_boxes
        else "ui.processed.preview.applies.crop.tone.single.facing",
        language,
    )
    return PreviewRenderResult(
        image=rendered,
        summary=summary,
        hint=hint,
        current_page_number=selection.current_page_number,
        total_pages=selection.total_pages,
        available_page_numbers=selection.available_page_numbers,
    )


def resolve_preview_selection(
    image_paths: list[Path],
    layout_options: LayoutOptions,
    shift_first_page: bool,
    anchor_page_number: int | None = None,
    language: str = "zh",
) -> PreviewSelection:
    if not image_paths:
        raise ValueError(_tr("ui.no.previewable.images.selected.folder", language))

    total_pages = len(image_paths)
    requested_page_number = anchor_page_number if anchor_page_number is not None else min(PREVIEW_TARGET_PAGE, total_pages)
    requested_page_number = max(1, min(requested_page_number, total_pages))
    anchor_index = requested_page_number - 1
    page_groups = build_layout_page_groups(
        total_pages,
        shift_blank_count=1 if shift_first_page else 0,
        page_layout=layout_options.page_layout,
    )
    available_page_numbers = tuple(_group_leading_page_number(group) for group in page_groups if any(slot.source_index is not None for slot in group))
    selected_group = next(
        (group for group in page_groups if any(slot.source_index == anchor_index for slot in group)),
        None,
    )
    if selected_group is None:
        raise ValueError(_tr("ui.preview.temporarily.unavailable", language))

    current_page_number = _group_leading_page_number(selected_group)
    return PreviewSelection(
        selected_group=selected_group,
        current_page_number=current_page_number,
        total_pages=total_pages,
        available_page_numbers=available_page_numbers,
    )


def _select_preview_pages(
    image_paths: list[Path],
    selection: PreviewSelection,
    layout_options: LayoutOptions,
    image_processing: ImageProcessingOptions,
    language: str,
) -> list[PreviewPage]:
    anchor_index = selection.current_page_number - 1
    preview_group = selection.selected_group

    processed_cache = _build_processed_cache(
        image_paths=image_paths,
        preview_group=preview_group,
        image_processing=image_processing,
        layout_options=layout_options,
    )

    if layout_options.page_layout == "single":
        slot = preview_group[0]
        return [
            _build_preview_page(
                image_paths=image_paths,
                image_index=slot.source_index,
                page_position=slot.page_position,
                processed_cache=processed_cache,
                image_processing=image_processing,
                language=language,
            )
        ]

    reference_size = _reference_blank_size(image_paths, processed_cache, anchor_index, image_processing)
    pages: list[PreviewPage] = []
    for slot in preview_group:
        pages.append(
            _build_preview_page(
                image_paths=image_paths,
                image_index=slot.source_index,
                page_position=slot.page_position,
                processed_cache=processed_cache,
                image_processing=image_processing,
                reference_size=reference_size,
                language=language,
            )
        )
    return pages


def _reference_blank_size(
    image_paths: list[Path],
    processed_cache: dict[int, tuple[object, object, tuple[int, int, int, int] | None]],
    fallback_index: int,
    image_processing: ImageProcessingOptions,
) -> tuple[int, int]:
    if image_processing.target_size is not None:
        return image_processing.target_size
    cached = processed_cache.get(fallback_index)
    if cached is not None:
        return cached[1].size
    source = load_source_image(image_paths[fallback_index])
    try:
        return source.size
    finally:
        source.close()


def _build_preview_page(
    image_paths: list[Path],
    image_index: int | None,
    page_position: str,
    processed_cache: dict[int, tuple[object, object, tuple[int, int, int, int] | None]],
    image_processing: ImageProcessingOptions,
    reference_size: tuple[int, int] | None = None,
    language: str = "zh",
) -> PreviewPage:
    if image_index is None:
        source_image = _blank_page_image(reference_size or image_processing.target_size or (1240, 1860), preserve_color=True)
        processed_image = _blank_page_image(reference_size or image_processing.target_size or (1240, 1860), preserve_color=image_processing.preserve_color)
        return PreviewPage(
            page_number=None,
            label=_tr("ui.blank", language),
            source_image=source_image,
            processed_image=processed_image,
            crop_box=None,
            page_position=page_position,
            is_blank=True,
        )

    processed, source, crop_box = processed_cache[image_index]
    return PreviewPage(
        page_number=image_index + 1,
        label=_tr("ui.page", language, page=image_index + 1),
        source_image=source,
        processed_image=processed,
        crop_box=crop_box,
        page_position=page_position,
        is_blank=False,
    )


def _build_processed_cache(
    image_paths: list[Path],
    preview_group: tuple[LayoutPageSlot, ...],
    image_processing: ImageProcessingOptions,
    layout_options: LayoutOptions,
) -> dict[int, tuple[object, object, tuple[int, int, int, int] | None]]:
    cache: dict[int, tuple[object, object, tuple[int, int, int, int] | None]] = {}
    source_slots = [slot for slot in preview_group if slot.source_index is not None]
    if not source_slots:
        return cache

    if layout_options.page_layout != "facing" or not is_spread_crop_mode(image_processing.crop_mode):
        for slot in source_slots:
            index = slot.source_index
            source = load_source_image(image_paths[index])
            processed, crop_box = _process_basic_page(source, image_processing)
            cache[index] = (processed, source, crop_box)
        return cache

    if len(source_slots) == 1:
        slot = source_slots[0]
        source = load_source_image(image_paths[slot.source_index])
        processed, crop_box = _process_facing_single_page(
            source,
            image_processing,
            template_direction=layout_options.reading_direction,
            page_position=slot.page_position,
        )
        cache[slot.source_index] = (processed, source, crop_box)
        return cache

    sources = [load_source_image(image_paths[slot.source_index]) for slot in source_slots]
    processed_entries = _process_spread_group(
        sources,
        image_processing,
        template_direction=layout_options.reading_direction,
    )
    for slot, source, (processed, crop_box) in zip(source_slots, sources, processed_entries):
        cache[slot.source_index] = (processed, source, crop_box)
    return cache


def _process_basic_page(source_image, image_processing: ImageProcessingOptions) -> tuple[object, tuple[int, int, int, int] | None]:
    working = source_image.copy()
    crop_box = None
    if image_processing.crop_mode == "smart":
        crop_box = build_smart_crop_box(working, target_size=image_processing.target_size)
        working = apply_crop_box(working, crop_box)
    processed = _finalize_processed_image(working, image_processing)
    return processed, crop_box


def _process_facing_single_page(
    source_image,
    image_processing: ImageProcessingOptions,
    template_direction: str,
    page_position: str,
) -> tuple[object, tuple[int, int, int, int] | None]:
    crop_box = build_kcc_crop_box(source_image)
    crop_box = build_outer_only_crop_box(
        source_image.size,
        crop_box,
        page_position=page_position,
        template_direction=template_direction,
    )
    processed = _finalize_processed_image(apply_crop_box(source_image.copy(), crop_box), image_processing)
    return processed, crop_box


def _process_spread_group(
    sources: list[object],
    image_processing: ImageProcessingOptions,
    template_direction: str,
) -> list[tuple[object, tuple[int, int, int, int] | None]]:
    crop_boxes = [build_kcc_crop_box(image) for image in sources]
    synchronized_boxes = list(
        synchronize_facing_crop_boxes(
            sources[0].size,
            crop_boxes[0],
            sources[1].size,
            crop_boxes[1],
            template_direction,
        )
    )
    if image_processing.crop_mode == "kcc-spread-fill":
        synchronized_boxes = list(
            maybe_add_inner_white_trim(
                sources[0],
                synchronized_boxes[0],
                sources[1],
                synchronized_boxes[1],
                template_direction=template_direction,
                target_size=image_processing.target_size,
            )
        )

    results: list[tuple[object, tuple[int, int, int, int] | None]] = []
    for source, crop_box in zip(sources, synchronized_boxes):
        results.append(
            (
                _finalize_processed_image(apply_crop_box(source.copy(), crop_box), image_processing),
                crop_box,
            )
        )
    return results


def _finalize_processed_image(image, image_processing: ImageProcessingOptions):
    processed = apply_luminance_operations(image, image_processing)
    if image_processing.target_size is not None:
        processed = fit_image_to_canvas(processed, image_processing.target_size, image_processing.preserve_color)
    if not image_processing.preserve_color:
        processed = processed.convert("L")
    return processed


def _blank_page_image(size: tuple[int, int], preserve_color: bool):
    Image, _, _ = load_pillow()
    mode = "RGB" if preserve_color else "L"
    background = (255, 255, 255) if preserve_color else 255
    return Image.new(mode, size, background)


def _visual_order(pages: list[PreviewPage], layout_options: LayoutOptions) -> list[PreviewPage]:
    if len(pages) <= 1:
        return pages
    if layout_options.reading_direction == "rtl":
        return [pages[1], pages[0]]
    return pages


def _render_preview_image(pages: list[PreviewPage], show_crop_boxes: bool):
    Image, ImageOps, _ = load_pillow()
    from PIL import ImageDraw

    page_count = max(1, len(pages))
    image_kind = "source" if show_crop_boxes else "processed"
    spread_width, spread_height = _spread_image_size(pages, image_kind=image_kind)
    max_content_width = PREVIEW_MAX_SPREAD_WIDTH if page_count == 2 else PREVIEW_MAX_SINGLE_WIDTH
    content_width = max(1, min(spread_width, max_content_width))
    content_height = max(1, round(content_width / max(spread_width / max(1, spread_height), 0.01)))
    inset = 6
    margin = 6
    row_width = content_width + inset * 2
    row_height = content_height + inset * 2
    canvas_width = row_width + margin * 2
    canvas_height = margin * 2 + row_height

    canvas = Image.new("RGB", (canvas_width, canvas_height), PREVIEW_BG)
    output_row = _render_row(
        pages,
        row_width,
        row_height,
        image_kind=image_kind,
        show_crop_boxes=show_crop_boxes,
        image_ops=ImageOps,
        inset=inset,
    )
    canvas.paste(output_row, (margin, margin))

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, canvas_width - 1, canvas_height - 1), radius=24, outline="#ececf0", width=2)
    return canvas


def _render_row(
    pages: list[PreviewPage],
    row_width: int,
    row_height: int,
    image_kind: str,
    show_crop_boxes: bool,
    image_ops,
    inset: int = 12,
):
    Image, _, _ = load_pillow()
    from PIL import ImageDraw

    row = Image.new("RGB", (row_width, row_height), PREVIEW_BG)
    draw = ImageDraw.Draw(row)
    spread_image, crop_rects = _compose_spread_image(pages, image_kind=image_kind)
    if spread_image.mode != "RGB":
        spread_image = spread_image.convert("RGB")

    frame_box = (0, 0, row_width - 1, row_height - 1)
    draw.rounded_rectangle(frame_box, radius=18, fill=PREVIEW_PANEL, outline=PREVIEW_BORDER, width=2)
    target_size = (row_width - inset * 2, row_height - inset * 2)
    if spread_image.width <= target_size[0] and spread_image.height <= target_size[1]:
        fitted = spread_image
    else:
        fitted = image_ops.contain(spread_image, target_size, Image.Resampling.LANCZOS)
    offset_x = (row_width - fitted.width) // 2
    offset_y = (row_height - fitted.height) // 2
    row.paste(fitted, (offset_x, offset_y))

    if show_crop_boxes:
        scale_x = fitted.width / spread_image.width
        scale_y = fitted.height / spread_image.height
        for crop_rect in crop_rects:
            left, top, right, bottom = crop_rect
            scaled_crop_rect = (
                offset_x + left * scale_x,
                offset_y + top * scale_y,
                offset_x + right * scale_x,
                offset_y + bottom * scale_y,
            )
            line_width = max(5, round(min(row_width, row_height) / 260))
            draw.rounded_rectangle(scaled_crop_rect, radius=8, outline=PREVIEW_CROP, width=line_width)
    return row


def _spread_image_size(
    pages: list[PreviewPage],
    image_kind: str,
) -> tuple[int, int]:
    images = [page.processed_image if image_kind == "processed" else page.source_image for page in pages]
    if not images:
        return (1, 1)
    return (max(1, sum(image.width for image in images)), max(1, max(image.height for image in images)))


def _compose_spread_image(
    pages: list[PreviewPage],
    image_kind: str,
) -> tuple[object, list[tuple[int, int, int, int]]]:
    Image, _, _ = load_pillow()
    images = []
    for page in pages:
        image = page.processed_image if image_kind == "processed" else page.source_image
        images.append(image.convert("RGB") if image.mode != "RGB" else image)

    if not images:
        return Image.new("RGB", (1, 1), "white"), []

    max_height = max(image.height for image in images)
    total_width = sum(image.width for image in images)
    spread = Image.new("RGB", (total_width, max_height), "white")
    crop_rects: list[tuple[int, int, int, int]] = []
    cursor_x = 0

    for page, image in zip(pages, images):
        offset_y = (max_height - image.height) // 2
        spread.paste(image, (cursor_x, offset_y))
        if image_kind == "source" and not page.is_blank:
            left, top, right, bottom = page.crop_box or (0, 0, image.width, image.height)
            left = max(0, min(left, image.width))
            top = max(0, min(top, image.height))
            right = max(left, min(right, image.width))
            bottom = max(top, min(bottom, image.height))
            crop_rects.append(
                (
                    cursor_x + left,
                    offset_y + top,
                    cursor_x + right,
                    offset_y + bottom,
                )
            )
        cursor_x += image.width

    return spread, crop_rects
