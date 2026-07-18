from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, fields
from pathlib import Path

from .models import (
    CROP_MODE_OPTIONS,
    CROP_STRENGTH_SEMANTICS_VERSION,
    GuiState,
    JOBS_MAX,
    JOBS_MIN,
    PERFORMANCE_MODE_OPTIONS,
    SPREAD_CROP_STRENGTH_DEFAULT,
)


def _legacy_value_to_retention_ratio(value: float, semantics_version: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 1.00
    if semantics_version == 2:
        numeric = 1.70 - numeric
    return round(max(0.70, min(1.00, numeric)), 2)


def _config_root(app_name: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if appdata:
            return Path(appdata) / app_name
    return Path.home() / ".config" / app_name


class GuiSettingsStore:
    def __init__(self, app_name: str = "img2kpf") -> None:
        self._root = _config_root(app_name)
        self._path = self._root / "gui_settings.json"
        self._profiles_path = self._root / "gui_profiles.json"

    @property
    def path(self) -> Path:
        return self._path

    @property
    def profiles_path(self) -> Path:
        return self._profiles_path

    def load(self) -> GuiState:
        if not self._path.is_file():
            return GuiState()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return GuiState()

        valid_keys = {field.name for field in fields(GuiState)}
        filtered = {key: value for key, value in payload.items() if key in valid_keys}
        return self._state_from_payload(filtered, payload)

    def save(self, state: GuiState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_profiles(self) -> dict[str, GuiState]:
        profiles, _ = self._read_profile_store()
        return profiles

    def load_default_profile_name(self) -> str | None:
        _, default_profile = self._read_profile_store()
        return default_profile

    def get_profile(self, name: str) -> GuiState | None:
        return self.load_profiles().get(name)

    def save_profile(self, name: str, state: GuiState) -> None:
        profiles, default_profile = self._read_profile_store()
        profiles[name] = state
        self._write_profile_store(profiles, default_profile)

    def delete_profile(self, name: str) -> None:
        profiles, default_profile = self._read_profile_store()
        profiles.pop(name, None)
        if default_profile == name:
            default_profile = None
        self._write_profile_store(profiles, default_profile)

    def set_default_profile(self, name: str | None) -> None:
        profiles, _ = self._read_profile_store()
        if name is not None and name not in profiles:
            raise KeyError(name)
        self._write_profile_store(profiles, name)

    def _read_profile_store(self) -> tuple[dict[str, GuiState], str | None]:
        if not self._profiles_path.is_file():
            return {}, None
        try:
            payload = json.loads(self._profiles_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}, None
        if not isinstance(payload, dict):
            return {}, None

        if isinstance(payload.get("profiles"), dict):
            raw_profiles = payload["profiles"]
            default_profile = payload.get("default_profile")
            if not isinstance(default_profile, str):
                default_profile = None
        else:
            raw_profiles = payload
            default_profile = None

        profiles: dict[str, GuiState] = {}
        valid_keys = {field.name for field in fields(GuiState)}
        for name, item in raw_profiles.items():
            if not isinstance(name, str) or not isinstance(item, dict):
                continue
            filtered = {key: value for key, value in item.items() if key in valid_keys}
            profiles[name] = self._state_from_payload(filtered, item)

        if default_profile not in profiles:
            default_profile = None
        return profiles, default_profile

    def _write_profile_store(self, profiles: dict[str, GuiState], default_profile: str | None) -> None:
        self._profiles_path.parent.mkdir(parents=True, exist_ok=True)
        self._profiles_path.write_text(
            json.dumps(
                {
                    "default_profile": default_profile,
                    "profiles": {key: asdict(value) for key, value in profiles.items()},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _state_from_payload(self, filtered: dict, raw_payload: dict) -> GuiState:
        state = GuiState(**filtered)
        if "panel_preset" not in raw_payload:
            state.panel_preset = "scribe_1240x1860" if state.scribe_panel else "none"
        if "shift_mode" not in raw_payload:
            state.shift_mode = "on" if state.shift else "off"
        if "output_format" not in raw_payload:
            state.output_format = "kpf_kfx" if state.emit_kfx else "kpf"
        if state.preserve_color == "auto":
            state.preserve_color = "enabled"
        semantics_version = raw_payload.get("crop_strength_semantics_version")
        if semantics_version != CROP_STRENGTH_SEMANTICS_VERSION:
            state.crop_edge_threshold = _legacy_value_to_retention_ratio(state.crop_edge_threshold, semantics_version)
            state.spread_fill_edge_threshold = _legacy_value_to_retention_ratio(
                state.spread_fill_edge_threshold,
                semantics_version,
            )
            state.spread_fill_inner_edge_threshold = _legacy_value_to_retention_ratio(
                state.spread_fill_inner_edge_threshold,
                semantics_version,
            )
            if semantics_version == 3:
                state.spread_fill_edge_threshold = SPREAD_CROP_STRENGTH_DEFAULT
                state.spread_fill_inner_edge_threshold = SPREAD_CROP_STRENGTH_DEFAULT
            state.crop_strength_semantics_version = CROP_STRENGTH_SEMANTICS_VERSION
        valid_crop_modes = {value for value, _ in CROP_MODE_OPTIONS}
        if state.crop_mode not in valid_crop_modes:
            state.crop_mode = "off"
        valid_performance_modes = {value for value, _ in PERFORMANCE_MODE_OPTIONS}
        if state.performance_mode not in valid_performance_modes:
            state.performance_mode = "balanced"
        if state.cover_mode not in {"auto", "page"}:
            state.cover_mode = "auto"
        if state.cover_batch_scope not in {"uniform", "per_volume"}:
            state.cover_batch_scope = "uniform"
        try:
            state.cover_page_number = max(1, int(state.cover_page_number))
        except (TypeError, ValueError):
            state.cover_page_number = 1
        if not isinstance(state.cover_volume_pages, dict):
            state.cover_volume_pages = {}
        else:
            normalized_pages: dict[str, int] = {}
            for key, value in state.cover_volume_pages.items():
                if not isinstance(key, str):
                    continue
                try:
                    normalized_pages[key] = max(1, int(value))
                except (TypeError, ValueError):
                    continue
            state.cover_volume_pages = normalized_pages
        state.jobs = max(JOBS_MIN, min(int(state.jobs), JOBS_MAX))
        return state
