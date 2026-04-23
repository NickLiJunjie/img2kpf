from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    _ensure_project_venv()

    from img2kpf.gui.app import main as run_gui

    return run_gui()


def _ensure_project_venv() -> None:
    project_root = Path(__file__).resolve().parent
    if sys.platform.startswith("win"):
        venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = project_root / ".venv" / "bin" / "python"

    if not venv_python.exists():
        return

    active_prefix = Path(sys.prefix).resolve()
    target_prefix = (project_root / ".venv").resolve()
    if active_prefix == target_prefix:
        return

    current_python = Path(sys.executable).absolute()
    target_python = venv_python.absolute()
    if current_python == target_python:
        return

    os.environ["VIRTUAL_ENV"] = str(project_root / ".venv")
    os.environ["PATH"] = f"{target_python.parent}{os.pathsep}{os.environ.get('PATH', '')}"
    os.execv(str(target_python), [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())

