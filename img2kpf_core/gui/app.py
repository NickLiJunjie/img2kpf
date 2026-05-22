from __future__ import annotations

import sys

from ..i18n import resolve_language
from .i18n import normalize_ui_language, translate_gui_text


def _selected_ui() -> str:
    for index, argument in enumerate(sys.argv):
        if argument == "--ui" and index + 1 < len(sys.argv):
            return sys.argv[index + 1].strip().lower()
        if argument.startswith("--ui="):
            return argument.split("=", 1)[1].strip().lower()
    return "qml"


def _strip_ui_args() -> None:
    stripped = [sys.argv[0]]
    skip_next = False
    for argument in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if argument == "--ui":
            skip_next = True
            continue
        if argument.startswith("--ui="):
            continue
        stripped.append(argument)
    sys.argv[:] = stripped


def main() -> int:
    if "--check-env" in sys.argv:
        print(sys.executable)
        print(sys.prefix)
        return 0
    if _selected_ui() == "qml":
        _strip_ui_args()
        from ..gui_qml.app import main as run_qml_gui

        return run_qml_gui()

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
