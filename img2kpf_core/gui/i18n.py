from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..i18n import decode_i18n_message


_PACKS_DIR = Path(__file__).resolve().parents[1] / "assets" / "i18n" / "packs"
_DEFAULT_LANGUAGE_LABELS = {"zh": "中文", "en": "English"}
_DEFAULT_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "cn": "zh",
    "en-us": "en",
    "en-gb": "en",
}


def _normalize_token(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_messages(payload: dict, field: str = "messages") -> dict[str, str]:
    raw_messages = payload.get(field)
    if not isinstance(raw_messages, dict):
        return {}
    messages: dict[str, str] = {}
    for key, value in raw_messages.items():
        if isinstance(key, str) and isinstance(value, str):
            messages[key] = value
    return messages


@lru_cache(maxsize=1)
def _pack_payloads() -> dict[str, dict]:
    if not _PACKS_DIR.is_dir():
        return {}

    packs: dict[str, dict] = {}
    for path in sorted(_PACKS_DIR.glob("*.json"), key=lambda item: item.name.lower()):
        payload = _read_json(path)
        if not payload:
            continue
        meta = payload.get("meta")
        language = ""
        if isinstance(meta, dict):
            raw_language = meta.get("language")
            if isinstance(raw_language, str):
                language = _normalize_token(raw_language)
        if not language:
            language = _normalize_token(path.stem)
        if not language:
            continue
        packs[language] = payload
    return packs


def _ordered_languages(candidates: list[str]) -> tuple[str, ...]:
    normalized = {_normalize_token(language) for language in candidates if isinstance(language, str)}
    normalized.discard("")
    if not normalized:
        return ()
    preferred = [language for language in ("zh", "en") if language in normalized]
    trailing = sorted(language for language in normalized if language not in {"zh", "en"})
    return tuple(preferred + trailing)


@lru_cache(maxsize=1)
def available_ui_languages() -> tuple[str, ...]:
    packs = _pack_payloads()
    if not packs:
        return ("zh", "en")
    ordered = _ordered_languages(list(packs.keys()))
    return ordered or ("zh", "en")


@lru_cache(maxsize=1)
def _language_aliases() -> dict[str, str]:
    aliases: dict[str, str] = dict(_DEFAULT_ALIASES)
    for language in available_ui_languages():
        aliases[language] = language

    for language, payload in _pack_payloads().items():
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            continue
        raw_aliases = meta.get("aliases")
        if not isinstance(raw_aliases, list):
            continue
        for alias in raw_aliases:
            if not isinstance(alias, str):
                continue
            alias_key = _normalize_token(alias)
            if alias_key:
                aliases[alias_key] = language
    return aliases


@lru_cache(maxsize=1)
def language_display_labels() -> dict[str, str]:
    labels = dict(_DEFAULT_LANGUAGE_LABELS)
    for language, payload in _pack_payloads().items():
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            continue
        label = meta.get("label")
        if isinstance(label, str) and label.strip():
            labels[language] = label.strip()
    return {language: labels.get(language, language) for language in available_ui_languages()}


def ui_language_options() -> tuple[tuple[str, str], ...]:
    labels = language_display_labels()
    return tuple((language, labels.get(language, language)) for language in available_ui_languages())


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict[str, str]]:
    packs = _pack_payloads()
    catalog: dict[str, dict[str, str]] = {}
    for language in available_ui_languages():
        payload = packs.get(language, {})
        messages = _extract_messages(payload)
        if messages:
            catalog[language] = messages
    return catalog


def normalize_ui_language(value: str | None, default: str = "zh") -> str:
    supported = available_ui_languages()
    supported_set = set(supported)
    aliases = _language_aliases()

    normalized_default = aliases.get(_normalize_token(default), _normalize_token(default))
    fallback = normalized_default if normalized_default in supported_set else (supported[0] if supported else "zh")
    if not value:
        return fallback

    normalized = aliases.get(_normalize_token(value), _normalize_token(value))
    if normalized in supported_set:
        return normalized
    return fallback


def reload_ui_catalog() -> None:
    _pack_payloads.cache_clear()
    available_ui_languages.cache_clear()
    _language_aliases.cache_clear()
    language_display_labels.cache_clear()
    _catalog.cache_clear()


def translate_gui_text(key: str, language: str, **kwargs: object) -> str:
    parsed = decode_i18n_message(key)
    token_kwargs: dict[str, object] = {}
    if parsed is not None:
        key, token_kwargs = parsed

    language_key = normalize_ui_language(language, default="zh")
    catalog = _catalog()
    text = catalog.get(language_key, {}).get(key)
    if text is None:
        text = catalog.get("zh", {}).get(key, key)
    merged_kwargs = dict(token_kwargs)
    merged_kwargs.update(kwargs)
    if not merged_kwargs:
        return text
    resolved_kwargs: dict[str, object] = {}
    for name, value in merged_kwargs.items():
        if isinstance(value, str):
            nested = decode_i18n_message(value)
            if nested is not None:
                resolved_kwargs[name] = translate_gui_text(value, language_key)
                continue
        resolved_kwargs[name] = value
    try:
        return text.format(**resolved_kwargs)
    except Exception:
        return text
