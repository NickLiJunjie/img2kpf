from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import io
import re
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Callable, Literal

from .i18n import encode_i18n_message
from .kfx_direct import convert_kpf_to_kfx
from .kpf_generator import (
    BuildCancelled,
    BuildResult,
    BuildProgressCallback,
    BuildStageProgress,
    LayoutOptions,
    build_kpf,
    build_parser,
    find_batch_directories,
    find_input_images,
    load_bundled_template_assets,
    load_template_assets,
    normalize_crop_mode,
    normalize_performance_mode,
    normalize_image_preset,
    parse_size,
    resolve_image_processing_options,
    resolve_parallel_jobs,
)
from .plugin_registry import DEFAULT_KFX_PLUGIN_ID, resolve_plugin_archive


InputMode = Literal["single", "batch", "invalid", "empty"]
TriStateValue = Literal["auto", "enabled", "disabled"]

IGNORED_SUBDIR_NAMES = {"_kpf_output"}
PRESET_DEFAULTS = {
    "none": {
        "gamma": 1.0,
        "contrast": 1.0,
        "jpeg_quality": 90,
    },
    "standard": {
        "gamma": 1.0,
        "contrast": 1.0,
        "jpeg_quality": 90,
    },
    "bright": {
        "gamma": 1.8,
        "contrast": 1.0,
        "jpeg_quality": 90,
    },
}


def _msg(key: str, **kwargs: object) -> str:
    return encode_i18n_message(key, **kwargs)


@dataclass(frozen=True)
class DetectionResult:
    mode: InputMode
    input_dir: Path
    root_images: tuple[Path, ...]
    image_subdirs: tuple[Path, ...]
    candidate_subdirs: tuple[Path, ...]
    message: str

    @property
    def is_runnable(self) -> bool:
        return self.mode in {"single", "batch"}


@dataclass(frozen=True)
class CliParameterInfo:
    dest: str
    option: str
    tooltip: str
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunProgress:
    mode: str
    phase: str
    current: int
    total: int
    current_name: str = ""
    successes: int = 0
    failures: int = 0
    detail_current: int = 0
    detail_total: int = 0
    detail_text: str = ""
    workers: int = 1
    indeterminate: bool = False


@dataclass(frozen=True)
class VolumeFailure:
    volume_dir: Path
    reason: str


@dataclass
class AppRunConfig:
    input_dir: str
    output_location: str
    template_path: str = ""
    title: str = ""
    custom_title_enabled: bool = False
    volume_title_template: str = " 第 {volume} 卷"
    shift: bool = False
    reading_direction: str = "rtl"
    page_layout: str = "facing"
    virtual_panels: bool = True
    panel_movement: str = "vertical"
    image_preset: str = "bright"
    crop_mode: str = "off"
    crop_edge_threshold: float = 1.00
    spread_fill_edge_threshold: float = 1.00
    spread_fill_inner_enabled: bool = False
    spread_fill_inner_edge_threshold: float = 1.00
    target_size_text: str = ""
    scribe_panel: bool = True
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
    output_format: str = "kpf"
    kfx_plugin: str = DEFAULT_KFX_PLUGIN_ID
    jobs: int = 5
    performance_mode: str = "balanced"


@dataclass(frozen=True)
class RunSummary:
    mode: InputMode
    output_location: Path
    successes: tuple[BuildResult, ...]
    failures: tuple[VolumeFailure, ...]
    stopped: bool = False


def _tooltip_key_map() -> dict[str, str]:
    return {
        "title": "ui.tip.title",
        "shift": "ui.tip.shift",
        "image_preset": "ui.tip.image.preset",
        "crop_mode": "ui.tip.crop.mode",
        "target_size": "ui.tip.target.size",
        "preserve_color": "ui.tip.preserve.color",
        "gamma": "ui.tip.gamma",
        "contrast": "ui.tip.contrast",
        "autocontrast": "ui.tip.autocontrast",
        "autolevel": "ui.tip.autolevel",
        "jpeg_quality": "ui.tip.jpeg.quality",
        "template": "ui.tip.template",
        "kfx_plugin": "ui.tip.kfx.plugin",
        "jobs": "ui.tip.jobs",
        "performance_mode": "ui.tip.performance.mode",
        "reading_direction": "ui.tip.reading.direction",
        "page_layout": "ui.tip.page.layout",
        "virtual_panels": "ui.tip.virtual.panels",
        "panel_movement": "ui.tip.panel.movement",
    }


def get_cli_parameter_info() -> dict[str, CliParameterInfo]:
    parser = build_parser()
    tooltip_keys = _tooltip_key_map()
    parameter_info: dict[str, CliParameterInfo] = {}
    for action in parser._actions:
        if not action.option_strings:
            continue
        long_options = [option for option in action.option_strings if option.startswith("--")]
        if not long_options:
            continue
        option = max(long_options, key=len)
        tooltip = tooltip_keys.get(action.dest, action.help or "")
        parameter_info[action.dest] = CliParameterInfo(
            dest=action.dest,
            option=option,
            tooltip=tooltip,
            choices=tuple(str(choice) for choice in action.choices or ()),
        )
    return parameter_info


CLI_PARAMETER_INFO = get_cli_parameter_info()


def preset_default_gamma(image_preset: str) -> float:
    normalized = normalize_image_preset(image_preset)
    return PRESET_DEFAULTS.get(normalized, PRESET_DEFAULTS["bright"])["gamma"]


def preset_default_contrast(image_preset: str) -> float:
    normalized = normalize_image_preset(image_preset)
    return PRESET_DEFAULTS.get(normalized, PRESET_DEFAULTS["bright"])["contrast"]


def preset_default_jpeg_quality(image_preset: str) -> int:
    normalized = normalize_image_preset(image_preset)
    return PRESET_DEFAULTS.get(normalized, PRESET_DEFAULTS["bright"])["jpeg_quality"]


def tristate_to_bool(value: TriStateValue) -> bool | None:
    if value == "enabled":
        return True
    if value == "disabled":
        return False
    return None


def normalize_output_format(config: AppRunConfig) -> str:
    output_format = config.output_format.strip()
    if output_format:
        return output_format
    return "kpf_kfx" if config.emit_kfx else "kpf"


def output_directory_suffix(output_format: str) -> str:
    suffix_map = {
        "kpf": "kpf",
        "kpf_kfx": "kpf_kfx",
        "kfx_only": "kfx",
        "epub": "epub",
        "mobi": "mobi",
    }
    return suffix_map.get(output_format, "output")


def primary_output_suffix(output_format: str) -> str:
    return ".kfx" if output_format == "kfx_only" else ".kpf"


def should_emit_kfx(config: AppRunConfig) -> bool:
    return normalize_output_format(config) in {"kpf_kfx", "kfx_only"}


def should_keep_kpf(config: AppRunConfig) -> bool:
    return normalize_output_format(config) != "kfx_only"


def suggest_output_location(
    input_dir: Path,
    mode: InputMode,
    output_format: str = "kpf",
    output_name: str | None = None,
) -> Path | None:
    return _suggest_output_location(input_dir, mode, output_format, output_name or input_dir.name)


def _suggest_output_location(
    input_dir: Path,
    mode: InputMode,
    output_format: str = "kpf",
    output_name: str | None = None,
) -> Path | None:
    base_name = sanitize_output_filename(output_name or input_dir.name, fallback=input_dir.name)
    output_dir = input_dir / f"{base_name}_{output_directory_suffix(output_format)}"
    if mode == "single":
        return output_dir / f"{base_name}{primary_output_suffix(output_format)}"
    if mode == "batch":
        return output_dir
    return None


def sanitize_output_filename(value: str, fallback: str = "output") -> str:
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.rstrip(" .")
    fallback_normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", fallback).strip().rstrip(" .")
    return normalized or fallback_normalized or "output"


def detect_input_mode(
    input_dir: Path,
    extra_ignored_paths: set[Path] | None = None,
) -> DetectionResult:
    if not input_dir.exists():
        raise FileNotFoundError(_msg("ui.error.input.directory.not_found", path=input_dir))
    if not input_dir.is_dir():
        raise NotADirectoryError(_msg("ui.error.input.path.not_directory", path=input_dir))

    ignored_paths = {path.resolve() for path in extra_ignored_paths or set() if path.exists()}
    root_images = tuple(find_input_images(input_dir))

    candidate_subdirs: list[Path] = []
    image_subdirs: list[Path] = []
    for path in sorted(input_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir():
            continue
        if path.name.startswith(".") or path.name in IGNORED_SUBDIR_NAMES:
            continue
        if path.resolve() in ignored_paths:
            continue
        candidate_subdirs.append(path)
        if find_input_images(path):
            image_subdirs.append(path)

    if root_images and image_subdirs:
        message = _msg(
            "ui.cannot.infer.automatically.root.has.images.these",
            names=_format_path_list(image_subdirs),
        )
        return DetectionResult(
            mode="invalid",
            input_dir=input_dir,
            root_images=root_images,
            image_subdirs=tuple(image_subdirs),
            candidate_subdirs=tuple(candidate_subdirs),
            message=message,
        )

    if root_images:
        message = _msg(
            "ui.single.detected.root.contains.images",
            count=len(root_images),
        )
        return DetectionResult(
            mode="single",
            input_dir=input_dir,
            root_images=root_images,
            image_subdirs=tuple(),
            candidate_subdirs=tuple(candidate_subdirs),
            message=message,
        )

    if image_subdirs:
        message = _msg(
            "ui.batch.detected.root.has.no.direct.images",
            names=_format_path_list(image_subdirs),
        )
        return DetectionResult(
            mode="batch",
            input_dir=input_dir,
            root_images=tuple(),
            image_subdirs=tuple(image_subdirs),
            candidate_subdirs=tuple(candidate_subdirs),
            message=message,
        )

    message = _msg("ui.no.processable.content.found.no.images.root")
    return DetectionResult(
        mode="empty",
        input_dir=input_dir,
        root_images=tuple(),
        image_subdirs=tuple(),
        candidate_subdirs=tuple(candidate_subdirs),
        message=message,
    )


def build_image_processing_options(config: AppRunConfig):
    target_size = None
    if config.target_size_text.strip():
        target_size = parse_size(config.target_size_text.strip())

    namespace = argparse.Namespace(
        image_preset=normalize_image_preset(config.image_preset),
        crop_mode=normalize_crop_mode(config.crop_mode),
        crop_edge_threshold=config.crop_edge_threshold,
        spread_fill_edge_threshold=config.spread_fill_edge_threshold,
        spread_fill_inner_enabled=config.spread_fill_inner_enabled,
        spread_fill_inner_edge_threshold=config.spread_fill_inner_edge_threshold,
        target_size=target_size,
        scribe_panel=config.scribe_panel,
        preserve_color=tristate_to_bool(config.preserve_color),
        gamma=None if config.gamma_auto else config.gamma_value,
        contrast=None if config.contrast_auto else config.contrast_value,
        autocontrast=tristate_to_bool(config.autocontrast),
        autolevel=tristate_to_bool(config.autolevel),
        jpeg_quality=None if config.jpeg_quality_auto else config.jpeg_quality_value,
        performance_mode=normalize_performance_mode(config.performance_mode),
    )
    return resolve_image_processing_options(namespace)


def build_layout_options(config: AppRunConfig) -> LayoutOptions:
    return LayoutOptions(
        reading_direction=config.reading_direction,
        page_layout=config.page_layout,
        virtual_panels=config.virtual_panels,
        panel_movement=config.panel_movement,
    )


def _build_stage_fraction(stage: BuildStageProgress) -> float:
    phase = stage.phase
    stage_progress = 1.0
    if stage.total > 0:
        stage_progress = max(0.0, min(1.0, stage.current / stage.total))
    if phase == "ui.progress.collect.images":
        return 0.02 * stage_progress
    if phase == "ui.progress.preprocess.images":
        return 0.02 + 0.50 * stage_progress
    if phase == "ui.progress.inspect.images":
        return 0.52 + 0.05 * stage_progress
    if phase == "ui.progress.build.layout":
        return 0.57 + 0.08 * stage_progress
    if phase == "ui.progress.write.kpf":
        return 0.65 + 0.35 * stage_progress
    return stage_progress


def _build_detail_text(stage: BuildStageProgress) -> str:
    if stage.workers > 1 and stage.phase == "ui.progress.preprocess.images":
        return _msg("ui.progress.worker.count", workers=stage.workers)
    return ""


def validate_run_config(config: AppRunConfig) -> DetectionResult:
    if not config.input_dir.strip():
        raise ValueError(_msg("ui.please.choose.input.folder"))

    input_dir = Path(config.input_dir).expanduser()
    ignored_paths = _build_detection_ignored_paths(config.output_location)
    detection = detect_input_mode(input_dir, extra_ignored_paths=ignored_paths)

    if detection.mode == "invalid":
        raise ValueError(
            _msg(
                "ui.cannot.infer.automatically.root.has.images.these",
                names=_format_path_list(detection.image_subdirs),
            )
        )
    if detection.mode == "empty":
        raise ValueError(_msg("ui.no.processable.content.found.no.images.root"))

    if config.template_path.strip():
        template_path = Path(config.template_path).expanduser()
        if not template_path.is_file():
            raise ValueError(_msg("ui.error.template.file.not_found", path=template_path))

    output_format = normalize_output_format(config)
    if output_format in {"epub", "mobi"}:
        raise ValueError(_msg("ui.epub.mobi.generation.not.available.please.choose"))

    if should_emit_kfx(config) and not config.kfx_plugin.strip():
        try:
            resolve_plugin_archive(None)
        except Exception:
            raise ValueError(_msg("ui.error.kfx.plugin.required")) from None
    if config.jobs < 1:
        raise ValueError(_msg("ui.error.jobs.must.be.positive"))
    if not config.gamma_auto and config.gamma_value <= 0:
        raise ValueError(_msg("ui.error.gamma.must.be.positive"))
    if not config.contrast_auto and config.contrast_value <= 0:
        raise ValueError(_msg("ui.error.contrast.must.be.positive"))
    if not config.jpeg_quality_auto and not 1 <= config.jpeg_quality_value <= 100:
        raise ValueError(_msg("ui.error.jpeg.quality.out.of.range"))
    if config.page_layout not in {"facing", "single"}:
        raise ValueError(_msg("ui.error.page.layout.invalid"))
    if config.reading_direction not in {"rtl", "ltr"}:
        raise ValueError(_msg("ui.error.reading.direction.invalid"))
    if config.panel_movement not in {"vertical", "horizontal"}:
        raise ValueError(_msg("ui.error.panel.movement.invalid"))
    if config.shift and config.page_layout != "facing":
        raise ValueError(_msg("ui.single.layout.does.not.support.first.shift"))

    output_location = resolve_output_location(config, detection.mode)
    if detection.mode == "single":
        expected_suffix = primary_output_suffix(output_format)
        if output_location.suffix.lower() != expected_suffix:
            raise ValueError(
                _msg(
                    "ui.error.single.output.must.match_suffix",
                    suffix=expected_suffix,
                )
            )
    if detection.mode == "batch" and output_location.suffix.lower() in {".kpf", ".kfx"}:
        raise ValueError(_msg("ui.error.batch.output.must.be.directory"))

    build_image_processing_options(config)
    return detection


def resolve_output_location(config: AppRunConfig, mode: InputMode) -> Path:
    input_dir = Path(config.input_dir).expanduser()
    if config.output_location.strip():
        return Path(config.output_location).expanduser()

    suggested = _suggest_output_location(
        input_dir,
        mode,
        normalize_output_format(config),
        _default_output_base_name(config, input_dir),
    )
    if suggested is None:
        raise ValueError(_msg("ui.error.output.location.unavailable"))
    return suggested


def execute_run(
    config: AppRunConfig,
    log_callback: Callable[[str], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[RunProgress], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    pause_requested: Callable[[], None] | None = None,
) -> RunSummary:
    detection = validate_run_config(config)
    input_dir = Path(config.input_dir).expanduser()
    output_location = resolve_output_location(config, detection.mode)
    image_processing = build_image_processing_options(config)
    layout_options = build_layout_options(config)
    template_assets = (
        load_template_assets(Path(config.template_path).expanduser())
        if config.template_path.strip()
        else load_bundled_template_assets()
    )

    log = log_callback or (lambda message: None)
    status = status_callback or (lambda message: None)
    progress = progress_callback or (lambda value: None)
    should_stop = stop_requested or (lambda: False)
    pause_if_requested = pause_requested or (lambda: None)

    log(_msg("ui.log.input.directory", path=input_dir))
    log(_msg("ui.log.detection.result", detail=detection.message))
    if config.template_path.strip():
        log(_msg("ui.log.template.file", path=Path(config.template_path).expanduser()))
    else:
        log(_msg("ui.template.using.built.rtl.facing.static.assets"))
    panel_label = (
        _msg("ui.log.virtual.panels.enabled", movement=config.panel_movement)
        if config.virtual_panels
        else _msg("ui.option.disabled")
    )
    log(
        _msg(
            "ui.log.layout.config",
            reading_direction=config.reading_direction,
            page_layout=config.page_layout,
            panel=panel_label,
        )
    )

    if detection.mode == "single":
        return _execute_single_run(
            config=config,
            input_dir=input_dir,
            output_path=output_location,
            template_assets=template_assets,
            image_processing=image_processing,
            layout_options=layout_options,
            log=log,
            status=status,
            progress=progress,
            should_stop=should_stop,
            pause_if_requested=pause_if_requested,
        )

    return _execute_batch_run(
        config=config,
        batch_dir=input_dir,
        output_dir=output_location,
        template_assets=template_assets,
        image_processing=image_processing,
        layout_options=layout_options,
        log=log,
        status=status,
        progress=progress,
        should_stop=should_stop,
        pause_if_requested=pause_if_requested,
    )


def _execute_single_run(
    config: AppRunConfig,
    input_dir: Path,
    output_path: Path,
    template_assets,
    image_processing,
    layout_options: LayoutOptions,
    log: Callable[[str], None],
    status: Callable[[str], None],
    progress: Callable[[RunProgress], None],
    should_stop: Callable[[], bool],
    pause_if_requested: Callable[[], None],
) -> RunSummary:
    emit_kfx = should_emit_kfx(config)
    keep_kpf = should_keep_kpf(config)
    build_output_path = output_path if keep_kpf else output_path.with_suffix(".kpf")
    total_units = 1000
    kpf_units = 780 if emit_kfx else total_units
    last_progress_units = 0

    def report_build_progress(stage: BuildStageProgress) -> None:
        nonlocal last_progress_units
        fraction = _build_stage_fraction(stage)
        last_progress_units = round(kpf_units * fraction)
        progress(
            RunProgress(
                mode="single",
                phase=_msg(stage.phase),
                current=last_progress_units,
                total=total_units,
                current_name=stage.current_name or input_dir.name,
                detail_current=stage.current,
                detail_total=stage.total,
                detail_text=_build_detail_text(stage),
                workers=stage.workers,
                indeterminate=stage.indeterminate,
            )
        )

    try:
        pause_if_requested()
        if should_stop():
            raise BuildCancelled("ui.task.cancelled")
        status(_msg("ui.generating.single.volume.kpf" if keep_kpf else "ui.generating.temporary.single.volume.kpf"))
        progress(
            RunProgress(
                mode="single",
                phase=_msg("ui.generating.kpf" if keep_kpf else "ui.generating.temporary.kpf"),
                current=0,
                total=total_units,
                current_name=input_dir.name,
            )
        )
        result = _capture_console_output(
            log,
            build_kpf,
            template_assets=template_assets,
            input_dir=input_dir,
            output_path=build_output_path,
            title=config.title.strip() if config.custom_title_enabled and config.title.strip() else None,
            image_processing=image_processing,
            shift_first_page=config.shift,
            layout_options=layout_options,
            progress_callback=report_build_progress,
            stop_requested=should_stop,
        )

        if emit_kfx:
            pause_if_requested()
            if should_stop():
                raise BuildCancelled("ui.task.cancelled")
            status(_msg("ui.generating.kfx"))
            progress(
                RunProgress(
                    mode="single",
                    phase=_msg("ui.generating.kfx"),
                    current=kpf_units,
                    total=total_units,
                    current_name=input_dir.name,
                    detail_text=_msg("ui.progress.kfx.plugin.running"),
                    indeterminate=True,
                )
            )
            kfx_result = _capture_console_output(
                log,
                convert_kpf_to_kfx,
                result.output_path,
                output_path if not keep_kpf else None,
                plugin_ref=config.kfx_plugin.strip() or None,
            )
            if keep_kpf:
                result.kfx_output_path = kfx_result.kfx_path
            else:
                try:
                    build_output_path.unlink(missing_ok=True)
                except TypeError:
                    if build_output_path.exists():
                        build_output_path.unlink()
                result.output_path = kfx_result.kfx_path
                result.kfx_output_path = None
    except BuildCancelled:
        status(_msg("ui.task.cancelled"))
        progress(
            RunProgress(
                mode="single",
                phase=_msg("ui.task.cancelled"),
                current=last_progress_units,
                total=total_units,
                current_name=input_dir.name,
                detail_text=_msg("ui.task.cancelled"),
            )
        )
        if emit_kfx and not keep_kpf:
            try:
                build_output_path.unlink(missing_ok=True)
            except TypeError:
                if build_output_path.exists():
                    build_output_path.unlink()
        return RunSummary(
            mode="single",
            output_location=output_path.parent,
            successes=tuple(),
            failures=tuple(),
            stopped=True,
        )

    status(_msg("ui.completed"))
    progress(
        RunProgress(
            mode="single",
            phase=_msg("ui.completed"),
            current=total_units,
            total=total_units,
            current_name=input_dir.name,
            successes=1,
            failures=0,
        )
    )
    return RunSummary(
        mode="single",
        output_location=output_path.parent,
        successes=(result,),
        failures=tuple(),
        stopped=False,
    )


def _execute_batch_run(
    config: AppRunConfig,
    batch_dir: Path,
    output_dir: Path,
    template_assets,
    image_processing,
    layout_options: LayoutOptions,
    log: Callable[[str], None],
    status: Callable[[str], None],
    progress: Callable[[RunProgress], None],
    should_stop: Callable[[], bool],
    pause_if_requested: Callable[[], None],
) -> RunSummary:
    emit_kfx = should_emit_kfx(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    subdirs = find_batch_directories(batch_dir, output_dir)
    if not subdirs:
        raise ValueError(_msg("ui.error.batch.no.processable.subdirs"))

    successes: list[BuildResult] = []
    failures: list[VolumeFailure] = []
    stopped = False
    volume_unit = 1000
    volume_progress: dict[Path, int] = {subdir: 0 for subdir in subdirs}
    progress_lock = Lock()

    def make_volume_progress_callback(subdir: Path) -> Callable[[BuildStageProgress], None]:
        def report(stage: BuildStageProgress) -> None:
            fraction = _build_stage_fraction(stage)
            if emit_kfx:
                fraction *= 0.78
            units = round(volume_unit * max(0.0, min(1.0, fraction)))
            with progress_lock:
                volume_progress[subdir] = max(volume_progress.get(subdir, 0), units)
                current = sum(volume_progress.values())
            progress(
                RunProgress(
                    mode="batch",
                    phase=_msg(stage.phase),
                    current=current,
                    total=len(subdirs) * volume_unit,
                    current_name=f"{subdir.name} · {stage.current_name}" if stage.current_name else subdir.name,
                    successes=len(successes),
                    failures=len(failures),
                    detail_current=stage.current,
                    detail_total=stage.total,
                    detail_text=_build_detail_text(stage),
                    workers=stage.workers,
                    indeterminate=stage.indeterminate,
                )
            )

        return report

    def mark_volume_done(subdir: Path) -> int:
        with progress_lock:
            volume_progress[subdir] = volume_unit
            return sum(volume_progress.values())

    def current_volume_units() -> int:
        with progress_lock:
            return sum(volume_progress.values())

    if config.jobs == 1:
        worker_image_processing = image_processing
        total = len(subdirs)
        for index, subdir in enumerate(subdirs, start=1):
            pause_if_requested()
            if should_stop():
                stopped = True
                log(_msg("ui.stop.requested.no.more.queued.volumes.one"))
                break
            status(_msg("ui.status.processing.volume", current=index, total=total))
            progress(
                RunProgress(
                    mode="batch",
                    phase=_msg("ui.processing"),
                    current=sum(volume_progress.values()),
                    total=len(subdirs) * volume_unit,
                    current_name=subdir.name,
                    successes=len(successes),
                    failures=len(failures),
                )
            )
            log(_msg("ui.started", name=subdir.name))
            try:
                result = _capture_console_output(
                    log,
                    _run_one_volume,
                    subdir,
                    _batch_volume_output_path(config, output_dir, subdir, index),
                    template_assets,
                    worker_image_processing,
                    config.shift,
                    layout_options,
                    emit_kfx,
                    should_keep_kpf(config),
                    config.kfx_plugin.strip() or None,
                    _resolve_batch_volume_title(config, subdir, index),
                    make_volume_progress_callback(subdir),
                    should_stop,
                )
            except BuildCancelled:
                current_units = current_volume_units()
                stopped = True
                log(_msg("ui.task.cancelled"))
            except Exception as exc:
                current_units = mark_volume_done(subdir)
                failures.append(VolumeFailure(volume_dir=subdir, reason=str(exc)))
                log(_msg("ui.failed", name=subdir.name))
                log(_msg("ui.reason", reason=str(exc)))
            else:
                current_units = mark_volume_done(subdir)
                successes.append(result)
                log(_msg("ui.done", name=subdir.name))
            progress(
                RunProgress(
                    mode="batch",
                    phase=_msg("ui.processing"),
                    current=current_units,
                    total=len(subdirs) * volume_unit,
                    current_name=subdir.name,
                    successes=len(successes),
                    failures=len(failures),
                )
            )
            if stopped:
                break
    else:
        worker_count = min(config.jobs, len(subdirs))
        worker_image_processing = replace(image_processing, preprocessing_workers=1)
        total = len(subdirs)
        status(_msg("ui.status.parallel.processing", workers=worker_count))
        log(_msg("ui.log.parallel.workers", workers=worker_count))
        completed = 0
        pending_iter = iter(enumerate(subdirs, start=1))
        future_to_subdir: dict[concurrent.futures.Future[BuildResult], tuple[int, Path]] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            for _ in range(worker_count):
                pause_if_requested()
                if should_stop():
                    stopped = True
                    break
                try:
                    next_index, next_subdir = next(pending_iter)
                except StopIteration:
                    break
                future = executor.submit(
                    _run_one_volume,
                    next_subdir,
                    _batch_volume_output_path(config, output_dir, next_subdir, next_index),
                    template_assets,
                    worker_image_processing,
                    config.shift,
                    layout_options,
                    emit_kfx,
                    should_keep_kpf(config),
                    config.kfx_plugin.strip() or None,
                    _resolve_batch_volume_title(config, next_subdir, next_index),
                    make_volume_progress_callback(next_subdir),
                    should_stop,
                )
                future_to_subdir[future] = (next_index, next_subdir)
                log(_msg("ui.started", name=next_subdir.name))

            while future_to_subdir:
                done, _ = concurrent.futures.wait(
                    future_to_subdir,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    _subdir_index, subdir = future_to_subdir.pop(future)
                    completed += 1
                    try:
                        result = future.result()
                    except BuildCancelled:
                        current_units = current_volume_units()
                        stopped = True
                        log(_msg("ui.task.cancelled"))
                    except Exception as exc:
                        current_units = mark_volume_done(subdir)
                        failures.append(VolumeFailure(volume_dir=subdir, reason=str(exc)))
                        log(_msg("ui.failed", name=subdir.name))
                        log(_msg("ui.reason", reason=str(exc)))
                    else:
                        current_units = mark_volume_done(subdir)
                        successes.append(result)
                        log(_msg("ui.done", name=subdir.name))

                    progress(
                        RunProgress(
                            mode="batch",
                            phase=_msg("ui.parallel.processing"),
                            current=current_units,
                            total=len(subdirs) * volume_unit,
                            current_name=subdir.name,
                            successes=len(successes),
                            failures=len(failures),
                        )
                    )

                    if should_stop():
                        stopped = True
                        continue

                    pause_if_requested()
                    if should_stop():
                        stopped = True
                        continue

                    try:
                        next_index, next_subdir = next(pending_iter)
                    except StopIteration:
                        continue

                    next_future = executor.submit(
                        _run_one_volume,
                        next_subdir,
                        _batch_volume_output_path(config, output_dir, next_subdir, next_index),
                        template_assets,
                        worker_image_processing,
                        config.shift,
                        layout_options,
                        emit_kfx,
                        should_keep_kpf(config),
                        config.kfx_plugin.strip() or None,
                        _resolve_batch_volume_title(config, next_subdir, next_index),
                        make_volume_progress_callback(next_subdir),
                        should_stop,
                    )
                    future_to_subdir[next_future] = (next_index, next_subdir)
                    log(_msg("ui.started", name=next_subdir.name))

        if should_stop():
            stopped = True
            log(_msg("ui.stop.request.applied.no.new.volume.tasks"))

    successes.sort(key=lambda item: item.input_dir.name.lower())
    failures.sort(key=lambda item: item.volume_dir.name.lower())
    status(_msg("ui.task.cancelled" if stopped else "ui.batch.completed"))
    progress(
        RunProgress(
            mode="batch",
            phase=_msg("ui.task.cancelled" if stopped else "ui.completed"),
            current=current_volume_units() if stopped else (len(successes) + len(failures)) * volume_unit,
            total=len(subdirs) * volume_unit,
            successes=len(successes),
            failures=len(failures),
        )
    )
    return RunSummary(
        mode="batch",
        output_location=output_dir,
        successes=tuple(successes),
        failures=tuple(failures),
        stopped=stopped,
    )


def _run_one_volume(
    input_dir: Path,
    output_path: Path,
    template_assets,
    image_processing,
    shift_first_page: bool,
    layout_options: LayoutOptions,
    emit_kfx: bool,
    keep_kpf: bool,
    kfx_plugin_ref: str | None,
    title: str | None = None,
    progress_callback: BuildProgressCallback | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> BuildResult:
    build_output_path = output_path if keep_kpf else output_path.with_suffix(".kpf")
    result = build_kpf(
        template_assets=template_assets,
        input_dir=input_dir,
        output_path=build_output_path,
        title=title or input_dir.name,
        image_processing=image_processing,
        shift_first_page=shift_first_page,
        layout_options=layout_options,
        progress_callback=progress_callback,
        stop_requested=stop_requested,
    )
    if emit_kfx:
        if stop_requested is not None and stop_requested():
            raise BuildCancelled("ui.task.cancelled")
        if progress_callback is not None:
            progress_callback(
                BuildStageProgress(
                    "ui.generating.kfx",
                    0,
                    0,
                    current_name=input_dir.name,
                    indeterminate=True,
                )
            )
        kfx_result = convert_kpf_to_kfx(
            result.output_path,
            None if keep_kpf else output_path,
            plugin_ref=kfx_plugin_ref,
        )
        if keep_kpf:
            result.kfx_output_path = kfx_result.kfx_path
        else:
            try:
                build_output_path.unlink(missing_ok=True)
            except TypeError:
                if build_output_path.exists():
                    build_output_path.unlink()
            result.output_path = kfx_result.kfx_path
            result.kfx_output_path = None
    return result


def _resolve_batch_volume_title(config: AppRunConfig, subdir: Path, volume_index: int) -> str:
    series = config.title.strip()
    if not config.custom_title_enabled or not series:
        return subdir.name

    template = config.volume_title_template
    if not template.strip():
        template = " 第 {volume} 卷"
    values = {
        "series": series,
        "volume": volume_index,
        "volume2": f"{volume_index:02d}",
        "folder": subdir.name,
    }
    try:
        rendered = template.format(**values)
    except Exception:
        rendered = f" 第 {volume_index} 卷"
    if "{series}" in template:
        return rendered.strip() or subdir.name
    return f"{series}{rendered}".strip() or subdir.name


def _default_output_base_name(config: AppRunConfig, input_dir: Path) -> str:
    title = config.title.strip()
    if config.custom_title_enabled and title:
        return title
    return input_dir.name


def _batch_volume_output_path(config: AppRunConfig, output_dir: Path, subdir: Path, volume_index: int) -> Path:
    title = _resolve_batch_volume_title(config, subdir, volume_index)
    filename = sanitize_output_filename(title, fallback=subdir.name)
    return output_dir / f"{filename}{primary_output_suffix(normalize_output_format(config))}"


def _capture_console_output(
    log_callback: Callable[[str], None],
    func: Callable[..., object],
    *args,
    **kwargs,
):
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        return func(*args, **kwargs)


def _build_detection_ignored_paths(output_location: str) -> set[Path]:
    if not output_location.strip():
        return set()
    output_path = Path(output_location).expanduser()
    if output_path.suffix.lower() == ".kpf":
        return set()
    return {output_path}


def _format_path_list(paths: tuple[Path, ...] | list[Path], limit: int = 5) -> str:
    if not paths:
        return "-"
    names = [path.name for path in paths[:limit]]
    if len(paths) > limit:
        names.append(f"... ({len(paths)})")
    return ", ".join(names)
