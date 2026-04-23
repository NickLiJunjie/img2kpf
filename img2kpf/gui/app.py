from __future__ import annotations

import sys

from ..i18n import resolve_language
from .i18n import normalize_ui_language, translate_gui_text


def main() -> int:
    if "--check-env" in sys.argv:
        print(sys.executable)
        print(sys.prefix)
        return 0

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        language = normalize_ui_language(resolve_language(), default="zh")
        print(translate_gui_text("ui.pyside6.not.installed.please.run.uv.pip", language))
        raise SystemExit(1) from exc

    from .assets import load_app_icon
    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("img2kpf")
    app.setOrganizationName("img2kpf")
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
