from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from pathlib import Path


PLUGINS_ROOT = Path(__file__).with_name("plugins")
DEFAULT_KFX_PLUGIN_ID = "kfx_output"
DEFAULT_KFX_PLUGIN_DIR = PLUGINS_ROOT / DEFAULT_KFX_PLUGIN_ID
DEFAULT_KFX_PLUGIN_ARCHIVE_NAME = "KFX Output.zip"
DEFAULT_KFX_PLUGIN_ARCHIVE_PATH = DEFAULT_KFX_PLUGIN_DIR / DEFAULT_KFX_PLUGIN_ARCHIVE_NAME
LEGACY_KFX_PLUGIN_PATH = Path(".analysis") / "KFX Output.zip"


def _app_data_root(app_name: str = "img2kpf") -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if appdata:
            return Path(appdata) / app_name
    return Path.home() / ".config" / app_name


def user_kfx_plugin_archive_path(app_name: str = "img2kpf") -> Path:
    return _app_data_root(app_name) / "plugins" / DEFAULT_KFX_PLUGIN_ID / DEFAULT_KFX_PLUGIN_ARCHIVE_NAME


def install_kfx_plugin_archive(source_path: Path, destination_path: Path | None = None) -> Path:
    source = source_path.expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"KFX Output plugin archive not found: {source}")
    if source.suffix.lower() != ".zip" or not zipfile.is_zipfile(source):
        raise ValueError(f"KFX Output plugin must be a valid .zip archive: {source}")

    destination = (destination_path or user_kfx_plugin_archive_path()).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        temp_path = destination.with_name(f"{destination.name}.tmp")
        shutil.copy2(source, temp_path)
        temp_path.replace(destination)
    return destination.resolve()


def _plugin_manifest_path(plugin_id: str) -> Path:
    return PLUGINS_ROOT / plugin_id / "plugin.json"


def _missing_default_plugin_error() -> FileNotFoundError:
    return FileNotFoundError(
        "KFX Output is not bundled with this repository. "
        f"Download `{DEFAULT_KFX_PLUGIN_ARCHIVE_NAME}` yourself and either place it at "
        f"`{DEFAULT_KFX_PLUGIN_ARCHIVE_PATH}`, import it in the GUI, or pass an explicit zip path with `--kfx-plugin`."
    )


def resolve_plugin_archive(plugin_ref: str | None, default_plugin_id: str = DEFAULT_KFX_PLUGIN_ID) -> Path:
    user_archive_path = user_kfx_plugin_archive_path()
    if plugin_ref is None:
        if user_archive_path.is_file():
            return user_archive_path.resolve()
        manifest_path = _plugin_manifest_path(default_plugin_id)
        if manifest_path.is_file():
            return resolve_plugin_archive(default_plugin_id, default_plugin_id=default_plugin_id)
        if DEFAULT_KFX_PLUGIN_ARCHIVE_PATH.is_file():
            return DEFAULT_KFX_PLUGIN_ARCHIVE_PATH.resolve()
        if LEGACY_KFX_PLUGIN_PATH.is_file():
            return LEGACY_KFX_PLUGIN_PATH.resolve()
        raise _missing_default_plugin_error()

    candidate_path = Path(plugin_ref)
    if candidate_path.is_file():
        return candidate_path.resolve()

    if candidate_path.is_dir():
        manifest_path = candidate_path / "plugin.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Plugin directory is missing `plugin.json`: {candidate_path}")
        return _resolve_from_manifest(manifest_path)

    manifest_path = _plugin_manifest_path(plugin_ref)
    if plugin_ref == default_plugin_id and user_archive_path.is_file():
        return user_archive_path.resolve()
    if manifest_path.is_file():
        return _resolve_from_manifest(manifest_path)

    raise FileNotFoundError(
        f"Cannot resolve plugin reference: {plugin_ref}. "
        "Pass a zip path, a plugin directory, or a registered plugin ID."
    )


def _resolve_from_manifest(manifest_path: Path) -> Path:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = payload.get("archive")
    if not archive:
        raise ValueError(f"Plugin manifest is missing the `archive` field: {manifest_path}")

    archive_path = (manifest_path.parent / archive).resolve()
    if not archive_path.is_file():
        if manifest_path == _plugin_manifest_path(DEFAULT_KFX_PLUGIN_ID):
            raise _missing_default_plugin_error()
        raise FileNotFoundError(f"Plugin archive does not exist: {archive_path}")
    return archive_path
