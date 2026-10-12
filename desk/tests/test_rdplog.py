import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from desk.keeper import rdplog
from desk.shared.errors import DeskError

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
NS = 'xmlns="http://schemas.microsoft.com/win/2004/08/events/event"'


def security_event(record, event_id=4624, logon_type="10", user="micca", ip="198.51.100.7", at="2026-10-10T20:01:00.000Z"):
    return (
        f"<Event {NS}><System><EventID>{event_id}</EventID><EventRecordID>{record}</EventRecordID>"
        f'<TimeCreated SystemTime="{at}"/><Channel>Security</Channel></System><EventData>'
        f'<Data Name="TargetUserName">{user}</Data><Data Name="TargetDomainName">VPS</Data>'
        f'<Data Name="LogonType">{logon_type}</Data><Data Name="IpAddress">{ip}</Data></EventData></Event>'
    )


def session_event(record, event_id=21, at="2026-10-10T20:01:05.000Z"):
    return (
        f"<Event {NS}><System><EventID>{event_id}</EventID><EventRecordID>{record}</EventRecordID>"
        f'<TimeCreated SystemTime="{at}"/></System><UserData>'
        '<EventXML xmlns="Event_NS"><User>VPS\\micca</User><SessionID>2</SessionID>'
        "<Address>198.51.100.7</Address></EventXML></UserData></Event>"
    )


class FakeLogs:
    """Stands in for wevtutil. Returns events newer than the asked-for record."""

    def __init__(self):
        self.events = {channel: [] for channel in rdplog.SOURCES}
        self.queries = []

    def __call__(self, channel, xpath):
        self.queries.append((channel, xpath))
        after = 0
        if "EventRecordID>" in xpath:
            after = int(xpath.split("EventRecordID>")[1].split("]")[0])
        return "".join(xml for record, xml in self.events[channel] if record > after)

    def add(self, channel, record, xml):
        self.events[channel].append((record, xml))


def event(n):
    return {"channel": "Security", "record": n, "event_id": 4624, "kind": "logon", "user": "micca", "ip": "198.51.100.7", "time": f"2026-10-10T20:0{n}:00Z"}


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "rdp-log.jsonl"
        rdplog.append(self.path, KEY, [event(n) for n in range(1, 6)])
        self.lines = rdplog.read_lines(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def kinds(self, lines, key=KEY, anchors=()):
        _checked, problems = rdplog.verify(lines, key, list(anchors))
        return [(problem.seq, problem.kind) for problem in problems]

    def test_valid_chain_verifies(self):
        checked, problems = rdplog.verify(self.lines, KEY)
        self.assertEqual(checked, 5)
        self.assertEqual(problems, [])

    def test_edited_entry_is_flagged(self):
        entry = json.loads(self.lines[2])
        entry["ip"] = "203.0.113.99"
        lines = list(self.lines)
        lines[2] = json.dumps(entry)
        self.assertEqual(self.kinds(lines), [(3, "EDITED")])

    def test_deleted_entry_is_flagged(self):
        lines = self.lines[:2] + self.lines[3:]
        self.assertEqual(self.kinds(lines), [(4, "MISSING"), (4, "BROKEN LINK")])

    def test_reordered_entries_are_flagged(self):
        lines = list(self.lines)
        lines[1], lines[2] = lines[2], lines[1]
        found = self.kinds(lines)
        self.assertIn((3, "MISSING"), found)
        self.assertIn((2, "OUT OF ORDER"), found)
        self.assertIn((3, "BROKEN LINK"), found)
        self.assertIn((4, "BROKEN LINK"), found)
        self.assertNotIn("EDITED", [kind for _seq, kind in found])

    def test_wrong_key_is_flagged(self):
        found = self.kinds(self.lines, key=OTHER_KEY)
        self.assertEqual(found, [(n, "WRONG KEY") for n in range(1, 6)])

    def test_forged_entry_with_the_right_key_id_is_flagged(self):
        forged = rdplog.seal_entry(OTHER_KEY, event(9), 6, json.loads(self.lines[-1])["mac"])
        forged["kid"] = rdplog.key_id(KEY)
        self.assertEqual(self.kinds(self.lines + [json.dumps(forged)]), [(6, "EDITED")])

    def test_injected_garbage_is_flagged(self):
        self.assertEqual(self.kinds(self.lines[:2] + ["not json"] + self.lines[2:]), [("?", "UNREADABLE")])

    def test_empty_log(self):
        self.assertEqual(rdplog.verify([], KEY), (0, []))
        self.assertEqual(rdplog.tail(Path(self.folder.name) / "none.jsonl"), (0, rdplog.GENESIS))

    def test_anchor_catches_a_rewritten_chain(self):
        anchor = rdplog.parse_anchor(f"5:{json.loads(self.lines[-1])['mac']}")
        self.assertEqual(self.kinds(self.lines, anchors=[anchor]), [])
        rewritten = Path(self.folder.name) / "rewritten.jsonl"
        rdplog.append(rewritten, KEY, [event(n) for n in (1, 2, 4, 5, 6)])
        self.assertEqual(self.kinds(rdplog.read_lines(rewritten), anchors=[anchor]), [(5, "ANCHOR DIFF")])

    def test_anchor_catches_a_cut_off_tail(self):
        anchor = rdplog.parse_anchor(f"5:{json.loads(self.lines[-1])['mac']}")
        self.assertEqual(self.kinds(self.lines[:3], anchors=[anchor]), [(5, "ANCHOR LOST")])

    def test_bad_anchor_text_is_refused(self):
        with self.assertRaises(DeskError):
            rdplog.parse_anchor("5:nothex")

    def test_appending_continues_the_chain(self):
        rdplog.append(self.path, KEY, [event(6)])
        checked, problems = rdplog.verify(rdplog.read_lines(self.path), KEY)
        self.assertEqual((checked, problems), (6, []))


class CollectTests(unittest.TestCase):
    def test_collects_rdp_events_once_and_skips_other_logons(self):
        logs = FakeLogs()
        logs.add(rdplog.SECURITY, 100, security_event(100))
        logs.add(rdplog.SECURITY, 101, security_event(101, logon_type="2"))
        logs.add(rdplog.SECURITY, 102, security_event(102, event_id=4625, logon_type="3", user="admin", ip="203.0.113.5"))
        logs.add(rdplog.LOCAL_SESSION, 7, session_event(7))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rdp-log.jsonl"
            self.assertEqual(rdplog.collect(path, KEY, logs), 3)
            self.assertEqual(rdplog.collect(path, KEY, logs), 0)
            logs.add(rdplog.LOCAL_SESSION, 8, session_event(8, event_id=24, at="2026-10-10T21:00:00.000Z"))
            self.assertEqual(rdplog.collect(path, KEY, logs), 1)
            entries = [json.loads(line) for line in rdplog.read_lines(path)]
            self.assertEqual([e["kind"] for e in entries], ["logon", "logon_failed", "session_logon", "session_disconnected"])
            self.assertEqual(entries[0]["user"], "micca")
            self.assertEqual(entries[0]["ip"], "198.51.100.7")
            self.assertEqual(entries[2]["user"], "VPS\\micca")
            self.assertEqual(rdplog.verify(rdplog.read_lines(path), KEY), (4, []))
        self.assertIn(("Security", "*[System[(EventID=4624 or EventID=4625) and EventRecordID>102]]"), logs.queries)

    def test_first_run_looks_back_a_week(self):
        self.assertIn("timediff(@SystemTime)", rdplog.xpath_for(rdplog.SECURITY, 0))


class FakeKeys:
    def __init__(self, key=KEY):
        self.key = key

    def load(self):
        return self.key


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.settings = rdplog.Settings(True, root / "rdp-log.jsonl", root / "rdp-log.key")

    def tearDown(self):
        self.folder.cleanup()

    def run_action(self, action, anchors=(), key=KEY):
        out = StringIO()
        code = rdplog.run_action(action, self.settings, list(anchors), out, keys=FakeKeys(key), query=FakeLogs())
        return code, out.getvalue()

    def test_verify_exit_codes_and_output(self):
        self.assertEqual(self.run_action("verify")[0], 0)
        rdplog.append(self.settings.log_path, KEY, [event(1), event(2)])
        code, text = self.run_action("verify")
        self.assertEqual(code, 0)
        self.assertIn("OK: 2 entries, chain intact", text)
        lines = rdplog.read_lines(self.settings.log_path)
        self.settings.log_path.write_text(lines[1] + "\n", encoding="utf-8")
        code, text = self.run_action("verify")
        self.assertEqual(code, 2)
        self.assertIn("MISSING", text)
        self.assertIn("TAMPERED", text)

    def test_head_prints_an_anchor(self):
        rdplog.append(self.settings.log_path, KEY, [event(1)])
        code, text = self.run_action("head")
        self.assertEqual(code, 0)
        anchor = text.splitlines()[0]
        self.assertEqual(self.run_action("verify", [anchor])[0], 0)

    def test_key_file_round_trip_and_never_replaced(self):
        keys = rdplog.KeyFile(self.settings.key_path, protect=lambda b: b[::-1], unprotect=lambda b: b[::-1], restrict=lambda p: None)
        made = keys.create()
        self.assertEqual(keys.load(), made)
        self.assertNotIn(made, self.settings.key_path.read_bytes())
        with self.assertRaises(DeskError):
            keys.create()

    def test_missing_key_is_a_clear_error(self):
        with self.assertRaises(DeskError):
            rdplog.KeyFile(self.settings.key_path).load()

    def test_settings_default_off(self):
        env = Path(self.folder.name) / "desk.env"
        env.write_text("TIMEZONE=Europe/Berlin\n", encoding="utf-8")
        self.assertFalse(rdplog.load_settings(env).enabled)
        env.write_text("RDPLOG_ENABLED=yes\nRDPLOG_PATH=logs/rdp.jsonl\n", encoding="utf-8")
        settings = rdplog.load_settings(env)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.log_path, (Path(self.folder.name) / "logs" / "rdp.jsonl").resolve())

    def test_collect_if_enabled_never_raises(self):
        env = Path(self.folder.name) / "desk.env"
        env.write_text(f"RDPLOG_ENABLED=yes\nRDPLOG_KEY_PATH={self.settings.key_path}\n", encoding="utf-8")
        err = StringIO()
        rdplog.collect_if_enabled(env, err)
        self.assertIn("RDP log not collected", err.getvalue())


if __name__ == "__main__":
    unittest.main()
