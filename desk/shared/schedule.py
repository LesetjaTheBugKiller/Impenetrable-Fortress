from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from desk.shared.errors import DeskError

KEEPER_TASK = "WeekendDeskKeeper"
RESEAL_TASK = "WeekendDeskReseal"


def schtasks_create(task_name: str, script: Path, as_system: bool = True) -> list[str]:
    """Every-minute task.

    as_system=True is for the VPS keeper, which has to own the firewall.
    as_system=False is for the home-PC reseal: it runs as the user who registers it,
    only while that user is signed in, with no stored password and no elevation,
    so it sees the same %LOCALAPPDATA% and seal file as `seal` and `link`.
    """
    target = f'"{script}"' if " " in str(script) else str(script)
    command = [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        task_name,
        "/SC",
        "MINUTE",
        "/MO",
        "1",
    ]
    if as_system:
        command.extend(["/RU", "SYSTEM", "/RL", "HIGHEST"])
    else:
        command.extend(["/IT", "/RL", "LIMITED"])
    command.extend(["/TR", target])
    return command


def refuse_unsafe_for_cmd(value: str) -> None:
    """A task script is a .cmd file. % would be expanded and a line break would start a new command."""
    if "%" in value:
        raise DeskError(f"A percent sign in {value!r} cannot be used in the scheduled task: cmd would expand it.")
    if "\r" in value or "\n" in value:
        raise DeskError(f"A line break in {value!r} cannot be used in the scheduled task.")


def task_script_body(root: Path, python: Path, args: str, config: Path) -> str:
    for path in (root, python, config):
        if '"' in str(path):
            raise DeskError(f"A quote in {path} cannot be used in the scheduled task.")
        refuse_unsafe_for_cmd(str(path))
    refuse_unsafe_for_cmd(args)
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


def install_task(task_name: str, script: Path, as_system: bool = True) -> None:
    if not script.is_file():
        raise DeskError(f"Missing {script.name}.")
    result = subprocess.run(
        schtasks_create(task_name, script, as_system=as_system),
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
