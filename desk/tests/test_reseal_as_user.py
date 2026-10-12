"""Tests for the reseal-as-user fix. Standard library only."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from desk.link import __main__ as link_main
from desk.link.commands import run_reseal
from desk.shared.config import load_config, resolve_seal_path
from desk.shared.errors import DeskError
from desk.shared.schedule import schtasks_create, task_script_body

WINDOW = "TIMEZONE=Europe/Berlin\nOPEN_DAY=Friday\nOPEN_TIME=22:00\nCLOSE_DAY=Sunday\nCLOSE_TIME=22:00\n"


def write_env(folder: Path, extra: str = "") -> Path:
    path = folder / "desk.env"
    path.write_text(WINDOW + extra, encoding="utf-8")
    return path


class FakeBeacon:
    class _Now:
        round = 1
        at = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    def now(self):
        return self._Now()


class NoTimelock:
    def decrypt(self, armor):
        raise AssertionError("must not decrypt without a seal")

    def encrypt(self, plaintext, round_number):
        raise AssertionError("must not encrypt without a seal")


class ScheduleTests(unittest.TestCase):
    def test_keeper_task_still_runs_as_system(self):
        command = schtasks_create("WeekendDeskKeeper", Path(r"C:\desk\bin\keeper-task.cmd"))
        self.assertIn("SYSTEM", command)
        self.assertIn("HIGHEST", command)

    def test_reseal_task_runs_as_signed_in_user_without_elevation(self):
        command = schtasks_create("WeekendDeskReseal", Path(r"C:\desk\bin\reseal-task.cmd"), as_system=False)
        self.assertNotIn("SYSTEM", command)
        self.assertNotIn("HIGHEST", command)
        self.assertNotIn("/RU", command)
        self.assertIn("/IT", command)
        self.assertEqual(command[command.index("/RL") + 1], "LIMITED")
        self.assertEqual(command[-2:], ["/TR", r"C:\desk\bin\reseal-task.cmd"])

    def test_reseal_script_pins_seal_path(self):
        seal = Path(r"C:\Users\someone\AppData\Local\WeekendDesk\seal.json")
        body = task_script_body(
            Path(r"C:\repo"), Path(r"C:\py\python.exe"), link_main.reseal_task_args(seal), Path(r"C:\repo\desk\desk.env")
        )
        self.assertIn(f'reseal --seal-path "{seal}" --config', body)

    def test_quote_in_seal_path_is_refused(self):
        with self.assertRaises(DeskError):
            link_main.reseal_task_args(Path('C:/bad"name/seal.json'))

    def test_percent_or_line_break_in_seal_path_is_refused(self):
        for bad in ("C:/Users/%USERNAME%/seal.json", "C:/a\nb/seal.json", "C:/a\rb/seal.json"):
            with self.subTest(bad=bad), self.assertRaises(DeskError) as caught:
                link_main.reseal_task_args(Path(bad))
            self.assertIn("scheduled task", str(caught.exception))

    def test_percent_or_line_break_in_task_script_is_refused(self):
        good = dict(root=Path(r"C:\repo"), python=Path(r"C:\py\python.exe"), args="-m desk.link reseal",
                    config=Path(r"C:\repo\desk\desk.env"))
        cases = [
            dict(good, args="-m desk.link reseal %TEMP%"),
            dict(good, args="-m desk.link reseal\r\ndel x"),
            dict(good, root=Path("C:/re%po")),
            dict(good, python=Path("C:/py\n/python.exe")),
            dict(good, config=Path("C:/repo/%APPDATA%/desk.env")),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(DeskError):
                task_script_body(case["root"], case["python"], case["args"], case["config"])
        self.assertIn("-m desk.link reseal", task_script_body(good["root"], good["python"], good["args"], good["config"]))


class InstallTests(unittest.TestCase):
    def _config(self, folder):
        return load_config(write_env(Path(folder), f"SEAL_PATH={(Path(folder) / 'seal.json').resolve()}\n"))

    def test_elevated_install_is_refused_without_force(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self._config(folder)
            with mock.patch.object(link_main, "is_admin", return_value=True), \
                 mock.patch.object(link_main, "install_task") as task, \
                 mock.patch.object(link_main, "ensure_binary") as binary:
                with self.assertRaises(DeskError) as caught:
                    link_main.install(config)
            self.assertIn("not an elevated", str(caught.exception))
            task.assert_not_called()
            binary.assert_not_called()

    def test_elevated_install_with_force_warns_and_installs_as_user(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self._config(folder)
            err, out = io.StringIO(), io.StringIO()
            with mock.patch.object(link_main, "is_admin", return_value=True), \
                 mock.patch.object(link_main, "install_task") as task, \
                 mock.patch.object(link_main, "ensure_binary"), \
                 mock.patch.object(link_main, "write_task_script", return_value=Path("reseal-task.cmd")) as script, \
                 redirect_stderr(err), redirect_stdout(out):
                self.assertEqual(link_main.install(config, force=True), 0)
            self.assertIn("WARNING", err.getvalue())
            task.assert_called_once_with(link_main.RESEAL_TASK, Path("reseal-task.cmd"), as_system=False)
            self.assertIn(f'--seal-path "{config.seal_path}"', script.call_args[0][1])

    def test_normal_install_has_no_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self._config(folder)
            err, out = io.StringIO(), io.StringIO()
            with mock.patch.object(link_main, "is_admin", return_value=False), \
                 mock.patch.object(link_main, "install_task") as task, \
                 mock.patch.object(link_main, "ensure_binary"), \
                 mock.patch.object(link_main, "write_task_script", return_value=Path("reseal-task.cmd")), \
                 redirect_stderr(err), redirect_stdout(out):
                self.assertEqual(link_main.install(config), 0)
            self.assertEqual(err.getvalue(), "")
            self.assertIn("No seal yet", out.getvalue())
            self.assertEqual(task.call_args.kwargs, {"as_system": False})


class SealPathTests(unittest.TestCase):
    def test_absolute_seal_path_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            target = (Path(folder) / "elsewhere" / "seal.json").resolve()
            config = load_config(write_env(Path(folder), f"SEAL_PATH={target}\n"))
            self.assertEqual(config.seal_path, target)

    def test_relative_seal_path_is_from_env_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            config = load_config(write_env(Path(folder), "SEAL_PATH=sub/seal.json\n"))
            self.assertEqual(config.seal_path, (Path(folder) / "sub" / "seal.json").resolve())

    def test_environment_variable_is_expanded(self):
        with tempfile.TemporaryDirectory() as folder:
            os.environ["DESK_TEST_SEAL_ROOT"] = folder
            try:
                reference = "%DESK_TEST_SEAL_ROOT%" if os.name == "nt" else "$DESK_TEST_SEAL_ROOT"
                path = resolve_seal_path(f"{reference}/seal.json", Path(folder))
            finally:
                del os.environ["DESK_TEST_SEAL_ROOT"]
            self.assertEqual(path, (Path(folder) / "seal.json").resolve())

    def test_seal_path_option_overrides_config(self):
        with tempfile.TemporaryDirectory() as folder:
            config = load_config(write_env(Path(folder)))
            pinned = (Path(folder) / "pinned.json").resolve()
            self.assertEqual(link_main.with_seal_path(config, pinned).seal_path, pinned)
            self.assertIs(link_main.with_seal_path(config, None), config)
            with self.assertRaises(DeskError):
                link_main.with_seal_path(config, Path("relative.json"))


class ResealTests(unittest.TestCase):
    def test_missing_seal_is_an_error_with_its_path(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = (Path(folder) / "none" / "seal.json").resolve()
            config = load_config(write_env(Path(folder), f"SEAL_PATH={missing}\n"))
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(run_reseal(config, FakeBeacon(), NoTimelock(), out, err), 1)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), f"Seal file is missing: {missing}. Run: py -3 -m desk.link seal\n")


if __name__ == "__main__":
    unittest.main()
