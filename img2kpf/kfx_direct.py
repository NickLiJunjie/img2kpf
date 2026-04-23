from __future__ import annotations

import hashlib
import logging
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .plugin_registry import DEFAULT_KFX_PLUGIN_ID, resolve_plugin_archive

_LOADED_PLUGIN_ROOTS: set[str] = set()


@dataclass(frozen=True)
class KFXConversionResult:
    kpf_path: Path
    kfx_path: Path
    warnings: list[str]
    errors: list[str]


def _plugin_unpack_root(plugin_zip_path: Path) -> Path:
    stat = plugin_zip_path.stat()
    fingerprint = hashlib.md5(
        f"{plugin_zip_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:12]
    return Path(".analysis") / "_kfx_output_runtime" / fingerprint


def _ensure_plugin_importable(plugin_zip_path: Path) -> None:
    if not plugin_zip_path.is_file():
        raise FileNotFoundError(
            "KFX Output plugin archive not found: "
            f"{plugin_zip_path}. Download `KFX Output.zip` yourself, place it under "
            "`img2kpf/plugins/kfx_output/`, or pass an explicit `--plugin` / `--kfx-plugin` path."
        )

    unpack_root = _plugin_unpack_root(plugin_zip_path)
    unpack_root_key = str(unpack_root.resolve())
    if unpack_root_key not in _LOADED_PLUGIN_ROOTS:
        if not unpack_root.exists():
            unpack_root.parent.mkdir(parents=True, exist_ok=True)
            temp_root = Path(
                tempfile.mkdtemp(prefix=f"{unpack_root.name}.tmp.", dir=str(unpack_root.parent))
            )
            try:
                with zipfile.ZipFile(plugin_zip_path, "r") as archive:
                    archive.extractall(temp_root)
                try:
                    temp_root.rename(unpack_root)
                except FileExistsError:
                    pass
            finally:
                if temp_root.exists():
                    shutil.rmtree(temp_root, ignore_errors=True)

        plugin_modules_path = unpack_root / "kfxlib" / "calibre-plugin-modules"
        for candidate in (plugin_modules_path, unpack_root):
            candidate_text = str(candidate.resolve())
            if candidate_text not in sys.path:
                sys.path.insert(0, candidate_text)
        _LOADED_PLUGIN_ROOTS.add(unpack_root_key)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def convert_kpf_to_kfx(
    kpf_path: Path,
    kfx_path: Path | None = None,
    plugin_ref: str | None = None,
) -> KFXConversionResult:
    resolved_kpf_path = kpf_path.resolve()
    if not resolved_kpf_path.is_file():
        raise FileNotFoundError(f"KPF file does not exist: {resolved_kpf_path}")

    resolved_plugin_zip = resolve_plugin_archive(plugin_ref)
    _ensure_plugin_importable(resolved_plugin_zip)

    from kfxlib import JobLog, YJ_Book, YJ_Metadata, set_logger

    logger = _build_logger("img2kpf.kfx_direct")
    job_log = JobLog(logger)
    set_logger(job_log)
    try:
        book = YJ_Book(str(resolved_kpf_path))
        metadata = YJ_Metadata(replace_existing_authors_with_sort=True)
        metadata.asin = True
        metadata.cde_content_type = "PDOC"
        book.decode_book(set_metadata=metadata)
        result = book.convert_to_single_kfx()
    finally:
        set_logger()

    if not result:
        error_message = "; ".join(job_log.errors) if job_log.errors else "KFX 转换失败。"
        raise RuntimeError(error_message)

    resolved_kfx_path = (kfx_path or resolved_kpf_path.with_suffix(".kfx")).resolve()
    resolved_kfx_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_kfx_path.write_bytes(result)
    print(f"Generated KFX: {resolved_kfx_path}")
    return KFXConversionResult(
        kpf_path=resolved_kpf_path,
        kfx_path=resolved_kfx_path,
        warnings=list(job_log.warnings),
        errors=list(job_log.errors),
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Convert a KPF file into KFX by loading the KFX Output plugin zip directly.")
    parser.add_argument("--input", type=Path, required=True, help="Input `.kpf` file")
    parser.add_argument("--output", type=Path, help="Output `.kfx` path; defaults to the input basename")
    parser.add_argument(
        "--plugin",
        type=str,
        default=DEFAULT_KFX_PLUGIN_ID,
        help="Plugin ID, plugin directory, or plugin zip path; default expects `img2kpf/plugins/kfx_output/KFX Output.zip`.",
    )
    args = parser.parse_args()

    convert_kpf_to_kfx(args.input, args.output, args.plugin)


if __name__ == "__main__":
    main()
