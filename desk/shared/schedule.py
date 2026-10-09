from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from desk.shared.errors import DeskError

KEEPER_TASK = "WeekendDeskKeeper"
RESEAL_TASK = "WeekendDeskReseal"


def schtasks_create(task_name: str, script: Path) -> list[str]:
    target = f'"{script}"' if " " in str(script) else str(script)
    return [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        task_name,
        "/SC",
        "MINUTE",
        "/MO",
        "1",
        "/RU",
        "SYSTEM",
        "/RL",
        "HIGHEST",
        "/TR",
        target,
    ]


def task_script_body(root: Path, python: Path, args: str, config: Path) -> str:
    for path in (root, python, config):
        if '"' in str(path):
            raise DeskError(f"A quote in {path} cannot be used in the scheduled task.")
    return (
        "@echo off\r\n"
        f'cd /d "{root}"\r\n'
        f'"{python}" {args} --config "{config}"\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )


def write_task_script(filename: str, args: str, config: Path) -> Path:
    desk_dir = Path(__file__).resolve().parent.parent
    root = desk_dir.parent
    folder = desk_dir / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / filename
    script.write_text(task_script_body(root, Path(sys.executable), args, config), encoding="ascii")
    return script


def install_task(task_name: str, script: Path) -> None:
    if not script.is_file():
        raise DeskError(f"Missing {script.name}.")
    result = subprocess.run(
        schtasks_create(task_name, script),
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise DeskError(detail or f"Could not register {task_name}.")


def is_admin() -> bool:
    if os.name != "nt":
        return False
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
