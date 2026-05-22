from __future__ import annotations

import sys
from pathlib import Path

from ..gui.assets import load_app_icon
from .bridge.app_controller import AppController


def main() -> int:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
    except ImportError as exc:
        raise SystemExit("PySide6 with Qt Quick/QML support is required for the QML UI.") from exc

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("img2kpf")
    app.setOrganizationName("img2kpf")
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    controller = AppController()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appController", controller)

    qml_path = Path(__file__).resolve().parent / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        return 1
    return app.exec()
