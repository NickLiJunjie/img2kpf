from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from pathlib import Path
import shutil
from threading import Event, Lock
from typing import Callable

from .kpf_generator import find_input_images, load_pillow


DEFAULT_SPREAD_MIN_RATIO = 1.2
DEFAULT_JPEG_QUALITY = 95


@dataclass(frozen=True)
class SpreadSplitScan:
    input_dir: Path
    image_count: int
    spread_count: int
    volume_count: int = 1

    @property
    def has_spreads(self) -> bool:
        return self.spread_count > 0


@dataclass(frozen=True)
class SpreadSplitResult:
    input_dir: Path
    output_dir: Path
    image_count: int
    split_image_count: int
    copied_image_count: int
    blank_page_count: int
    output_image_count: int
    volume_count: int = 1


@dataclass(frozen=True)
class _VolumeSplitResult:
    source_dir: Path
    image_count: int
    split_image_count: int
    copied_image_count: int
    blank_page_count: int
    output_image_count: int


def is_spread_size(width: int, height: int, min_ratio: float = DEFAULT_SPREAD_MIN_RATIO) -> bool:
    if width <= 0 or height <= 0:
        return False
    return width > height and (width / height) >= min_ratio


def scan_spread_folder(input_dir: Path, min_ratio: float = DEFAULT_SPREAD_MIN_RATIO) -> SpreadSplitScan:
    return scan_spread_sources(input_dir, (input_dir,), min_ratio=min_ratio)


def scan_spread_sources(
    input_dir: Path,
    source_dirs: tuple[Path, ...],
    min_ratio: float = DEFAULT_SPREAD_MIN_RATIO,
) -> SpreadSplitScan:
    Image, _, _ = load_pillow()
    image_count = 0
    spread_count = 0
    for source_dir in source_dirs:
        image_paths = find_input_images(source_dir)
        image_count += len(image_paths)
        for image_path in image_paths:
            with Image.open(image_path) as image:
                if is_spread_size(image.width, image.height, min_ratio=min_ratio):
                    spread_count += 1
    return SpreadSplitScan(
        input_dir=input_dir,
        image_count=image_count,
        spread_count=spread_count,
        volume_count=len(source_dirs),
    )


def suggest_split_output_dir(input_dir: Path) -> Path:
    base = input_dir.with_name(f"{input_dir.name}_split")
    if not base.exists():
        return base
    for index in range(2, 1000):
        candidate = input_dir.with_name(f"{input_dir.name}_split_{index}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"无法找到可用的拆分页输出目录：{base.parent}")


def split_spread_folder(
    input_dir: Path,
    output_dir: Path | None = None,
    *,
    reading_direction: str = "rtl",
    min_ratio: float = DEFAULT_SPREAD_MIN_RATIO,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    progress_callback: Callable[[int, int, Path], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> SpreadSplitResult:
    return split_spread_sources(
        input_dir,
        (input_dir,),
        output_dir,
        reading_direction=reading_direction,
        min_ratio=min_ratio,
        jpeg_quality=jpeg_quality,
        progress_callback=progress_callback,
        stop_requested=stop_requested,
    )


def split_spread_sources(
    input_dir: Path,
    source_dirs: tuple[Path, ...],
    output_dir: Path | None = None,
    *,
    reading_direction: str = "rtl",
    min_ratio: float = DEFAULT_SPREAD_MIN_RATIO,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    align_facing_pairs: bool = True,
    shift_first_page: bool = False,
    jobs: int = 1,
    progress_callback: Callable[[int, int, Path], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> SpreadSplitResult:
    input_dir = input_dir.expanduser()
    if not input_dir.is_dir():
        raise NotADirectoryError(f"输入目录不可用：{input_dir}")
    source_dirs = tuple(source_dir.expanduser() for source_dir in source_dirs)
    if not source_dirs:
        raise ValueError("输入目录中没有找到可拆分图片。")
    for source_dir in source_dirs:
        if not source_dir.is_dir():
            raise NotADirectoryError(f"输入目录不可用：{source_dir}")

    output_dir = (output_dir or suggest_split_output_dir(input_dir)).expanduser()
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)

    source_image_paths = tuple((source_dir, find_input_images(source_dir)) for source_dir in source_dirs)
    total_images = sum(len(image_paths) for _, image_paths in source_image_paths)
    if total_images <= 0:
        raise ValueError("输入目录中没有找到可拆分图片。")

    Image, _, _ = load_pillow()
    external_stop_requested = stop_requested or (lambda: False)
    local_stop = Event()

    def should_stop() -> bool:
        return local_stop.is_set() or external_stop_requested()

    progress_lock = Lock()
    processed = 0

    def report_image_done(image_path: Path) -> None:
        nonlocal processed
        if progress_callback is None:
            return
        with progress_lock:
            processed += 1
            current = processed
        progress_callback(current, total_images, image_path)

    try:
        worker_count = max(1, min(int(jobs), len(source_image_paths)))
        if worker_count == 1:
            volume_results = [
                _split_one_source(
                    input_dir=input_dir,
                    source_dir=source_dir,
                    image_paths=image_paths,
                    output_dir=output_dir,
                    preserve_source_dir=len(source_dirs) > 1,
                    reading_direction=reading_direction,
                    min_ratio=min_ratio,
                    jpeg_quality=jpeg_quality,
                    align_facing_pairs=align_facing_pairs,
                    shift_first_page=shift_first_page,
                    Image=Image,
                    progress_callback=report_image_done,
                    stop_requested=should_stop,
                )
                for source_dir, image_paths in source_image_paths
            ]
        else:
            volume_results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [
                    executor.submit(
                        _split_one_source,
                        input_dir=input_dir,
                        source_dir=source_dir,
                        image_paths=image_paths,
                        output_dir=output_dir,
                        preserve_source_dir=True,
                        reading_direction=reading_direction,
                        min_ratio=min_ratio,
                        jpeg_quality=jpeg_quality,
                        align_facing_pairs=align_facing_pairs,
                        shift_first_page=shift_first_page,
                        Image=Image,
                        progress_callback=report_image_done,
                        stop_requested=should_stop,
                    )
                    for source_dir, image_paths in source_image_paths
                ]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        volume_results.append(future.result())
                    except Exception:
                        local_stop.set()
                        raise
    except Exception:
        local_stop.set()
        shutil.rmtree(output_dir, ignore_errors=True)
        raise

    return SpreadSplitResult(
        input_dir=input_dir,
        output_dir=output_dir,
        image_count=total_images,
        split_image_count=sum(result.split_image_count for result in volume_results),
        copied_image_count=sum(result.copied_image_count for result in volume_results),
        blank_page_count=sum(result.blank_page_count for result in volume_results),
        output_image_count=sum(result.output_image_count for result in volume_results),
        volume_count=len(source_dirs),
    )


def _destination_dir_for_source(
    input_dir: Path,
    source_dir: Path,
    output_dir: Path,
    preserve_source_dir: bool,
) -> Path:
    if not preserve_source_dir:
        return output_dir
    try:
        relative = source_dir.relative_to(input_dir)
    except ValueError:
        relative = Path(source_dir.name)
    return output_dir / relative


def _split_one_source(
    *,
    input_dir: Path,
    source_dir: Path,
    image_paths: list[Path],
    output_dir: Path,
    preserve_source_dir: bool,
    reading_direction: str,
    min_ratio: float,
    jpeg_quality: int,
    align_facing_pairs: bool,
    shift_first_page: bool,
    Image,
    progress_callback: Callable[[Path], None],
    stop_requested: Callable[[], bool],
) -> _VolumeSplitResult:
    destination_dir = _destination_dir_for_source(input_dir, source_dir, output_dir, preserve_source_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    split_count = 0
    copied_count = 0
    blank_count = 0
    output_count = 0

    for image_path in image_paths:
        if stop_requested():
            raise RuntimeError("ui.spread.split.cancelled")
        with Image.open(image_path) as image:
            should_split = is_spread_size(image.width, image.height, min_ratio=min_ratio)
            if should_split:
                split_x = image.width // 2
                left = image.crop((0, 0, split_x, image.height))
                right = image.crop((split_x, 0, image.width, image.height))
                halves = (right, left) if reading_direction == "rtl" else (left, right)
                if align_facing_pairs and _needs_facing_alignment_blank(output_count, shift_first_page):
                    output_count += 1
                    blank_count += 1
                    _save_blank_page(halves[0].size, destination_dir / f"{output_count:06d}.jpg", Image, jpeg_quality)
                for half in halves:
                    output_count += 1
                    _save_jpeg(
                        half,
                        destination_dir / f"{output_count:06d}.jpg",
                        Image,
                        jpeg_quality,
                    )
                split_count += 1
            else:
                output_count += 1
                destination = destination_dir / f"{output_count:06d}{image_path.suffix.lower()}"
                shutil.copy2(image_path, destination)
                copied_count += 1
        progress_callback(image_path)

    return _VolumeSplitResult(
        source_dir=source_dir,
        image_count=len(image_paths),
        split_image_count=split_count,
        copied_image_count=copied_count,
        blank_page_count=blank_count,
        output_image_count=output_count,
    )


def _needs_facing_alignment_blank(output_count: int, shift_first_page: bool) -> bool:
    shift_blank_count = 1 if shift_first_page else 0
    return (output_count + shift_blank_count) % 2 != 0


def _save_blank_page(size: tuple[int, int], output_path: Path, Image, quality: int) -> None:
    width, height = size
    blank = Image.new("RGB", (max(1, width), max(1, height)), "white")
    _save_jpeg(blank, output_path, Image, quality)


def _save_jpeg(image, output_path: Path, Image, quality: int) -> None:
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    image.save(
        output_path,
        format="JPEG",
        quality=max(1, min(100, int(quality))),
        optimize=True,
        subsampling=0,
    )
