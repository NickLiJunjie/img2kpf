from __future__ import annotations

import json
import locale
import os

_I18N_MESSAGE_PREFIX = "__img2kpf_i18n__:"


def normalize_language(value: str | None, default: str = "zh") -> str:
    if not value:
        return default
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "zh-cn": "zh",
        "zh-hans": "zh",
        "cn": "zh",
        "en-us": "en",
        "en-gb": "en",
    }
    return aliases.get(normalized, normalized)


def _system_language() -> str:
    locale_name = locale.getlocale()[0]
    if locale_name:
        return locale_name
    fallback = locale.getdefaultlocale()[0]
    return fallback or ""


def resolve_language(preferred: str | None = None, default: str = "zh") -> str:
    if preferred:
        return normalize_language(preferred, default=default)
    env_value = os.environ.get("IMG2KPF_LANG")
    if env_value:
        return normalize_language(env_value, default=default)
    return normalize_language(_system_language(), default=default)


def encode_i18n_message(key: str, **kwargs: object) -> str:
    payload = {"key": key}
    if kwargs:
        payload["kwargs"] = kwargs
    return _I18N_MESSAGE_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def decode_i18n_message(value: str) -> tuple[str, dict[str, object]] | None:
    if not isinstance(value, str) or not value.startswith(_I18N_MESSAGE_PREFIX):
        return None
    raw = value[len(_I18N_MESSAGE_PREFIX) :]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        return None
    raw_kwargs = payload.get("kwargs", {})
    kwargs = raw_kwargs if isinstance(raw_kwargs, dict) else {}
    return key, kwargs
