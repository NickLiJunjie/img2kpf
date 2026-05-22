from __future__ import annotations

import json
from pathlib import Path


PLUGINS_ROOT = Path(__file__).with_name("plugins")
DEFAULT_KFX_PLUGIN_ID = "kfx_output"
DEFAULT_KFX_PLUGIN_DIR = PLUGINS_ROOT / DEFAULT_KFX_PLUGIN_ID
DEFAULT_KFX_PLUGIN_ARCHIVE_NAME = "KFX Output.zip"
DEFAULT_KFX_PLUGIN_ARCHIVE_PATH = DEFAULT_KFX_PLUGIN_DIR / DEFAULT_KFX_PLUGIN_ARCHIVE_NAME
LEGACY_KFX_PLUGIN_PATH = Path(".analysis") / "KFX Output.zip"


def _plugin_manifest_path(plugin_id: str) -> Path:
    return PLUGINS_ROOT / plugin_id / "plugin.json"


def _missing_default_plugin_error() -> FileNotFoundError:
    return FileNotFoundError(
        "KFX Output is not bundled with this repository. "
        f"Download `{DEFAULT_KFX_PLUGIN_ARCHIVE_NAME}` yourself and either place it at "
        f"`{DEFAULT_KFX_PLUGIN_ARCHIVE_PATH}` or pass an explicit zip path with `--kfx-plugin`."
    )


def resolve_plugin_archive(plugin_ref: str | None, default_plugin_id: str = DEFAULT_KFX_PLUGIN_ID) -> Path:
    if plugin_ref is None:
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
