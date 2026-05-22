from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon


ASSETS_ROOT = Path(__file__).resolve().parents[1] / "assets"
GUI_ASSETS_ROOT = ASSETS_ROOT / "gui"
APP_ICON_PATH = GUI_ASSETS_ROOT / "app_icon.svg"


def load_app_icon() -> QIcon:
    if APP_ICON_PATH.is_file():
        return QIcon(str(APP_ICON_PATH))
    return QIcon()
