import socket
import unittest
from pathlib import Path

from desk.shared.errors import DeskError
from desk.keeper.firewall import ALLOW, BLOCK, apply_firewall, set_enabled
from desk.link.probe import OPEN, REFUSED, SILENT, probe
from desk.shared.schedule import KEEPER_TASK, schtasks_create, task_script_body


class Result:
    def __init__(self, code=0, stdout="", stderr=""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


class FirewallTests(unittest.TestCase):
    def test_outside_the_window_the_block_rule_is_on(self):
        calls = []

        def run(command):
            calls.append(command)
            if command[:5] == ["netsh", "advfirewall", "firewall", "show", "rule"]:
                return Result(0, f"Rule Name: {command[-1]}")
            return Result(0, "Ok.")

        apply_firewall(False, run)
        self.assertIn(set_enabled(BLOCK, "yes"), calls)
        self.assertNotIn(set_enabled(BLOCK, "no"), calls)

    def test_inside_the_window_the_block_rule_is_off(self):
        calls = []

        def run(command):
            calls.append(command)
            if "show" in command:
                return Result(0, "Rule Name: present")
            return Result(0, "Ok.")

        apply_firewall(True, run)
        self.assertIn(set_enabled(BLOCK, "no"), calls)

    def test_missing_rules_are_created(self):
        calls = []

        def run(command):
            calls.append(command)
            if "show" in command:
                return Result(1, "No rules match the specified criteria.")
            return Result(0, "Ok.")

        apply_firewall(False, run)
        added = [" ".join(command) for command in calls if "add" in command]
        self.assertTrue(any(ALLOW in text and "action=allow" in text for text in added))
        self.assertTrue(any(BLOCK in text and "action=block" in text and "enable=yes" in text for text in added))

    def test_a_failed_change_is_reported(self):
        def run(command):
            if "show" in command:
                return Result(0, "Rule Name: present")
            return Result(1, "Access is denied.")

        with self.assertRaises(DeskError):
            apply_firewall(True, run)


class ScheduleTests(unittest.TestCase):
    def test_task_runs_every_minute_as_system(self):
        command = schtasks_create(KEEPER_TASK, Path(r"C:\Weekend\keeper.cmd"))
        self.assertIn("SYSTEM", command)
        self.assertIn("MINUTE", command)
        self.assertIn(KEEPER_TASK, command)

    def test_task_script_uses_this_python(self):
        body = task_script_body(
            Path(r"C:\Server"),
            Path(r"C:\Python\python.exe"),
            "-m desk.keeper run",
            Path(r"C:\Server\desk\desk.env"),
        )
        self.assertIn(r"C:\Python\python.exe", body)
        self.assertIn("-m desk.keeper run", body)
        self.assertIn(r"C:\Server\desk\desk.env", body)


class ProbeTests(unittest.TestCase):
    def test_a_listening_port_is_open(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            self.assertEqual(probe("127.0.0.1", port, 2), OPEN)
        finally:
            listener.close()

    def test_an_active_refusal_is_not_a_timeout(self):
        def refuse(_family, _sockaddr, _timeout):
            raise ConnectionRefusedError()

        def drop(_family, _sockaddr, _timeout):
            raise TimeoutError("timed out")

        self.assertEqual(probe("127.0.0.1", 9, 1, connect=refuse), REFUSED)
        self.assertEqual(probe("127.0.0.1", 9, 1, connect=drop), SILENT)

        def windows_reset(_family, _sockaddr, _timeout):
            error = OSError("refused")
            error.winerror = 10061
            raise error

        self.assertEqual(probe("127.0.0.1", 9, 1, connect=windows_reset), REFUSED)
