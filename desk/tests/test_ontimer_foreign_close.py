"""Receiver.mq5 OnTimer must always call NukeForeignsByMagic() (foreign-position closing).

MQL5 test scripts cannot open files outside MQL5\\Files, so this check lives in the desk
Python suite. It reads mql5/Experts/Receiver.mq5, takes the body of OnTimer with comments
and string literals blanked (tools/config_guard.py's own Src/decode), and fails if:
  (a) the call `NukeForeignsByMagic();` is missing;
  (b) any `return` comes before it, except the one approved EmergencyStop block below;
  (d) CheckEmergencyStop() does not add the foreign recount to `remaining` before ExpertRemove().
  (c) it is not a plain statement at OnTimer's top brace level (inside if/else/switch/loop,
      after a braceless if/else/for/while/do, a case label, or inside a ternary/argument).

Approved early return (STRICT, "all foreigners must be eliminated all the time"): while
EmergencyStop is on, OnTimer runs
    if(EmergencyStop) { NukeForeignsByMagic(); CheckEmergencyStop(); return; }
so foreign positions and orders are closed first, then this EA's own, and CheckEmergencyStop
keeps the EA attached while any foreign or own position/order remains. Only that exact block
(whitespace ignored) is allowed; the old block without NukeForeignsByMagic() fails.
"""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECEIVER = ROOT / "mql5" / "Experts" / "Receiver.mq5"
CALL = "NukeForeignsByMagic"
APPROVED_RETURN_BLOCK = "if(EmergencyStop){NukeForeignsByMagic();CheckEmergencyStop();return;}"


def _load_guard():
    spec = importlib.util.spec_from_file_location("_config_guard_for_tests", ROOT / "tools" / "config_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # stdlib only; main() is behind __name__ == "__main__"
    return module


GUARD = _load_guard()


def function_body(bare, name):
    """Text strictly between the outer braces of `void name()`, from comment/string-blanked source."""
    heads = list(re.finditer(r"\bvoid\s+%s\s*\(\s*(?:void\s*)?\)\s*\{" % name, bare))
    if len(heads) != 1:
        raise AssertionError("expected exactly one 'void %s()' definition, found %d" % (name, len(heads)))
    start = heads[0].end()
    depth = 1
    for j in range(start, len(bare)):
        if bare[j] == "{":
            depth += 1
        elif bare[j] == "}":
            depth -= 1
            if depth == 0:
                return bare[start:j]
    raise AssertionError("%s body has no closing brace" % name)


def ontimer_body(bare):
    return function_body(bare, "OnTimer")


def _depths(body):
    """Brace and paren depth before each character, relative to OnTimer's body."""
    braces, parens, b, p = [], [], 0, 0
    for ch in body:
        if ch == "}":
            b -= 1
        elif ch == ")":
            p -= 1
        braces.append(b)
        parens.append(p)
        if ch == "{":
            b += 1
        elif ch == "(":
            p += 1
    return braces, parens


def _top_level_blocks(body, braces):
    """(start, end) spans of top-level statements that end in a braced block, e.g. if(..){..}."""
    spans, stmt_start = [], 0
    for i, ch in enumerate(body):
        if braces[i] == 0 and ch == ";":
            stmt_start = i + 1
        if ch == "}" and braces[i] == 0:
            spans.append((stmt_start, i + 1))
            stmt_start = i + 1
    return spans


def problems(source_text):
    """List of problem codes ('a', 'b', 'c') with reasons; empty list means OK."""
    bare = GUARD.Src(GUARD.decode(source_text.encode("latin-1"))).bare
    body = ontimer_body(bare)
    braces, parens = _depths(body)
    calls = [m for m in re.finditer(r"\b%s\s*\(\s*\)\s*;" % CALL, body)]
    if not calls:
        return [("a", "OnTimer does not call %s();" % CALL)]

    good = None
    found = []
    for m in calls:
        i = m.start()
        if braces[i] != 0 or parens[i] != 0:
            found.append(("c", "%s(); is inside a block or parentheses, not at OnTimer's top level" % CALL))
            continue
        k = i - 1
        while k >= 0 and not (braces[k] == 0 and body[k] in ";{}"):
            k -= 1
        lead = body[k + 1:i].strip()
        if lead:
            found.append(("c", "%s(); is controlled by %r (conditional, label or ternary)" % (CALL, lead)))
            continue
        good = i
        break
    if good is None:
        return found

    approved = []
    for s, e in _top_level_blocks(body[:good], braces):
        if re.sub(r"\s+", "", body[s:e]) == APPROVED_RETURN_BLOCK:
            approved.append((s, e))
    for m in re.finditer(r"\breturn\b", body[:good]):
        if not any(s <= m.start() < e for s, e in approved):
            return [("b", "an early return (conditional or not) comes before %s();" % CALL)]
    return emergency_problems(bare)


def emergency_problems(bare):
    """(d): CheckEmergencyStop must add CountForeignByMagic() into `remaining` at its top level,
    before the `if(remaining > 0)` retry check and before ExpertRemove()."""
    body = function_body(bare, "CheckEmergencyStop")
    braces, parens = _depths(body)
    adds = [m for m in re.finditer(r"\bremaining\s*(?:\+=\s*CountForeignByMagic\s*\(\s*\)"
                                   r"|=\s*remaining\s*\+\s*CountForeignByMagic\s*\(\s*\))\s*;", body)
            if braces[m.start()] == 0 and parens[m.start()] == 0]
    if not adds:
        return [("d", "CheckEmergencyStop does not add CountForeignByMagic() to remaining at its top level")]
    first_add = adds[0].start()
    k = first_add - 1
    while k >= 0 and not (braces[k] == 0 and body[k] in ";{}"):
        k -= 1
    if body[k + 1:first_add].strip():
        return [("d", "the foreign recount in CheckEmergencyStop is conditional")]
    remove = re.search(r"\bExpertRemove\s*\(", body)
    check = re.search(r"\bif\s*\(\s*remaining\s*>\s*0\s*\)", body)
    if remove is None or check is None:
        return [("d", "CheckEmergencyStop no longer has the if(remaining > 0) retry and ExpertRemove()")]
    if not first_add < check.start() < remove.start():
        return [("d", "the foreign recount must come before if(remaining > 0) and ExpertRemove()")]
    # Only the leading `if(!EmergencyStop) return;` guard may return before the recount.
    lead_guard = re.match(r"\s*if\s*\(\s*!\s*EmergencyStop\s*\)\s*return\s*;", body)
    allowed_end = lead_guard.end() if lead_guard else 0
    if re.search(r"\breturn\b", body[allowed_end:first_add]):
        return [("d", "CheckEmergencyStop can return before the foreign recount")]
    return []


STRICT_EMERGENCY = """void CheckEmergencyStop()
{
   if(!EmergencyStop) return;
   int remaining = 0;
   remaining += CountForeignByMagic();
   if(remaining > 0)
   {
      return;
   }
   ExpertRemove();
}
"""


def wrap(body, emergency=STRICT_EMERGENCY):
    return "int x;\nvoid Other()\n{\n   return;\n}\n\n%s\nvoid OnTimer()\n{\n%s\n}\n" % (emergency, body)


class ReceiverOnTimerForeignCloseTests(unittest.TestCase):
    def assertCode(self, body, code):
        found = problems(wrap(body))
        self.assertTrue(found, "expected a failure (%s) for:\n%s" % (code, body))
        self.assertEqual(found[0][0], code, found)

    def test_current_receiver_ontimer_calls_foreign_close(self):
        text = RECEIVER.read_bytes().decode("latin-1")
        self.assertEqual(problems(text), [])

    def test_good_synthetic_with_approved_emergency_block(self):
        body = ("   Stamp();\n   if(EmergencyStop)\n   {\n      NukeForeignsByMagic();\n      CheckEmergencyStop(); // closes own\n"
                "      return;\n   }\n   Refresh();\n   NukeForeignsByMagic();\n   if(x) return;\n")
        self.assertEqual(problems(wrap(body)), [])

    # (a) missing
    def test_a_missing_call(self):
        self.assertCode("   Refresh();\n   MaintainPositions();\n", "a")

    def test_a_commented_out_call(self):
        self.assertCode("   // NukeForeignsByMagic();\n   /* NukeForeignsByMagic(); */\n", "a")

    def test_a_call_only_inside_string(self):
        self.assertCode('   Print("NukeForeignsByMagic();");\n', "a")

    def test_a_differently_named_function(self):
        self.assertCode("   NukeForeignsByMagicLater();\n", "a")

    # (b) early return before the call
    def test_b_unconditional_return_before(self):
        self.assertCode("   Refresh();\n   return;\n   NukeForeignsByMagic();\n", "b")

    def test_b_braceless_conditional_return_before(self):
        self.assertCode("   if(!Ready()) return;\n   NukeForeignsByMagic();\n", "b")

    def test_b_braced_conditional_return_before(self):
        self.assertCode("   if(Busy())\n   {\n      Log();\n      return;\n   }\n   NukeForeignsByMagic();\n", "b")

    def test_b_emergency_block_with_extra_statement_is_not_approved(self):
        self.assertCode("   if(EmergencyStop)\n   {\n      NukeForeignsByMagic();\n      CheckEmergencyStop();\n      Other();\n      return;\n   }\n"
                        "   NukeForeignsByMagic();\n", "b")

    def test_b_emergency_block_with_else_return(self):
        self.assertCode("   if(EmergencyStop) { NukeForeignsByMagic(); CheckEmergencyStop(); return; }\n   else { return; }\n"
                        "   NukeForeignsByMagic();\n", "b")

    def test_b_old_emergency_block_without_nuke_now_fails(self):
        self.assertCode("   if(EmergencyStop)\n   {\n      CheckEmergencyStop();\n      return;\n   }\n"
                        "   NukeForeignsByMagic();\n", "b")

    def test_b_nuke_after_check_emergency_stop_fails(self):
        self.assertCode("   if(EmergencyStop)\n   {\n      CheckEmergencyStop();\n      NukeForeignsByMagic();\n"
                        "      return;\n   }\n   NukeForeignsByMagic();\n", "b")

    # (d) CheckEmergencyStop recounts foreigners before removing the EA
    def assertEmergency(self, emergency, code):
        good = "   if(EmergencyStop) { NukeForeignsByMagic(); CheckEmergencyStop(); return; }\n   NukeForeignsByMagic();\n"
        found = problems(wrap(good, emergency))
        if code is None:
            self.assertEqual(found, [])
        else:
            self.assertTrue(found, "expected (%s) for:\n%s" % (code, emergency))
            self.assertEqual(found[0][0], code, found)

    def test_d_strict_emergency_passes(self):
        self.assertEmergency(STRICT_EMERGENCY, None)

    def test_d_missing_recount(self):
        self.assertEmergency(STRICT_EMERGENCY.replace("   remaining += CountForeignByMagic();\n", ""), "d")

    def test_d_commented_recount(self):
        self.assertEmergency(STRICT_EMERGENCY.replace("   remaining +=", "   // remaining +="), "d")

    def test_d_recount_after_retry_check(self):
        moved = STRICT_EMERGENCY.replace("   remaining += CountForeignByMagic();\n", "")
        moved = moved.replace("   ExpertRemove();", "   remaining += CountForeignByMagic();\n   ExpertRemove();")
        self.assertEmergency(moved, "d")

    def test_d_conditional_recount(self):
        self.assertEmergency(STRICT_EMERGENCY.replace("   remaining +=", "   if(y) remaining +="), "d")

    def test_d_recount_inside_block(self):
        self.assertEmergency(STRICT_EMERGENCY.replace("   remaining += CountForeignByMagic();",
                                                      "   if(y)\n   {\n      remaining += CountForeignByMagic();\n   }"), "d")

    def test_d_counted_but_not_added(self):
        self.assertEmergency(STRICT_EMERGENCY.replace("remaining += CountForeignByMagic();",
                                                      "int foreign = CountForeignByMagic();"), "d")

    def test_d_early_return_before_recount(self):
        self.assertEmergency(STRICT_EMERGENCY.replace("   int remaining = 0;\n", "   int remaining = 0;\n   if(z) return;\n"), "d")

    def test_d_assignment_form_accepted(self):
        self.assertEmergency(STRICT_EMERGENCY.replace("remaining += CountForeignByMagic();",
                                                      "remaining = remaining + CountForeignByMagic();"), None)

    def test_current_repo_style_without_strict_fails(self):
        old = ("   if(EmergencyStop)\n   {\n      CheckEmergencyStop();\n      return;\n   }\n   NukeForeignsByMagic();\n")
        self.assertTrue(problems(wrap(old, STRICT_EMERGENCY.replace("   remaining += CountForeignByMagic();\n", ""))))

    # (c) not at the top brace level
    def test_c_braceless_if(self):
        self.assertCode("   if(x)\n      NukeForeignsByMagic();\n", "c")

    def test_c_braced_if(self):
        self.assertCode("   if(x)\n   {\n      NukeForeignsByMagic();\n   }\n", "c")

    def test_c_else_branch(self):
        self.assertCode("   if(x) { Refresh(); }\n   else NukeForeignsByMagic();\n", "c")

    def test_c_loop(self):
        self.assertCode("   for(int i = 0; i < n; i++)\n      NukeForeignsByMagic();\n", "c")

    def test_c_while_block(self):
        self.assertCode("   while(Again())\n   {\n      NukeForeignsByMagic();\n   }\n", "c")

    def test_c_switch_case(self):
        self.assertCode("   switch(mode)\n   {\n      case 1: NukeForeignsByMagic(); break;\n   }\n", "c")

    def test_c_ternary(self):
        self.assertCode("   bool r = x ? Skip() : NukeForeignsByMagic();\n", "c")

    def test_c_bare_nested_block(self):
        self.assertCode("   {\n      NukeForeignsByMagic();\n   }\n", "c")


if __name__ == "__main__":
    unittest.main()
