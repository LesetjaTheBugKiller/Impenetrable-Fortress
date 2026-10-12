"""Tests for the RDP port fix: TCP and UDP, real listener port, sticky capped block list, this-run allow list."""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from desk.keeper import __main__ as keeper_main
from desk.keeper import firewall
from desk.shared.config import load_config
from desk.shared.errors import DeskError

WINDOW = "TIMEZONE=Europe/Berlin\nOPEN_DAY=Friday\nOPEN_TIME=22:00\nCLOSE_DAY=Sunday\nCLOSE_TIME=22:00\n"


def write_env(folder: Path, extra: str = "") -> Path:
    path = folder / "desk.env"
    path.write_text(WINDOW + extra, encoding="utf-8")
    return path


def broken():
    raise OSError("no key")


def show_output(name: str, ports: str) -> str:
    return (
        f"\nRule Name:                            {name}\n"
        "----------------------------------------------------------------------\n"
        "Enabled:                              Yes\n"
        "Direction:                            In\n"
        "Protocol:                             TCP\n"
        f"LocalPort:                            {ports}\n"
        "RemotePort:                           Any\n"
        "Action:                               Block\n\nOk.\n"
    )


class Recorder:
    """Fake netsh. `existing` maps rule name to its LocalPort text."""

    def __init__(self, existing=None, fail_on=None):
        self.existing = dict(existing or {})
        self.fail_on = fail_on
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        name = next((part[5:] for part in command if part.startswith("name=")), "")
        if self.fail_on and self.fail_on(command):
            return subprocess.CompletedProcess(command, 1, "", "failed")
        if command[3] == "show":
            if name in self.existing:
                return subprocess.CompletedProcess(command, 0, show_output(name, self.existing[name]), "")
            return subprocess.CompletedProcess(command, 1, "No rules match the specified criteria.", "")
        if command[3] == "add":
            port = next(p[10:] for p in command if p.startswith("localport="))
            self.existing[name] = port
        return subprocess.CompletedProcess(command, 0, "Ok.", "")

    def sets(self):
        return [c for c in self.calls if c[3] == "set"]


def changes(run):
    return [c for c in run.calls if c[3] != "show"]


def ports_of(run, base):
    """localport= of the last set for base-TCP."""
    sets = [c for c in run.sets() if f"name={base}-TCP" in c]
    return next(p[10:] for p in sets[-1] if p.startswith("localport="))


def config_with(extra: str):
    with tempfile.TemporaryDirectory() as folder:
        return load_config(write_env(Path(folder), extra))


class RdpPortConfigTests(unittest.TestCase):
    def test_rdp_port_unset_or_empty(self):
        for extra in ("", "RDP_PORT=\n"):
            with self.subTest(extra=extra):
                config = config_with(extra)
                self.assertEqual(config.rdp_port, 3389)
                self.assertFalse(config.rdp_port_set)
                self.assertFalse(config.rdp_port_force)

    def test_rdp_port_and_force_set(self):
        config = config_with("RDP_PORT=40123\nRDP_PORT_FORCE=1\n")
        self.assertEqual(config.rdp_port, 40123)
        self.assertTrue(config.rdp_port_set)
        self.assertTrue(config.rdp_port_force)


class PortCheckTests(unittest.TestCase):
    def test_refused_ports(self):
        for port in (22, 80, 135, 139, 443, 445, 1023, 5985, 5986, 8080, 0, 70000):
            with self.subTest(port=port):
                self.assertIsNotNone(firewall.port_refusal(port))

    def test_accepted_ports(self):
        for port in (3389, 1024, 40123, 65535):
            with self.subTest(port=port):
                self.assertIsNone(firewall.port_refusal(port))


class PlanTests(unittest.TestCase):
    def test_allow_is_this_runs_ports_and_block_adds_3389(self):
        plan = firewall.plan_ports(40124, read=lambda: 40123)
        self.assertEqual(plan.allow, (40123, 40124))
        self.assertEqual(plan.block, (3389, 40123, 40124))

    def test_listener_on_3389_only(self):
        plan = firewall.plan_ports(None, read=lambda: 3389)
        self.assertEqual((plan.block, plan.allow), ((3389,), (3389,)))

    def test_unreadable_registry_allows_3389_and_warns(self):
        warnings = []
        plan = firewall.plan_ports(None, read=broken, warn=warnings.append)
        self.assertEqual((plan.block, plan.allow), ((3389,), (3389,)))
        self.assertIn("could not read the Remote Desktop listener port", warnings[0])

    def test_unreadable_registry_with_rdp_port(self):
        warnings = []
        plan = firewall.plan_ports(40123, read=broken, warn=warnings.append)
        self.assertEqual(plan.allow, (3389, 40123))
        self.assertEqual(plan.block, (3389, 40123))
        self.assertEqual(len(warnings), 1)

    def test_readable_registry_does_not_warn(self):
        warnings = []
        firewall.plan_ports(None, read=lambda: 40123, warn=warnings.append)
        self.assertEqual(warnings, [])

    def test_rdp_port_445_is_refused_without_force(self):
        with self.assertRaises(DeskError) as caught:
            firewall.plan_ports(445, read=lambda: 3389)
        self.assertIn("RDP_PORT=445 refused", str(caught.exception))
        self.assertIn("RDP_PORT_FORCE=1", str(caught.exception))
        plan = firewall.plan_ports(445, force=True, read=lambda: 3389)
        self.assertEqual(plan.allow, (445, 3389))

    def test_reserved_listener_port_is_blocked_but_not_allowed(self):
        warnings = []
        plan = firewall.plan_ports(None, read=lambda: 5985, warn=warnings.append)
        self.assertNotIn(5985, plan.allow)
        self.assertIn(5985, plan.block)
        self.assertEqual(plan.allow, (3389,))
        self.assertIn("listener port 5985 is not opened", warnings[0])
        self.assertIn(5985, firewall.plan_ports(None, force=True, read=lambda: 5985).allow)

    def test_block_never_shrinks_but_allow_does(self):
        plan = firewall.plan_ports(None, read=lambda: 3389, current={3389, 40123})
        self.assertEqual(plan.block, (3389, 40123))
        self.assertEqual(plan.allow, (3389,))

    def test_port_number_changed_without_restart(self):
        plan = firewall.plan_ports(None, read=lambda: 50000, current={3389, 40123})
        self.assertEqual(plan.block, (3389, 40123, 50000))
        self.assertEqual(plan.allow, (50000,))

    def test_once_wrong_443_later_fixed(self):
        # 443 was forced in once, so the rules cover it. RDP_PORT is now fixed to 40123.
        plan = firewall.plan_ports(40123, read=lambda: 40123, current={3389, 443})
        self.assertNotIn(443, plan.allow)
        self.assertIn(443, plan.block)
        self.assertEqual(plan.block, (443, 3389, 40123))
        # Never present before: 443 does not appear at all.
        self.assertNotIn(443, firewall.plan_ports(40123, read=lambda: 40123, current={3389}).block)
        # The cap still holds: 443 kept plus three more ports is five.
        with self.assertRaises(DeskError):
            firewall.plan_ports(40124, read=lambda: 40125, current={3389, 443, 40123})

    def test_more_than_four_block_ports_fails(self):
        with self.assertRaises(DeskError) as caught:
            firewall.plan_ports(40124, read=lambda: 40125, current={3389, 40122, 40123})
        self.assertIn("limit is 4", str(caught.exception))
        self.assertIn("reset-ports", str(caught.exception))
        self.assertEqual(len(firewall.plan_ports(40124, read=lambda: 40123, current={3389, 40122}).block), 4)


class CurrentRulePortTests(unittest.TestCase):
    def test_reads_ports_from_new_and_legacy_rules(self):
        run = Recorder({f"{firewall.BLOCK}-TCP": "3389,40123", firewall.BLOCK: "3389", f"{firewall.ALLOW}-UDP": "40123"})
        self.assertEqual(firewall.current_rule_ports(run), {3389, 40123})

    def test_no_rules_means_no_ports_and_no_warning(self):
        warnings = []
        self.assertEqual(firewall.current_rule_ports(Recorder(), warn=warnings.append), set())
        self.assertEqual(warnings, [])

    def test_unparsable_rule_warns(self):
        warnings = []
        run = Recorder({f"{firewall.BLOCK}-TCP": "Any"})
        self.assertEqual(firewall.current_rule_ports(run, warn=warnings.append), set())
        self.assertIn(f"{firewall.BLOCK}-TCP", warnings[0])
        self.assertIn("Keeping 3389", warnings[0])

    def test_parse_in_other_languages(self):
        samples = {
            "LocalPort:                            3389,40123\nRemotePort:   Any\n": {3389, 40123},
            "Lokaler Port:                         3389\nRemoteport:   Beliebig\n": {3389},
            "Port local :                          3389, 50000\n": {3389, 50000},
            "\u041b\u043e\u043a\u0430\u043b\u044c\u043d\u044b\u0439 \u043f\u043e\u0440\u0442:   3390\n": {3390},
        }
        for text, expected in samples.items():
            with self.subTest(text=text):
                self.assertEqual(firewall.parse_local_ports(text), expected)

    def test_parse_ignores_ranges_words_and_other_numbers(self):
        self.assertEqual(firewall.parse_local_ports("LocalPort: 5000-5010\nProtocol: 6\nRemotePort: RPC\n"), set())
        self.assertEqual(firewall.parse_local_ports(show_output("x", "3389,40123")), {3389, 40123})


class FirewallTests(unittest.TestCase):
    def test_closed_window_blocks_tcp_and_udp_on_every_port(self):
        run = Recorder()
        firewall.apply_firewall(False, run, ports=(3389, 40123))
        added = [c for c in run.calls if c[3] == "add"]
        self.assertEqual(
            sorted(next(p for p in c if p.startswith("name=")) for c in added),
            sorted(f"name={base}-{proto}" for base in (firewall.ALLOW, firewall.BLOCK) for proto in ("TCP", "UDP")),
        )
        for command in added + run.sets():
            self.assertIn("localport=3389,40123", command)
        block_sets = [c for c in run.sets() if f"name={firewall.BLOCK}-TCP" in c or f"name={firewall.BLOCK}-UDP" in c]
        self.assertEqual(len(block_sets), 2)
        for command in block_sets:
            self.assertIn("enable=yes", command)

    def test_allow_rules_use_allow_ports_only(self):
        run = Recorder()
        firewall.apply_firewall(True, run, ports=(443, 3389, 40123), allow_ports=(40123,))
        self.assertEqual(ports_of(run, firewall.BLOCK), "443,3389,40123")
        self.assertEqual(ports_of(run, firewall.ALLOW), "40123")

    def test_3389_is_always_in_the_block_rules(self):
        run = Recorder()
        firewall.apply_firewall(False, run, ports=(40123,), allow_ports=(40123,))
        self.assertEqual(ports_of(run, firewall.BLOCK), "3389,40123")
        self.assertEqual(ports_of(run, firewall.ALLOW), "40123")

    def test_open_window_disables_block_and_keeps_allow(self):
        run = Recorder()
        firewall.apply_firewall(True, run, ports=(40123,))
        for command in run.sets():
            if any(part.startswith(f"name={firewall.BLOCK}-") for part in command):
                self.assertIn("enable=no", command)
            else:
                self.assertIn("enable=yes", command)

    def test_block_rules_are_set_before_allow_rules(self):
        run = Recorder()
        firewall.apply_firewall(False, run, ports=(3389,))
        names = [next(p for p in c if p.startswith("name=")) for c in run.sets()]
        self.assertEqual(names[:2], [f"name={firewall.BLOCK}-TCP", f"name={firewall.BLOCK}-UDP"])

    def test_legacy_block_rule_follows_the_block_state_and_is_never_deleted(self):
        for open_now, state in ((False, "yes"), (True, "no")):
            with self.subTest(open_now=open_now):
                run = Recorder({firewall.ALLOW: "3389", firewall.BLOCK: "3389"})
                firewall.apply_firewall(open_now, run, ports=(3389,))
                self.assertIn(firewall.set_enabled(firewall.BLOCK, state), run.calls)
                self.assertFalse([c for c in run.calls if c[3] == "delete"])
                self.assertFalse([c for c in run.sets() if f"name={firewall.ALLOW}" in c])

    def test_no_legacy_rule_means_no_legacy_change(self):
        run = Recorder()
        firewall.apply_firewall(False, run, ports=(3389,))
        self.assertNotIn(firewall.set_enabled(firewall.BLOCK, "yes"), run.calls)

    def test_failed_block_update_raises_and_stops(self):
        run = Recorder(fail_on=lambda c: c[3] == "set" and f"name={firewall.BLOCK}-TCP" in c)
        with self.assertRaises(DeskError):
            firewall.apply_firewall(False, run, ports=(3389,))
        self.assertFalse([c for c in run.sets() if f"name={firewall.ALLOW}-TCP" in c])

    def test_failed_legacy_update_raises(self):
        run = Recorder({firewall.BLOCK: "3389"}, fail_on=lambda c: c == firewall.set_enabled(firewall.BLOCK, "yes"))
        with self.assertRaises(DeskError):
            firewall.apply_firewall(False, run, ports=(3389,))


SATURDAY = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)   # inside the Friday-Sunday window
WEDNESDAY = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)  # outside


class FakeTools:
    """Fake icacls for the folder lock-down. Any other tool call fails the test."""

    def __init__(self):
        self.calls = []
        self.grant_code = 0

    def __call__(self, command):
        self.calls.append(command)
        if command[0] == "icacls" and "/inheritance:r" in command:
            return subprocess.CompletedProcess(command, self.grant_code, "processed file", "")
        raise AssertionError(f"unexpected tool {command}")

    def secured(self, folder):
        return [
            "icacls", str(folder), "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
            "/remove:g", "*S-1-5-32-545", "*S-1-5-11", "*S-1-1-0", "*S-1-3-0",
        ] in self.calls


class KeeperTests(unittest.TestCase):
    """keeper commands with a fake netsh, fake registry, fixed clock, temp status file, fake Event Log."""

    ALL_RULES = {
        **{f"{b}-{p}": "3389,40121,40122" for b in (firewall.BLOCK, firewall.ALLOW) for p in ("TCP", "UDP")},
        firewall.BLOCK: "3389",
        firewall.ALLOW: "3389",
    }
    SIMPLE_RULES = {f"{b}-{p}": "3389" for b in (firewall.BLOCK, firewall.ALLOW) for p in ("TCP", "UDP")}

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.desk = self.folder / "WeekendDesk"
        self.desk.mkdir()
        self.status_file = self.desk / "keeper-status.json"
        self.open_file = self.desk / "open-now.json"
        self.events = []
        self.collected = []
        self.tools = FakeTools()

    def keeper(self, existing, listener, now, fail_on=None, admin=True):
        """now=None: the window clock throws (UTC time for the status file still works)."""
        run = Recorder(existing, fail_on=fail_on)
        real_datetime = datetime

        class Clock(real_datetime):
            @classmethod
            def now(cls, tz=None):
                if now is None:
                    if tz is timezone.utc:
                        return real_datetime.now(timezone.utc)
                    raise RuntimeError("no clock")
                return now.astimezone(tz)

        self.patches = [
            mock.patch.object(firewall, "run_command", run),
            mock.patch.object(firewall, "read_listener_port", listener),
            mock.patch.object(keeper_main, "datetime", Clock),
            mock.patch.object(keeper_main, "is_admin", lambda: admin),
            mock.patch.object(keeper_main.rdplog, "collect_if_enabled", self.collected.append),
            mock.patch.object(keeper_main, "desk_folder", lambda: self.desk),
            mock.patch.object(keeper_main, "run_tool", self.tools),
            mock.patch.object(keeper_main, "report_event", self.events.append),
        ]
        return run

    def call(self, *argv):
        err, out = io.StringIO(), io.StringIO()
        for patch in self.patches:
            patch.start()
        try:
            with redirect_stderr(err), redirect_stdout(out):
                result = keeper_main.main(list(argv))
        finally:
            for patch in reversed(self.patches):
                patch.stop()
        return result, out.getvalue(), err.getvalue()

    def env(self, extra: str) -> str:
        folder = Path(tempfile.mkdtemp(dir=self.folder))
        return str(write_env(folder, extra))

    def status(self) -> dict:
        import json

        return json.loads(self.status_file.read_text(encoding="utf-8"))

    def enabled_states(self, run):
        """name -> last enable= value from plain set_enabled calls."""
        states = {}
        for command in run.sets():
            if len(command) == 8:
                states[command[5][5:]] = command[7][7:]
        return states

    def assert_failed_closed(self, run):
        self.assertEqual(
            self.enabled_states(run),
            {
                f"{firewall.BLOCK}-TCP": "yes", f"{firewall.BLOCK}-UDP": "yes", firewall.BLOCK: "yes",
                f"{firewall.ALLOW}-TCP": "no", f"{firewall.ALLOW}-UDP": "no", firewall.ALLOW: "no",
            },
        )

    def assert_only_enable_sets_after_failure(self, run, start=0):
        # fail_closed uses only `set rule name=X new enable=`: no add, no localport=.
        for command in changes(run)[start:]:
            self.assertEqual(command[3], "set")
            self.assertEqual(len(command), 8)

    def assert_error_recorded(self, text):
        self.assertTrue(self.status_file.is_file())
        status = self.status()
        self.assertFalse(status["ok"])
        self.assertIn(text, status["error"])
        self.assertEqual(len(self.events), 1)
        self.assertIn(text, self.events[0])

    # ----- normal runs

    def test_run_keeps_old_block_port_but_allows_only_this_runs_ports(self):
        rules = {f"{b}-{p}": "443,3389" for b in (firewall.BLOCK, firewall.ALLOW) for p in ("TCP", "UDP")}
        run = self.keeper(rules, lambda: 40123, SATURDAY)
        code, _out, _err = self.call("run", "--config", self.env("RDP_PORT=40123\n"))
        self.assertEqual(code, 0)
        self.assertEqual(ports_of(run, firewall.BLOCK), "443,3389,40123")
        self.assertEqual(ports_of(run, firewall.ALLOW), "40123")

    def test_successful_run_collects_the_rdp_log_and_writes_ok_status(self):
        self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY)
        code, _out, _err = self.call("run", "--scheduled", "--config", self.env(""))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.collected), 1)
        self.assertTrue(self.status()["ok"])
        self.assertEqual(self.events, [])

    # ----- port plan errors

    def test_cap_error_outside_the_window_fails_closed(self):
        run = self.keeper(self.ALL_RULES, lambda: 40123, WEDNESDAY)
        code, _out, err = self.call("run", "--config", self.env("RDP_PORT=40124\n"))
        self.assertEqual(code, 1)
        self.assertIn("ALERT", err)
        self.assertIn("limit is 4", err)
        self.assert_failed_closed(run)
        self.assert_only_enable_sets_after_failure(run)
        self.assertEqual(self.collected, [])
        self.assert_error_recorded("limit is 4")
        self.assertEqual(self.status()["window"], "closed")
        self.assertEqual(self.status()["rules"][f"{firewall.BLOCK}-TCP"], "enabled")

    def test_port_error_outside_the_window_fails_closed(self):
        run = self.keeper(self.ALL_RULES, lambda: 3389, WEDNESDAY)
        code, _out, err = self.call("run", "--config", self.env("RDP_PORT=445\n"))
        self.assertEqual(code, 1)
        self.assertIn("RDP_PORT=445 refused", err)
        self.assert_failed_closed(run)
        self.assertEqual(self.collected, [])

    def test_cap_error_inside_the_window_leaves_rules_alone(self):
        run = self.keeper(self.ALL_RULES, lambda: 40123, SATURDAY)
        code, _out, err = self.call("run", "--config", self.env("RDP_PORT=40124\n"))
        self.assertEqual(code, 1)
        self.assertIn("left exactly as they are", err)
        self.assertEqual(changes(run), [])
        self.assertEqual(self.collected, [])
        self.assert_error_recorded("limit is 4")

    def test_port_error_inside_the_window_leaves_rules_alone(self):
        run = self.keeper(self.ALL_RULES, lambda: 3389, SATURDAY)
        code, _out, _err = self.call("run", "--config", self.env("RDP_PORT=445\n"))
        self.assertEqual(code, 1)
        self.assertEqual(changes(run), [])

    # ----- apply_firewall errors

    def test_netsh_rejects_port_update_just_after_window_close(self):
        run = self.keeper(
            self.SIMPLE_RULES, lambda: 3389, WEDNESDAY,
            fail_on=lambda c: c[3] == "set" and any(p.startswith("localport=") for p in c),
        )
        code, _out, err = self.call("run", "--scheduled", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn(f"Could not set firewall rule {firewall.BLOCK}-TCP", err)
        self.assertIn("localport=3389", err)
        states = self.enabled_states(run)
        self.assertEqual(states[f"{firewall.BLOCK}-TCP"], "yes")
        self.assertEqual(states[f"{firewall.BLOCK}-UDP"], "yes")
        self.assertEqual(states[f"{firewall.ALLOW}-TCP"], "no")
        self.assertEqual(states[f"{firewall.ALLOW}-UDP"], "no")
        self.assertEqual(self.collected, [])
        self.assert_error_recorded(f"{firewall.BLOCK}-TCP")

    def test_firewall_error_inside_the_window_changes_nothing_more(self):
        run = self.keeper(
            self.SIMPLE_RULES, lambda: 3389, SATURDAY,
            fail_on=lambda c: c[3] == "set" and any(p.startswith("localport=") for p in c),
        )
        code, _out, err = self.call("run", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn("left exactly as they are", err)
        # Only the one rejected set was attempted.
        self.assertEqual(len(changes(run)), 1)

    # ----- unknown window

    def test_unknown_window_with_cap_error_fails_closed(self):
        run = self.keeper(self.ALL_RULES, lambda: 40123, None)
        code, _out, err = self.call("run", "--config", self.env("RDP_PORT=40124\n"))
        self.assertEqual(code, 1)
        self.assertIn("Treating it as closed", err)
        self.assert_failed_closed(run)
        self.assertEqual(self.collected, [])

    def test_unknown_window_without_port_error_fails_closed(self):
        run = self.keeper(self.ALL_RULES, lambda: 3389, None)
        code, _out, err = self.call("run", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn("could not work out whether the window is open", err)
        self.assert_failed_closed(run)
        self.assert_only_enable_sets_after_failure(run)
        self.assertEqual(self.status()["window"], "unknown")

    def test_broken_desk_env_fails_closed(self):
        run = self.keeper(self.ALL_RULES, lambda: 3389, WEDNESDAY)
        broken = self.folder / "broken.env"
        broken.write_text("TIMEZONE=Not/AZone\nOPEN_DAY=Funday\n", encoding="utf-8")
        code, _out, err = self.call("run", "--config", str(broken))
        self.assertEqual(code, 1)
        self.assertIn("could not load", err)
        self.assert_failed_closed(run)
        self.assert_error_recorded("could not load")

    def test_missing_desk_env_fails_closed(self):
        run = self.keeper(self.ALL_RULES, lambda: 3389, SATURDAY)
        code, _out, _err = self.call("run", "--config", str(self.folder / "nope.env"))
        self.assertEqual(code, 1)
        self.assert_failed_closed(run)

    def test_unexpected_exception_before_window_check_fails_closed(self):
        run = self.keeper(self.ALL_RULES, lambda: 3389, SATURDAY)
        self.patches.append(mock.patch.object(keeper_main, "load_config", side_effect=RuntimeError("boom")))
        code, _out, err = self.call("run", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn("boom", err)
        self.assert_failed_closed(run)

    # ----- fail_closed details

    def test_netsh_failure_during_fail_closed_keeps_going(self):
        failing = f"name={firewall.BLOCK}-TCP"
        run = self.keeper(self.ALL_RULES, lambda: 40123, WEDNESDAY, fail_on=lambda c: c[3] == "set" and failing in c)
        code, _out, err = self.call("run", "--config", self.env("RDP_PORT=40124\n"))
        self.assertEqual(code, 1)
        self.assertIn(f"{firewall.BLOCK}-TCP: set failed", err)
        states = self.enabled_states(run)
        self.assertIn(firewall.set_enabled(f"{firewall.BLOCK}-TCP", "yes"), run.calls)
        self.assertEqual(states[f"{firewall.BLOCK}-UDP"], "yes")
        self.assertEqual(states[firewall.BLOCK], "yes")
        self.assertEqual(states[f"{firewall.ALLOW}-TCP"], "no")
        self.assertNotIn("NO block rule was confirmed enabled", err)
        self.assertEqual(self.collected, [])

    def test_alert_when_no_block_rule_is_confirmed(self):
        run = self.keeper(self.ALL_RULES, lambda: 40123, WEDNESDAY,
                          fail_on=lambda c: c[3] == "set" and "enable=yes" in c)
        code, _out, err = self.call("run", "--config", self.env("RDP_PORT=40124\n"))
        self.assertEqual(code, 1)
        self.assertIn("NO block rule was confirmed enabled", err)
        self.assertIn("NO block rule was confirmed enabled", self.events[0])

    def test_fail_closed_tells_missing_from_show_failed(self):
        calls = []

        def run(command):
            calls.append(command)
            name = command[5][5:]
            if command[3] == "show":
                if name == f"{firewall.BLOCK}-UDP":
                    raise OSError("netsh missing")
                if name == firewall.BLOCK:
                    return subprocess.CompletedProcess(command, 1, "Es gibt keine Regeln", "")  # not English
                if name in (f"{firewall.BLOCK}-TCP", f"{firewall.ALLOW}-TCP"):
                    return subprocess.CompletedProcess(command, 0, "Ok.", "")
                return subprocess.CompletedProcess(command, 1, "No rules match the specified criteria.", "")
            if name == firewall.BLOCK:
                return subprocess.CompletedProcess(command, 1, "Es gibt keine Regeln", "")
            return subprocess.CompletedProcess(command, 0, "Ok.", "")

        report = firewall.fail_closed(run)
        self.assertEqual(report.rules[f"{firewall.BLOCK}-TCP"], "enabled")
        self.assertEqual(report.rules[f"{firewall.BLOCK}-UDP"], "enabled")  # show failed, set still tried and worked
        self.assertTrue(report.rules[firewall.BLOCK].startswith("show failed"))
        self.assertEqual(report.rules[f"{firewall.ALLOW}-TCP"], "disabled")
        self.assertEqual(report.rules[f"{firewall.ALLOW}-UDP"], "missing")
        self.assertEqual(report.rules[firewall.ALLOW], "missing")
        self.assertEqual(report.failed, [firewall.BLOCK])
        self.assertFalse(report.new_block_missing)
        self.assertTrue(all(c[3] in ("show", "set") for c in calls))

    def test_rule_state_relies_on_exit_code(self):
        cp = subprocess.CompletedProcess
        self.assertEqual(firewall.rule_state(cp([], 0, "Regelname: x", "")), firewall.PRESENT)
        self.assertEqual(firewall.rule_state(cp([], 1, "Keine Regeln", "")), firewall.UNKNOWN)
        self.assertEqual(firewall.rule_state(cp([], 1, "No rules match the specified criteria.", "")), firewall.MISSING)
        self.assertEqual(firewall.rule_state(cp([], 0, "No rules match the specified criteria.", "")), firewall.MISSING)

    def test_event_log_failure_is_ignored(self):
        run = self.keeper(self.ALL_RULES, lambda: 3389, WEDNESDAY)
        self.patches[-1] = mock.patch.object(keeper_main, "report_event", side_effect=OSError("no eventcreate"))
        code, _out, _err = self.call("run", "--config", self.env("RDP_PORT=445\n"))
        self.assertEqual(code, 1)
        self.assert_failed_closed(run)
        self.assertFalse(self.status()["ok"])

    def test_report_event_never_raises(self):
        with mock.patch.object(keeper_main.subprocess, "run", side_effect=OSError("no eventcreate")):
            keeper_main.report_event('quote " and\nnewline')
        with mock.patch.object(keeper_main.subprocess, "run") as fake:
            keeper_main.report_event('a "b"\nc')
        command = fake.call_args[0][0]
        self.assertEqual(command[:9], ["eventcreate", "/L", "APPLICATION", "/T", "ERROR", "/SO", "WeekendDesk", "/ID", "900"])
        self.assertEqual(command[-1], "a 'b' c")

    def test_status_write_failure_is_only_a_warning(self):
        self.keeper(self.ALL_RULES, lambda: 3389, WEDNESDAY)
        self.desk = self.folder / "file-not-dir"
        self.desk.write_text("x")
        code, _out, err = self.call("run", "--config", self.env("RDP_PORT=445\n"))
        self.assertEqual(code, 1)
        self.assertIn("WARNING: could not write", err)
        self.assertEqual(len(self.events), 1)

    # ----- fresh VPS

    def test_fresh_vps_scheduled_run_creates_block_rules(self):
        run = self.keeper({}, lambda: 40123, WEDNESDAY)
        code, _out, err = self.call("run", "--scheduled", "--config", self.env("RDP_PORT=445\n"))
        self.assertEqual(code, 1)
        added = [c for c in run.calls if c[3] == "add"]
        self.assertEqual(sorted(c[5] for c in added), [f"name={firewall.BLOCK}-TCP", f"name={firewall.BLOCK}-UDP"])
        for command in added:
            self.assertIn("action=block", command)
            self.assertIn("enable=yes", command)
            self.assertIn("localport=3389,40123", command)
        self.assertIn("created", err)
        self.assertEqual(self.status()["created"][f"{firewall.BLOCK}-TCP"], "created")

    def test_fresh_vps_refused_listener_port_is_not_used_for_new_rules(self):
        run = self.keeper({}, lambda: 445, WEDNESDAY)
        self.call("run", "--scheduled", "--config", self.env("RDP_PORT=445\n"))
        for command in [c for c in run.calls if c[3] == "add"]:
            self.assertIn("localport=3389", command)

    def test_fresh_vps_interactive_run_only_alerts(self):
        run = self.keeper({}, lambda: 40123, WEDNESDAY)
        code, _out, err = self.call("run", "--config", self.env("RDP_PORT=445\n"))
        self.assertEqual(code, 1)
        self.assertEqual([c for c in run.calls if c[3] == "add"], [])
        self.assertIn("no WeekendDeskLock-TCP/-UDP rules exist", err)
        self.assertIn("NO block rule was confirmed enabled", err)

    def test_fresh_vps_inside_the_window_creates_nothing(self):
        run = self.keeper({}, lambda: 40123, SATURDAY)
        code, _out, _err = self.call("run", "--scheduled", "--config", self.env("RDP_PORT=445\n"))
        self.assertEqual(code, 1)
        self.assertEqual(changes(run), [])

    # ----- open-now is gone

    def write_leftover_open_now(self, until):
        import json

        self.open_file.write_text(json.dumps({"open_until": until.isoformat()}), encoding="utf-8")

    def test_open_now_command_no_longer_exists(self):
        run = self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY)
        with self.assertRaises(SystemExit) as raised:
            self.call("open-now", "--minutes", "30", "--config", self.env(""))
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(changes(run), [])

    def test_leftover_open_now_file_is_ignored_outside_the_window(self):
        from datetime import timedelta

        self.write_leftover_open_now(WEDNESDAY + timedelta(minutes=30))
        run = self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY)
        code, out, err = self.call("run", "--scheduled", "--config", self.env(""))
        self.assertEqual(code, 0)
        self.assertNotIn("open-now", out + err)
        self.assertIn("enable=yes", [c for c in run.sets() if f"name={firewall.BLOCK}-TCP" in c][-1])
        self.assertNotIn("open_now_until", self.status())
        # Only the folder lock-down ran: no ACL or owner check of the file.
        self.assertEqual([c for c in self.tools.calls if "/inheritance:r" not in c], [])

    def test_leftover_open_now_file_never_opens_an_unknown_window(self):
        from datetime import timedelta

        self.write_leftover_open_now(datetime.now(timezone.utc) + timedelta(minutes=30))
        run = self.keeper(self.SIMPLE_RULES, lambda: 3389, None)
        code, _out, err = self.call("run", "--scheduled", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn("could not work out whether the window is open", err)
        self.assertIn("enable=yes", [c for c in run.sets() if f"name={firewall.BLOCK}-TCP" in c][-1])
        self.assertIn("enable=no", [c for c in run.sets() if f"name={firewall.ALLOW}-TCP" in c][-1])

    def test_status_file_is_never_read_for_decisions(self):
        import json
        from datetime import timedelta

        self.status_file.write_text(json.dumps({"open_until": (WEDNESDAY + timedelta(minutes=30)).isoformat()}))
        run = self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY)
        code, _out, _err = self.call("run", "--scheduled", "--config", self.env(""))
        self.assertEqual(code, 0)
        self.assertIn("enable=yes", [c for c in run.sets() if f"name={firewall.BLOCK}-TCP" in c][-1])

    def test_every_run_locks_down_the_folder(self):
        self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY)
        self.call("run", "--scheduled", "--config", self.env(""))
        self.assertTrue(self.tools.secured(self.desk))

    def test_folder_lockdown_failure_in_run_is_a_warning(self):
        self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY)
        self.tools.grant_code = 5
        code, _out, err = self.call("run", "--scheduled", "--config", self.env(""))
        self.assertEqual(code, 0)
        self.assertIn("WARNING: Could not lock down", err)

    # ----- duplicate rules

    def test_unknown_show_failure_never_adds_a_rule_and_fails_closed_outside(self):
        run = self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY,
                          fail_on=lambda c: c[3] == "show" and c[-1] == f"name={firewall.BLOCK}-TCP")
        code, _out, err = self.call("run", "--scheduled", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn(f"could not check firewall rule {firewall.BLOCK}-TCP", err)
        self.assertIn("Not adding it", err)
        self.assertEqual([c for c in run.calls if c[3] == "add"], [])
        self.assertEqual(self.enabled_states(run)[f"{firewall.BLOCK}-TCP"], "yes")
        self.assertEqual(self.enabled_states(run)[f"{firewall.ALLOW}-TCP"], "no")

    def test_unknown_show_failure_inside_the_window_changes_nothing(self):
        run = self.keeper(self.SIMPLE_RULES, lambda: 3389, SATURDAY,
                          fail_on=lambda c: c[3] == "show" and c[-1] == f"name={firewall.BLOCK}-TCP")
        code, _out, err = self.call("run", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn("Not adding it", err)
        self.assertEqual(changes(run), [])

    def test_ensure_adds_only_when_clearly_missing(self):
        cp = subprocess.CompletedProcess
        calls = []

        def run(command):
            calls.append(command)
            return cp(command, 1, "Keine Regeln", "")

        with self.assertRaises(DeskError):
            firewall._ensure("X", ["netsh", "advfirewall", "firewall", "add", "rule", "name=X"], run)
        self.assertEqual([c for c in calls if c[3] == "add"], [])

    # ----- install

    def install_patches(self):
        self.script_args = []
        self.patches += [
            mock.patch.object(keeper_main, "enable_remote_desktop_service", lambda _run=None: None),
            mock.patch.object(keeper_main, "install_task", lambda *_a, **_k: None),
            mock.patch.object(keeper_main, "write_task_script",
                              lambda name, args, source: self.script_args.append(args) or Path(name)),
        ]

    def test_install_outside_the_window_is_refused_without_force(self):
        run = self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY)
        self.install_patches()
        code, _out, err = self.call("install", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn("The window is closed", err)
        self.assertIn("--force", err)
        self.assertEqual(changes(run), [])
        self.assertEqual(self.script_args, [])

    def test_install_outside_the_window_with_force(self):
        self.keeper(self.SIMPLE_RULES, lambda: 3389, WEDNESDAY)
        self.install_patches()
        code, _out, _err = self.call("install", "--force", "--config", self.env(""))
        self.assertEqual(code, 0)
        self.assertEqual(self.script_args, ["-m desk.keeper run --scheduled"])

    def test_install_inside_the_window_uses_scheduled_flag(self):
        self.keeper(self.SIMPLE_RULES, lambda: 3389, SATURDAY)
        self.install_patches()
        code, _out, _err = self.call("install", "--config", self.env(""))
        self.assertEqual(code, 0)
        self.assertEqual(self.script_args, ["-m desk.keeper run --scheduled"])
        self.assertTrue(self.tools.secured(self.desk))

    def test_install_refused_when_the_folder_cannot_be_locked_down(self):
        run = self.keeper(self.SIMPLE_RULES, lambda: 3389, SATURDAY)
        self.install_patches()
        self.tools.grant_code = 5
        code, _out, err = self.call("install", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn("Could not lock down", err)
        self.assertEqual(changes(run), [])

    # ----- reset-ports

    def test_reset_ports_inside_the_window_shrinks_both_lists(self):
        rules = {f"{b}-{p}": "443,3389,40121,40122" for b in (firewall.BLOCK, firewall.ALLOW) for p in ("TCP", "UDP")}
        run = self.keeper(rules, lambda: 40123, SATURDAY)
        code, out, _err = self.call("reset-ports", "--config", self.env(""))
        self.assertEqual(code, 0)
        self.assertEqual(ports_of(run, firewall.BLOCK), "3389,40123")
        self.assertEqual(ports_of(run, firewall.ALLOW), "40123")
        self.assertIn("Ports reset", out)

    def test_reset_ports_outside_the_window_is_refused(self):
        run = self.keeper({f"{firewall.BLOCK}-TCP": "443,3389"}, lambda: 40123, WEDNESDAY)
        code, _out, err = self.call("reset-ports", "--config", self.env(""))
        self.assertEqual(code, 1)
        self.assertIn("only inside the open window", err)
        self.assertEqual(changes(run), [])

    def test_reset_ports_outside_the_window_with_force(self):
        rules = {f"{b}-{p}": "443,3389" for b in (firewall.BLOCK, firewall.ALLOW) for p in ("TCP", "UDP")}
        run = self.keeper(rules, lambda: 40123, WEDNESDAY)
        code, _out, _err = self.call("reset-ports", "--force", "--config", self.env(""))
        self.assertEqual(code, 0)
        self.assertEqual(ports_of(run, firewall.BLOCK), "3389,40123")
        self.assertIn("enable=yes", [c for c in run.sets() if f"name={firewall.BLOCK}-TCP" in c][-1])


if __name__ == "__main__":
    unittest.main()
