#!/usr/bin/env python3
"""config_guard.py - config, SEAL and tooling integrity guard for Impenetrable-Fortress (DRAFT v4.3).

Committed location: tools/config_guard.py. Lock file: config.lock.json at the repo root.
Stdlib only, Python 3.8+, Windows and Linux. It prints entry names, file:line and
PASS/FAIL/MISSING/DRIFT words only. It never prints file contents, values or hashes.

What is locked (config.lock.json, SHA-256 per entry)
  whole:<file>       The WHOLE text of the risk-bearing includes: ReceiverDecisions.mqh,
                     ReceiverOrderExecution.mqh, ReceiverPositionManagement.mqh,
                     ReceiverDailyState.mqh, plus any mql5/Include/*Limits.mqh or *Config.mqh
                     (ReceiverLimits.mqh, ReceiverConfig.mqh, SenderConfig.mqh) once they exist.
  inputs:<EA>        Every input/sinput statement of Receiver.mq5 / Sender.mq5 while the inputs
                     still live there (whole statement up to ';', comments removed, whitespace
                     collapsed, string defaults included).
  decls:<file>       For every other MQL5 file (Receiver.mq5, Sender.mq5, ReceiverProtocol.mqh,
                     ReceiverSignalWorkflow.mqh, tests): every global declaration with an
                     initializer, every enum body, every declaration containing `const`
                     anywhere (e.g. `double const X = ...`), every `static` with an initializer
                     anywhere (also inside functions), and every #define.
  lits:<file>        For every file NOT hashed whole: a fingerprint of EVERY numeric, string
                     and char literal in code, each tagged with its enclosing class/function
                     scope, a running index (e.g. CLim::CLim#0, OnTick#2), a unary sign when
                     there is one, the code token right BEFORE it and the code token right
                     AFTER it. A token is an operator run (`>`, `>=`, `-`, `=` ...), one
                     punctuation mark (`,` `;` `[` `{` `?` `:` ...), `return`/`case`, or `id`
                     for any other identifier (the NAME is not recorded). `(` before and `)`
                     after a literal are skipped, so redundant brackets and spacing do not
                     count. Caught: `dd > -5.0` -> `dd > 5.0`, `a - 5` -> `a + 5`,
                     `x > 3` -> `x < 3`, `5.0 < dd` -> `5.0 > dd`, `5.0 - y` -> `5.0 + y`,
                     and any literal value or text change (an OnTick constant, a constructor
                     default, `return 30.0`, a log message). Each needs a reviewed --init; that
                     false-positive cost is intended.
  risk:<file>#<fn>   The WHOLE body of every definition of each RISK_SELECTORS function.
  file:<path>        SEAL (desk/link/sealfile.py, desk/reseal.cmd, desk/shared/*.py),
                     .gitignore, and the tooling: tools/config_guard.py, deploy.cmd,
                     desk/run_all_tests.cmd, deploy/test-deploy.sh.
  tree:<dir>         Every file of the test directories (desk/tests, src/test,
                     mql5/Scripts/Tests, tests), path-sorted. __pycache__/*.pyc ignored.
  desk/desk.env and desk/bin/ are never read.

  Line endings: hashes are taken over the bytes with CRLF turned into LF (what git stores
  in the index), so a Windows checkout and a Linux CI checkout give the same lock. Files with
  NUL bytes (UTF-16) are hashed raw. Separately, EVERY *.cmd/*.bat/*.ps1 in the repo must be
  pure CRLF (cmd.exe misreads labels in LF files): a bare LF or a lone CR fails the check.

WHAT IS CAUGHT, BY FILE KIND
  whole files (the four risk includes, *Limits.mqh, *Config.mqh), SEAL and tooling files:
    any change except a pure CRLF<->LF conversion (also comments and whitespace).
  inputs/decls/risk regions: any change inside them except whitespace and comments.
  everything else in non-whole MQL5 files: ONLY literals and the tokens directly around them
    (see lits:). Comments, whitespace and redundant brackets never count there.

WHAT THE GUARD DOES NOT CATCH (non-whole files, outside decls/risk regions): logic edits that
touch no literal and no token directly next to one - e.g. `a > b` -> `a < b` between two
variables, `a > b && c` -> `a > b || c` (no literal next to the &&), MathMin -> MathMax,
changing which variable is passed (names are recorded only as `id`), deleting or reordering
a statement that has no literal, adding an early `return;`.
Only review (Vera) and the behaviour/integration tests catch those.

TRADE-OFF (deliberate): the four risk-bearing includes are hashed as whole files, so ANY edit
in them - also a pure logic fix or a comment - fails the guard until Lesetja reviews it and
re-runs --init --force. That closes the gaps a selector list cannot (helper functions called
from selected ones, globals, enums, statics). Use --diff-names to see exactly which entries
changed before re-initialising.

What fails (exit 1)
  - any hash differs, an entry is missing, or a new entry is not in the lock;
  - `input`/`sinput` anywhere in comment- and string-stripped code (after backslash line
    continuations are spliced, so `inp\\<newline>ut` is caught) outside the config sources and
    mql5/Scripts/Tests;
  - a #define that maps to input/sinput, or any ## token pasting;
  - a quoted #include that cannot be found, or an #include <...> that is neither in the repo
    nor on SYSTEM_INCLUDES (derived from the tree: Trade/Trade.mqh). Adding one is a reviewed
    edit of this (locked) file;
  - a // comment ending with a backslash, or a backslash line continuation anywhere outside
    a #define (MQL5 splicing rules are not documented; such lines are refused, not guessed);
  - any *.cmd/*.bat/*.ps1 in the repo (tracked or untracked, gitignored paths skipped) with a
    bare LF or a lone CR (must be pure CRLF).

Usage (Windows: py -3, Linux: python3)
  config_guard.py                      check the repo against the lock
  config_guard.py --live "<MQL5 dir>" [--live-roots Receiver,Sender]
                                       also compare the terminal's live copies of EVERY file
                                       in the include chain of the given EAs (default both;
                                       deploy.cmd passes Receiver only)
  config_guard.py --live <dir> --porcelain
                                       machine-readable: only non-OK lines, as
                                       "<STATUS> <repo-path> <entry>" (STATUS = FAIL, MISSING,
                                       DRIFT, MISSING-LIVE), then "RESULT <word>"
  config_guard.py --live <dir> --live-system
                                       also hash the allowlisted SYSTEM_INCLUDES the chain uses
                                       (e.g. Include/Trade/Trade.mqh, top level only) from the
                                       terminal and report DRIFT against sys: entries; the
                                       baseline is recorded by `--init --live <dir> --live-system`
                                       (terminal-specific; off by default; deploy.cmd does not use it)
  config_guard.py --print-chain [--live-roots ...]
                                       print the include chain ("chain <path>") and the include
                                       file names ("include <name>"); writes nothing
  config_guard.py --diff-names         print only the entry NAMES that differ from the lock
  config_guard.py --list               show what would be locked and scan results; writes nothing
  config_guard.py --changed-inputs <git-ref>
                                       print NAMES of inputs added/removed/changed since <ref>
                                       (multi-declarations like `input int A=1, B=2;` handled)
  config_guard.py --init [--force]     write config.lock.json (refuses to overwrite without --force)
  Options: --root <repo dir> (default: nearest parent with .git), --lock <file>
  Exit codes: 0 PASS, 1 FAIL (incl. MISSING-LIVE), 2 usage/lock error, 3 repo PASS but live drift.

--live checks SOURCE files in the terminal folder only (their input DEFAULTS and the locked
regions). It does NOT and cannot see the input values saved on the chart, in presets or in
the terminal profile. A file of the include chain missing from the terminal is MISSING-LIVE:
a hard failure, never drift.

--init and --init --force are REVIEWED steps, run by Lesetja only. --init refuses unless every
TOOL_FILES entry (.gitignore, tools/config_guard.py, deploy.cmd, desk/run_all_tests.cmd,
deploy/test-deploy.sh) and every SEAL file exists, so place the committed tooling first.
After the config, test or tooling change has been reviewed and approved, run --init (--force
to replace the lock), then commit config.lock.json on its own in a separate commit with a
`Config-Change-Reason:` trailer. Never re-init to make a failing check pass.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LOCK_VERSION = 4  # sys: entries (optional) do not change the format
PORCELAIN = False

EA_CONFIG = [  # (EA source, planned config include)
    ("mql5/Experts/Receiver.mq5", "mql5/Include/ReceiverConfig.mqh"),
    ("mql5/Experts/Sender.mq5", "mql5/Include/SenderConfig.mqh"),
]
EA_ROOTS = [ea for ea, _ in EA_CONFIG]
WHOLE_FILES = ["mql5/Include/ReceiverDecisions.mqh", "mql5/Include/ReceiverOrderExecution.mqh",
               "mql5/Include/ReceiverPositionManagement.mqh", "mql5/Include/ReceiverDailyState.mqh"]
WHOLE_PATTERNS = [re.compile(r"^mql5/include/[^/]*(limits|config)\.mqh$", re.I)]
SYSTEM_INCLUDES = {"trade/trade.mqh"}  # lower-case, forward slashes; derived from the tree
SEAL_FILES = ["desk/link/sealfile.py", "desk/reseal.cmd"]
SEAL_DIR_GLOBS = [("desk/shared", ".py")]
TOOL_FILES = [".gitignore", "tools/config_guard.py", "deploy.cmd",
              "desk/run_all_tests.cmd", "deploy/test-deploy.sh"]
TEST_TREES = ["desk/tests", "src/test", "mql5/Scripts/Tests", "tests"]  # locked if present
TREE_IGNORE_DIRS = {"__pycache__", ".pytest_cache"}
TREE_IGNORE_SUFFIXES = {".pyc", ".pyo", ".ex5", ".ex4"}  # compiled MetaTrader output, never committed
INPUT_ALLOWED_PREFIXES = ["mql5/scripts/tests/"]  # lower-case
CRLF_SUFFIXES = {".cmd", ".bat", ".ps1"}
NEVER_READ = ("desk/bin/", "desk/desk.env")  # SEAL secrets: never opened

RISK_SELECTORS = [  # (file, function); the whole function is hashed
    ("mql5/Experts/Receiver.mq5", "GetSafeStopPoints"),
    ("mql5/Experts/Receiver.mq5", "OnInit"),
    ("mql5/Experts/Receiver.mq5", "ResolveStopPoints"),
    ("mql5/Experts/Receiver.mq5", "CheckEmergencyStop"),
    ("mql5/Experts/Receiver.mq5", "OnTimer"),
    ("mql5/Experts/Receiver.mq5", "CountForeignByMagic"),
    ("mql5/Experts/Sender.mq5", "PlannedDayTargetPct"),
    ("mql5/Experts/Sender.mq5", "PaintDayBar"),
    ("mql5/Experts/Sender.mq5", "HandleButtonClick"),
    ("mql5/Include/ReceiverDailyState.mqh", "SpikeThresholdMoney"),
    ("mql5/Include/ReceiverDailyState.mqh", "LossCountTolerance"),
    ("mql5/Include/ReceiverDailyState.mqh", "RebuildDailyFactsFromHistory"),
    ("mql5/Include/ReceiverDecisions.mqh", "ReceiverBreakEvenTolerance"),
    ("mql5/Include/ReceiverDecisions.mqh", "ReceiverExpectedDayTargetPercent"),
    ("mql5/Include/ReceiverDecisions.mqh", "ReceiverScaleStopPoints"),
    ("mql5/Include/ReceiverDecisions.mqh", "ReceiverBackstopStopPrice"),
    ("mql5/Include/ReceiverDecisions.mqh", "ReceiverApplyMinimumStopPoints"),
    ("mql5/Include/ReceiverProtocol.mqh", "ReceiverSignalExpired"),
    ("mql5/Include/ReceiverOrderExecution.mqh", "SafeTradeExecute"),
    ("mql5/Include/ReceiverOrderExecution.mqh", "PlaceVisibleTakeProfit"),
    ("mql5/Include/ReceiverOrderExecution.mqh", "EnsureBackstop"),
    ("mql5/Include/ReceiverPositionManagement.mqh", "CloseIfWidened"),
    ("mql5/Include/ReceiverPositionManagement.mqh", "MaintainPositions"),
    ("mql5/Include/ReceiverPositionManagement.mqh", "NukeForeignsByMagic"),
]

LIVE_NAME_MAP = {  # repo path -> path under the terminal's MQL5 folder
    "mql5/Experts/Receiver.mq5": "Experts/receiver.mq5",
    "mql5/Experts/Sender.mq5": "Experts/Sender.mq5",
}

MQL5_DIR = "mql5"
MQL5_EXTS = {".mq5", ".mqh"}
KEYWORD_RE = re.compile(r"\b(s?input)\b")
INCLUDE_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*([<"])([^>"\n]+)[>"]', re.M | re.I)
PP_LINE_RE = re.compile(r"^[ \t]*#", re.M)
ASSIGN_RE = re.compile(r"(?<![=!<>+\-*/%&|^])=(?!=)")
NOT_A_TYPE = {"return", "else", "new", "delete", "case", "if", "while", "for", "switch"}


# ---------- bytes and text ----------

def norm_bytes(data):
    """CRLF -> LF unless the data has NUL bytes (UTF-16): matches git's LF index form."""
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_lines(lines):
    return sha_bytes("\n".join(lines).encode("utf-8"))


def hash_file(path):
    p = Path(path)
    return sha_bytes(norm_bytes(p.read_bytes())) if p.is_file() else None


def _utf16_nobom(data):
    sample = data[:4096]
    pairs = len(sample) // 2
    if pairs < 2:
        return None
    even = sum(1 for i in range(0, pairs * 2, 2) if sample[i] == 0)
    odd = sum(1 for i in range(1, pairs * 2, 2) if sample[i] == 0)
    if odd > 0.3 * pairs and even < 0.05 * pairs:
        return "utf-16-le"
    if even > 0.3 * pairs and odd < 0.05 * pairs:
        return "utf-16-be"
    return None


def decode(data):
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16", "replace")
    elif data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8", "replace")
    else:
        enc = _utf16_nobom(data)
        if enc:
            text = data.decode(enc, "replace")
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("cp1252", "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def norm(s):
    return " ".join(s.split())


class Src:
    """Source text with backslash-newline continuations spliced (like the C preprocessor).
    .text is spliced; .line(pos) maps a position back to the original line number."""

    def __init__(self, text):
        self.orig = text
        out, pos = [], []
        i, n = 0, len(text)
        while i < n:
            if text[i] == "\\" and i + 1 < n and text[i + 1] == "\n":
                i += 2
                continue
            out.append(text[i]); pos.append(i); i += 1
        self.text = "".join(out)
        self._pos = pos
        self.bare = strip_code(self.text, strings=True)    # comments + strings blanked
        self.keep = strip_code(self.text, strings=False)   # comments blanked, strings kept

    def line(self, p):
        o = self._pos[p] if p < len(self._pos) else len(self.orig)
        return self.orig.count("\n", 0, o) + 1


def load(path):
    return Src(decode(Path(path).read_bytes()))


def strip_code(text, strings=True):
    """Blank comments; with strings=True also string and char literals (same length)."""
    out, i, n = [], 0, len(text)
    while i < n:
        two = text[i:i + 2]
        c = text[i]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j])); i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(" " * (j - i) if strings else text[i:j]); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def line_start(text, pos):
    return text.rfind("\n", 0, pos) + 1


def line_end(text, pos):
    k = text.find("\n", pos)
    return len(text) if k < 0 else k


def pp_spans(src):
    """(start, end) of every preprocessor line (continuations already spliced)."""
    return [(m.start(), line_end(src.bare, m.start())) for m in PP_LINE_RE.finditer(src.bare)]


def blank_spans(text, spans):
    t = list(text)
    for a, b in spans:
        for k in range(a, b):
            if t[k] != "\n":
                t[k] = " "
    return "".join(t)


# ---------- MQL5 analysis ----------

def split_top_commas(s):
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _declarator_name(part):
    head = ASSIGN_RE.split(part, 1)[0]
    head = re.sub(r"\[[^\]]*\]\s*$", "", head.strip())
    ids = re.findall(r"\w+", head)
    return ids[-1] if ids else "?"


def input_statements(src):
    """[(line, [(name, per-name declaration)], whole normalized statement)]."""
    bare = blank_spans(src.bare, pp_spans(src))
    out, groups = [], 0
    for m in KEYWORD_RE.finditer(bare):
        end = bare.find(";", m.end())
        end = len(bare) if end < 0 else end + 1
        stmt = norm(src.keep[m.start():end])
        rest_keep = src.keep[m.end():end].rstrip().rstrip(";")
        rest_bare = bare[m.end():end].rstrip().rstrip(";")
        if norm(rest_bare).startswith("group"):
            groups += 1
            decls = [("group#%d" % groups, stmt)]
        else:
            # split on top-level commas using the bare text, apply cuts to the kept text
            cuts, depth = [], 0
            for k, ch in enumerate(rest_bare):
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "," and depth == 0:
                    cuts.append(k)
            pieces_b, pieces_k, a = [], [], 0
            for c in cuts + [len(rest_bare)]:
                pieces_b.append(rest_bare[a:c]); pieces_k.append(rest_keep[a:c]); a = c + 1
            first = pieces_b[0]
            name0 = _declarator_name(first)
            idx = first.rfind(name0)
            type_part = norm(first[:idx]) if idx > 0 else ""
            decls = [(name0, norm(m.group(1) + " " + pieces_k[0]))]
            for pb, pk in zip(pieces_b[1:], pieces_k[1:]):
                decls.append((_declarator_name(pb), norm("%s %s %s" % (m.group(1), type_part, pk))))
        out.append((src.line(m.start()), decls, stmt))
    return out


def preprocessor_problems(src):
    probs = []
    for a, b in pp_spans(src):
        body = src.bare[a:b]
        if not re.match(r"^\s*#\s*define\b", body):
            continue
        if KEYWORD_RE.search(body[body.find("define") + 6:]):
            probs.append((src.line(a), "#define maps to input/sinput"))
        if "##" in body:
            probs.append((src.line(a), "## token pasting in #define"))
    return probs


def declarations(src):
    """Normalized globals-with-initializer, enum bodies, const and static-with-initializer
    declarations (any depth) and #defines, in source order, each tagged with its kind."""
    items = []
    spans = pp_spans(src)
    for a, b in spans:
        if re.match(r"^\s*#\s*define\b", src.bare[a:b]):
            items.append((a, "D|" + norm(src.keep[a:b])))
    bare = blank_spans(src.bare, spans)
    depth, seg = 0, 0
    i, n = 0, len(bare)
    while i < n:
        ch = bare[i]
        if ch == "{":
            if depth == 0 and re.search(r"\benum\b", bare[seg:i]):
                d, j = 0, i
                while j < n:
                    if bare[j] == "{":
                        d += 1
                    elif bare[j] == "}":
                        d -= 1
                        if d == 0:
                            break
                    j += 1
                items.append((seg, "E|" + norm(src.keep[seg:j + 1])))
                i, seg = j + 1, j + 1
                continue
            depth += 1; seg = i + 1
        elif ch == "}":
            depth -= 1; seg = i + 1
        elif ch == ";":
            s = bare[seg:i + 1]
            ns = norm(s)
            has_assign = bool(ASSIGN_RE.search(s))
            kind = None
            if depth == 0 and has_assign and not KEYWORD_RE.match(ns):
                kind = "G"
            if has_assign and re.search(r"\bconst\b", s):
                kind = "C"
            if has_assign and re.search(r"\bstatic\b", s):
                kind = "S"
            if kind:
                items.append((seg, kind + "|" + norm(src.keep[seg:i + 1])))
            seg = i + 1
        i += 1
    return [t for _, t in sorted(items)]


def char_states(text):
    """Per-character state of UNspliced text: N code, L // comment, B /* */ comment, S string/char."""
    st, i, n, mode, q = [], 0, len(text), "N", None
    while i < n:
        c, two = text[i], text[i:i + 2]
        if mode == "N":
            if two == "//":
                mode = "L"; st += "LL"; i += 2; continue
            if two == "/*":
                mode = "B"; st += "BB"; i += 2; continue
            if c in "\"'":
                mode, q = "S", c; st.append("S"); i += 1; continue
            st.append("N"); i += 1
        elif mode == "L":
            if c == "\n":
                mode = "N"; st.append("N")
            else:
                st.append("L")
            i += 1
        elif mode == "B":
            if two == "*/":
                st += "BB"; i += 2; mode = "N"; continue
            st.append("B"); i += 1
        else:  # string
            if c == "\\" and i + 1 < n and text[i + 1] != "\n":
                st += "SS"; i += 2; continue
            st.append("S")
            if c == q or c == "\n":
                mode = "N"
            i += 1
    return st


def continuation_problems(src):
    """[(line, reason)] for // comments ending in a backslash and continuations outside #define."""
    text, out = src.orig, []
    st = char_states(text)
    lines = text.split("\n")
    pos, in_define, cont = 0, False, False
    for no, line in enumerate(lines, 1):
        if not cont:
            in_define = bool(re.match(r"^[ \t]*#[ \t]*define\b", line))
        stripped = line.rstrip(" \t")
        ends = stripped.endswith("\\")
        if ends:
            k = pos + len(stripped) - 1
            state = st[k] if k < len(st) else "N"
            if state == "L":
                out.append((no, "// comment ends with a backslash"))
            elif state != "B" and not in_define:
                out.append((no, "backslash line continuation outside #define"))
        cont = ends and (st[pos + len(stripped) - 1] if pos + len(stripped) - 1 < len(st) else "N") != "B"
        pos += len(line) + 1
    return out


NUM_RE = re.compile(r"(?<![\w.])(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)")


def _scope_name(header):
    """Name for a '{' from the text before it: class/struct/enum name or function name."""
    h = norm(header)
    m = re.search(r"\b(class|struct|enum|union)\s+(\w+)[^;]*$", h)
    if m and "(" not in h[m.end(2):]:
        return m.group(2)
    m = re.search(r"([\w:~]+)\s*\([^;]*\)\s*(?:const\s*)?(?::[^;{]*)?$", h)
    if m and m.group(1) not in NOT_A_TYPE and m.group(1) not in ("catch",):
        return m.group(1).split("::")[-1] if "::" not in m.group(1) else m.group(1)
    return None


OP_CHARS = "=<>!&|+-*/%^~?:"
PUNCT_CHARS = "(,[{;}"


def _prev_tok(bare, pos):
    """(token, start) of the code token before pos; identifiers become 'id' except return/case."""
    j = pos - 1
    while j >= 0 and bare[j] in " \t\n":
        j -= 1
    if j < 0:
        return "^", -1
    c = bare[j]
    if c in OP_CHARS:
        k = j
        while k - 1 >= 0 and bare[k - 1] in OP_CHARS:
            k -= 1
        return bare[k:j + 1], k
    if c.isalnum() or c == "_":
        k = j
        while k - 1 >= 0 and (bare[k - 1].isalnum() or bare[k - 1] == "_"):
            k -= 1
        w = bare[k:j + 1]
        return (w if w in ("return", "case") else "id"), k
    return c, j


def _op_and_sign(bare, pos):
    """(recorded operator, unary sign). Opening parentheses are transparent for the recorded
    operator (so `return 0` == `return (0)`), but count as context for the unary-sign test."""
    tok, tpos = _prev_tok(bare, pos)
    sign = ""
    if tok in ("-", "+"):
        before, bpos = _prev_tok(bare, tpos)
        unary = (before[-1:] in OP_CHARS or before in PUNCT_CHARS
                 or before in ("return", "case", "^"))
        if unary:
            sign, tok, tpos = tok, before, bpos
    elif len(tok) >= 2 and tok[-1] in "+-" and tok[-2] != tok[-1]:
        sign, tok = tok[-1], tok[:-1]   # e.g. ">-5" glued: operator ">" and sign "-"
    while tok == "(" and tpos >= 0:
        tok, tpos = _prev_tok(bare, tpos)
    return tok, sign


def _next_tok(bare, pos):
    """Code token starting at/after pos: an operator run, punctuation, 'id' or '$' (end).
    Closing parentheses are transparent, so `return 0;` == `return (0);`."""
    n = len(bare)
    j = pos
    while True:
        while j < n and bare[j] in " \t\n":
            j += 1
        if j < n and bare[j] == ")":
            j += 1
            continue
        break
    if j >= n:
        return "$"
    c = bare[j]
    if c in OP_CHARS:
        k = j
        while k < n and bare[k] in OP_CHARS:
            k += 1
        return bare[j:k]
    if c.isalnum() or c == "_":
        return "id"
    return c


def literals(src):
    """['scope#idx:literal'] for every numeric/string/char literal, in source order."""
    keep, bare = src.keep, src.bare
    toks = []
    for m in NUM_RE.finditer(bare):
        op, sign = _op_and_sign(bare, m.start())
        toks.append((m.start(), "%s|%s%s|%s" % (op, sign, m.group(0), _next_tok(bare, m.end()))))
    i, n = 0, len(keep)
    while i < n:  # string/char literals are where bare is blank but keep is not
        if keep[i] in "\"'" and bare[i] == " ":
            q, j = keep[i], i + 1
            while j < n and keep[j] != q and keep[j] != "\n":
                j += 2 if keep[j] == "\\" else 1
            prefix = keep[i - 1] if i > 0 and keep[i - 1] in "CD" and (i < 2 or not keep[i - 2].isalnum()) else ""
            op, _sign = _op_and_sign(bare, i - len(prefix))
            toks.append((i, "%s|%s|%s" % (op, prefix + keep[i:j + 1], _next_tok(bare, j + 1))))
            i = j + 1
            continue
        i += 1
    toks.sort()
    # scope of each position: walk braces
    events, stack, seg = [], [], 0
    scopes_at = []
    k = 0
    out, counters = [], {}
    tok_i = 0
    for pos, ch in enumerate(bare):
        while tok_i < len(toks) and toks[tok_i][0] == pos:
            names = [s for s in stack if s]
            scope = "::".join(names) if names else "<file>"
            idx = counters.get(scope, 0); counters[scope] = idx + 1
            out.append("%s#%d:%s" % (scope, idx, toks[tok_i][1]))
            tok_i += 1
        if ch == "{":
            stack.append(_scope_name(bare[seg:pos])); seg = pos + 1
        elif ch == "}":
            if stack:
                stack.pop()
            seg = pos + 1
        elif ch == ";":
            seg = pos + 1
    return out


def function_bodies(src, name):
    bare, keep = src.bare, src.keep
    header = re.compile(r"^[ \t]*([\w<>]+)[\w<>&*:\s]*?\b" + re.escape(name) + r"\s*\(", re.M)
    found = []
    for m in header.finditer(bare):
        if m.group(1) in NOT_A_TYPE:
            continue
        i, depth = m.end() - 1, 0
        while i < len(bare):
            if bare[i] == "(":
                depth += 1
            elif bare[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        i += 1
        while i < len(bare) and (bare[i].isspace() or bare[i:i + 5] == "const"):
            i += 5 if bare[i:i + 5] == "const" else 1
        if i >= len(bare) or bare[i] != "{":
            continue
        depth, j = 0, i
        while j < len(bare):
            if bare[j] == "{":
                depth += 1
            elif bare[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        found.extend(l for l in (norm(x) for x in keep[m.start():j + 1].split("\n")) if l)
        found.append("--")
    return found or None


# ---------- files and include chain ----------

def ci_resolve(base, rel):
    cur = Path(base)
    for part in re.split(r"[\\/]+", rel.strip()):
        if part in ("", "."):
            continue
        if part == "..":
            cur = cur.parent
            continue
        exact = cur / part
        if exact.exists():
            cur = exact
            continue
        if not cur.is_dir():
            return None
        match = [c for c in cur.iterdir() if c.name.lower() == part.lower()]
        if not match:
            return None
        cur = match[0]
    return cur if cur.is_file() else None


def relpath(root, p):
    return Path(p).resolve().relative_to(Path(root).resolve()).as_posix()


def includes_of(root, rel, p):
    """[(line, kind, target, resolved rel or None)] for one file."""
    src = load(p)
    base = root / MQL5_DIR
    out = []
    for m in INCLUDE_RE.finditer(src.keep):
        kind, target = m.group(1), m.group(2)
        if kind == '"':
            tp = ci_resolve(p.parent, target) or ci_resolve(base / "Include", target)
        else:
            tp = ci_resolve(base / "Include", target)
        trel = None
        if tp is not None:
            try:
                trel = relpath(root, tp)
            except ValueError:
                trel = "<outside>"
        out.append((src.line(m.start()), kind, target, trel))
    return out


def mql5_files(root):
    """({rel: Path} for all MQL5 sources + every reachable include target, [problems])."""
    base = root / MQL5_DIR
    files, problems = {}, []
    if base.is_dir():
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in MQL5_EXTS:
                files[relpath(root, p)] = p
    queue = list(files.items())
    while queue:
        rel, p = queue.pop()
        for line, kind, target, trel in includes_of(root, rel, p):
            if trel == "<outside>":
                problems.append((rel, line, "#include points outside the repo"))
            elif trel is None:
                sysname = re.sub(r"[\\/]+", "/", target.strip()).lower()
                if kind == '"':
                    problems.append((rel, line, "#include target not found"))
                elif sysname not in SYSTEM_INCLUDES:
                    problems.append((rel, line, "#include <...> not on SYSTEM_INCLUDES"))
            elif trel not in files:
                files[trel] = root / trel
                queue.append((trel, root / trel))
    return dict(sorted(files.items())), problems


def include_chain(root, roots=None):
    """Repo files reachable from the EA roots (roots included)."""
    roots = roots or EA_ROOTS
    seen, queue = set(), [r for r in roots if (root / r).is_file()]
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for _l, _k, _t, trel in includes_of(root, rel, root / rel):
            if trel and trel != "<outside>" and trel not in seen:
                queue.append(trel)
    return sorted(seen)


def is_whole(root, rel):
    if rel in WHOLE_FILES or any(p.match(rel) for p in WHOLE_PATTERNS):
        return True
    return any(split and rel == inc for _ea, inc, split in config_mode(root))


def config_mode(root):
    return [(ea, inc, (root / inc).is_file()) for ea, inc in EA_CONFIG]


def config_sources(root):
    return {(inc if split else ea).lower() for ea, inc, split in config_mode(root)}


def repo_files(root):
    """Tracked + untracked, not gitignored (git ls-files); falls back to a walk."""
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if r.returncode == 0:
        rels = [x for x in r.stdout.decode("utf-8", "replace").split("\0") if x]
    else:
        rels = []
        for p in root.rglob("*"):
            if p.is_file() and ".git" not in p.relative_to(root).parts:
                rels.append(p.relative_to(root).as_posix())
    return sorted(x for x in rels if not x.lower().startswith(NEVER_READ))


def eol_problems(root):
    out = []
    for rel in repo_files(root):
        p = root / rel
        if p.suffix.lower() in CRLF_SUFFIXES and p.is_file():
            data = p.read_bytes()
            crlf = data.count(b"\r\n")
            if data.count(b"\n") != crlf:
                out.append("%s: bare LF line ending(s) (must be CRLF)" % rel)
            if data.count(b"\r") != crlf:
                out.append("%s: lone CR line ending(s) (must be CRLF)" % rel)
    return out


def scan_problems(root):
    allowed = config_sources(root)
    files, problems = mql5_files(root)
    out = ["%s:%d %s" % p for p in problems]
    for rel, p in files.items():
        src = load(p)
        low = rel.lower()
        test_ok = any(low.startswith(px) for px in INPUT_ALLOWED_PREFIXES)
        if low not in allowed and not test_ok:
            for line, _d, _s in input_statements(src):
                out.append("%s:%d input outside config" % (rel, line))
        for line, reason in preprocessor_problems(src) + continuation_problems(src):
            out.append("%s:%d %s" % (rel, line, reason))
    return out + eol_problems(root)


# ---------- entries ----------

def tree_hash(d):
    d = Path(d)
    if not d.is_dir():
        return None
    parts = []
    for p in sorted(d.rglob("*"), key=lambda x: x.as_posix()):
        if not p.is_file() or p.suffix.lower() in TREE_IGNORE_SUFFIXES:
            continue
        if any(seg in TREE_IGNORE_DIRS for seg in p.relative_to(d).parts):
            continue
        parts.append(p.relative_to(d).as_posix() + "\0" + sha_bytes(norm_bytes(p.read_bytes())))
    return sha_lines(parts)


def file_entries(root, rel, path):
    """Entries for one MQL5 source (repo or live copy at `path`)."""
    e = {}
    if not Path(path).is_file():
        return None
    src = load(path)
    if is_whole(root, rel):
        e["whole:" + rel] = hash_file(path)
    else:
        items = declarations(src)
        if items:
            e["decls:" + rel] = sha_lines(items)
        lits = literals(src)
        if lits:
            e["lits:" + rel] = sha_lines(lits)
    for ea, inc, split in config_mode(root):
        if rel == ea and not split:
            st = [s for _l, _d, s in input_statements(src)]
            e["inputs:" + ea] = sha_lines(st) if st else None
    for frel, fn in RISK_SELECTORS:
        if frel == rel:
            body = function_bodies(src, fn)
            e["risk:%s#%s" % (rel, fn)] = sha_lines(body) if body else None
    return e


def compute_repo(root):
    files, _ = mql5_files(root)
    e = {}
    for rel, p in files.items():
        e.update(file_entries(root, rel, p) or {})
    for ea, inc, split in config_mode(root):  # make a missing EA visible
        if not (root / ea).is_file() and not split:
            e["inputs:" + ea] = None
    for rel, fn in RISK_SELECTORS:
        e.setdefault("risk:%s#%s" % (rel, fn), None)
    for rel in WHOLE_FILES:
        e.setdefault("whole:" + rel, None)
    for rel in SEAL_FILES + TOOL_FILES:
        e["file:" + rel] = hash_file(root / rel)
    for d, ext in SEAL_DIR_GLOBS:
        dd = root / d
        if dd.is_dir():
            for p in sorted(dd.iterdir()):
                if p.is_file() and p.suffix.lower() == ext:
                    e["file:" + relpath(root, p)] = hash_file(p)
    for d in TEST_TREES:
        if (root / d).is_dir():
            e["tree:" + d] = tree_hash(root / d)
    return dict(sorted(e.items()))


def to_live(rel):
    if rel in LIVE_NAME_MAP:
        return LIVE_NAME_MAP[rel]
    return rel[len("mql5/"):] if rel.startswith("mql5/") else rel


def entry_file(key):
    body = key.split(":", 1)[1]
    return body.split("#", 1)[0]


# ---------- commands ----------

def find_root(start):
    p = Path(start).resolve()
    for cand in [p] + list(p.parents):
        if (cand / ".git").exists():
            return cand
    return None


def load_lock(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("ERROR        lock file not found: %s (run --init as a reviewed step first)" % Path(path).name)
        return None
    except (OSError, ValueError):
        print("ERROR        lock file unreadable: %s" % Path(path).name)
        return None
    if data.get("version") != LOCK_VERSION or not isinstance(data.get("entries"), dict):
        print("ERROR        lock file has an old or unknown format (re-init as a reviewed step)")
        return None
    return data["entries"]


def emit(status, key, note="", ok=False):
    """Human: '<STATUS> <entry> (note)'. Porcelain: '<STATUS> <repo-path> <entry>', non-OK only."""
    if PORCELAIN:
        if not ok:
            path = entry_file(key) if ":" in key else key
            print("%s %s %s" % (status, path, key))
        return
    print("%-12s %s%s" % (status, key, (" (%s)" % note) if note else ""))


def say(line):
    if not PORCELAIN:
        print(line)


def compare(locked, current, ok="PASS", bad="FAIL", missing="MISSING", quiet_ok=False):
    n = 0
    for key in sorted(set(locked) | set(current)):
        if key not in locked:
            emit(bad, key, "not in lock"); n += 1
        elif key not in current:
            emit(missing, key, "no longer present"); n += 1
        elif current[key] is None:
            emit(missing, key); n += 1
        elif current[key] != locked[key]:
            emit(bad, key); n += 1
        elif not quiet_ok:
            emit(ok, key, ok=True)
    return n


def result(word, detail):
    print("RESULT %s" % word if PORCELAIN else "RESULT       %s%s" % (word, detail))


def system_includes_used(root, chain):
    names = set()
    for rel in chain:
        for _l, kind, target, trel in includes_of(root, rel, root / rel):
            sysname = re.sub(r"[\\/]+", "/", target.strip()).lower()
            if trel is None and kind == "<" and sysname in SYSTEM_INCLUDES:
                names.add(sysname)
    return sorted(names)


def system_entries(root, live, chain):
    e = {}
    for name in system_includes_used(root, chain):
        p = ci_resolve(live / "Include", name)
        e["sys:" + name] = hash_file(p) if p else None
    return e


def cmd_list(root):
    cur = compute_repo(root)
    for k, v in cur.items():
        print("%-12s %s" % ("present" if v else "MISSING", k))
    for rel in include_chain(root):
        print("%-12s %s" % ("chain", rel))
    probs = scan_problems(root)
    for s in probs:
        print("FAIL         %s" % s)
    missing = [k for k, v in cur.items() if v is None]
    print("RESULT       %s (%d entries, %d missing, %d scan problem(s); nothing written)"
          % ("FAIL" if (missing or probs) else "PASS", len(cur), len(missing), len(probs)))
    return 1 if (missing or probs) else 0


def cmd_diff_names(root, lock_path):
    locked = load_lock(lock_path)
    if locked is None:
        return 2
    cur = compute_repo(root)
    n = 0
    for key in sorted(set(locked) | set(cur)):
        if key not in locked:
            print("ADDED        %s" % key); n += 1
        elif key not in cur or cur[key] is None:
            print("REMOVED      %s" % key); n += 1
        elif cur[key] != locked[key]:
            print("CHANGED      %s" % key); n += 1
    probs = scan_problems(root)
    for s in probs:
        print("SCAN         %s" % s)
    print("RESULT       %d entry name(s) differ from the lock, %d scan problem(s)" % (n, len(probs)))
    return 1 if (n or probs) else 0


def cmd_init(root, lock_path, force, live_dir=None, live_system=False, roots=None):
    if live_system and not (live_dir and Path(live_dir).is_dir()):
        print("ERROR        --init --live-system needs --live <MQL5 dir>")
        return 2
    if Path(lock_path).exists() and not force:
        print("ERROR        lock exists; --init --force is a reviewed step (see docstring)")
        return 2
    cur = compute_repo(root)
    bad = 0
    for k in sorted(k for k, v in cur.items() if v is None):
        print("MISSING      %s" % k); bad += 1
    for s in scan_problems(root):
        print("FAIL         %s" % s); bad += 1
    if live_system:
        sysent = system_entries(root, Path(live_dir), include_chain(root, roots))
        for k in sorted(k for k, v in sysent.items() if v is None):
            print("MISSING-LIVE %s" % k); bad += 1
        cur.update(sysent)
    if bad:
        print("RESULT       FAIL (lock not written; every TOOL_FILES and SEAL file must exist)")
        return 1
    data = {"version": LOCK_VERSION, "generated_by": "config_guard.py", "entries": cur}
    Path(lock_path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    for k in cur:
        print("LOCKED       %s" % k)
    print("RESULT       PASS (lock written; commit it alone with a Config-Change-Reason: trailer)")
    return 0


def parse_roots(spec):
    if not spec:
        return list(EA_ROOTS)
    want = [x.strip().lower() for x in spec.split(",") if x.strip()]
    roots = [ea for ea in EA_ROOTS if Path(ea).stem.lower() in want]
    if len(roots) != len(want):
        return None
    return roots


def cmd_print_chain(root, roots):
    for rel in include_chain(root, roots):
        print("chain        %s" % rel)
    for rel in include_chain(root, roots):
        if rel.lower().startswith("mql5/include/"):
            print("include      %s" % Path(rel).name)
    return 0


def cmd_check(root, lock_path, live_dir, roots=None, live_system=False):
    locked = load_lock(lock_path)
    if locked is None:
        return 2
    repo_locked = {k: v for k, v in locked.items() if not k.startswith("sys:")}
    bad = compare(repo_locked, compute_repo(root))
    for sp in scan_problems(root):
        if PORCELAIN:
            print("FAIL %s scan" % sp.split(":", 1)[0])
        else:
            print("FAIL         %s" % sp)
        bad += 1
    if bad:
        result("FAIL", " (%d problem(s))" % bad)
        return 1
    if not live_dir:
        result("PASS", "")
        return 0
    say("RESULT       PASS (repo)")
    live = Path(live_dir)
    if not live.is_dir():
        emit("MISSING-LIVE", "MQL5-folder")
        result("FAIL", " (live MQL5 folder not found)")
        return 1
    say("NOTE         --live compares source files and their input DEFAULTS only, not chart-saved inputs")
    chain = include_chain(root, roots)
    say("NOTE         live roots: %s" % ", ".join(Path(r).stem for r in (roots or EA_ROOTS)))
    missing, cur = 0, {}
    for rel in chain:
        e = file_entries(root, rel, live / to_live(rel))
        if e is None:
            emit("MISSING-LIVE", rel); missing += 1
            continue
        cur.update(e)
    chain_set = set(chain)
    sub = {k: v for k, v in repo_locked.items() if not k.startswith(("file:", "tree:")) and entry_file(k) in chain_set}
    for k in sub:  # a locked entry the live copy no longer produces
        if k not in cur and entry_file(k) in chain_set and (live / to_live(entry_file(k))).is_file():
            cur[k] = "<absent>"
    nil = [k for k, v in cur.items() if v is None]
    for k in sorted(nil):
        emit("MISSING-LIVE", k)
    live_cur = {k: v for k, v in cur.items() if v is not None and entry_file(k) in chain_set}
    if live_system:
        sysent = system_entries(root, live, chain)
        for k in sorted(k for k, v in sysent.items() if v is None):
            emit("MISSING-LIVE", k); missing += 1
        for k, v in sysent.items():
            if v is not None:
                live_cur[k] = v
        for k in sysent:
            if k in locked:
                sub[k] = locked[k]
            elif sysent[k] is not None:
                emit("FAIL", k, "no system-include baseline; record it with --init --live --live-system"); missing += 1
                live_cur.pop(k, None)
    drift = compare(sub, live_cur, ok="LIVE-OK", bad="DRIFT", missing="DRIFT")
    if missing or nil:
        result("FAIL", " (%d live file(s)/region(s) MISSING-LIVE or without baseline)" % (missing + len(nil)))
        return 1
    if drift:
        result("LIVE-DRIFT", " (%d entry(s) differ from the lock in the terminal folder)" % drift)
        return 3
    result("LIVE-PASS", "")
    return 0


def _decls_at(root, ref, rel):
    if ref is None:
        p = root / rel
        sts = input_statements(load(p)) if p.is_file() else []
    else:
        r = subprocess.run(["git", "-C", str(root), "show", "%s:%s" % (ref, rel)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        sts = input_statements(Src(decode(r.stdout))) if r.returncode == 0 else []
    return {n: d for _l, decls, _s in sts for n, d in decls}


def cmd_changed_inputs(root, ref):
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode != 0:
        print("ERROR        git ref not found")
        return 2
    total = 0
    for ea, inc in EA_CONFIG:
        old, new = {}, {}
        for rel in (ea, inc):
            old.update(_decls_at(root, ref, rel))
            new.update(_decls_at(root, None, rel))
        label = Path(ea).stem
        for n in sorted(set(old) | set(new)):
            if n not in old:
                print("ADDED        %s.%s (new input: takes the SOURCE default on recompile)" % (label, n)); total += 1
            elif n not in new:
                print("REMOVED      %s.%s (renamed or deleted: a renamed input loses its chart value)" % (label, n)); total += 1
            elif old[n] != new[n]:
                print("CHANGED      %s.%s (declaration or default changed)" % (label, n)); total += 1
    print("RESULT       %d input change(s) since the given ref (names only)" % total)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Config, SEAL and tooling integrity guard. "
                                 "Prints names and PASS/FAIL only, never contents or values.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--init", action="store_true", help="write the lock (reviewed step)")
    g.add_argument("--list", action="store_true", help="show entries and scan results; write nothing")
    g.add_argument("--diff-names", action="store_true", help="print only entry names that differ from the lock")
    g.add_argument("--changed-inputs", metavar="GIT_REF", help="names of inputs changed since GIT_REF")
    g.add_argument("--print-chain", action="store_true", help="print the include chain of the roots; write nothing")
    ap.add_argument("--force", action="store_true", help="with --init: replace an existing lock (reviewed step)")
    ap.add_argument("--root", help="repository root (default: nearest parent with .git)")
    ap.add_argument("--lock", help="lock file (default: <root>/config.lock.json)")
    ap.add_argument("--live", metavar="MQL5_DIR", help="also compare the terminal's live copies of the include chain")
    ap.add_argument("--porcelain", action="store_true", help="machine-readable output (non-OK lines only)")
    ap.add_argument("--live-system", action="store_true", help="with --live: also check allowlisted system includes")
    ap.add_argument("--live-roots", metavar="EA[,EA]", help="EAs whose include chain --live/--print-chain use "
                    "(Receiver, Sender; default both)")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else find_root(os.getcwd())
    if root is None or not root.is_dir():
        print("ERROR        repository root not found (use --root)")
        return 2
    lock = Path(a.lock) if a.lock else root / "config.lock.json"
    global PORCELAIN
    PORCELAIN = bool(a.porcelain)
    roots = parse_roots(a.live_roots)
    if roots is None:
        print("ERROR        --live-roots accepts: %s" % ", ".join(Path(r).stem for r in EA_ROOTS))
        return 2
    if a.print_chain:
        return cmd_print_chain(root, roots)
    if a.list:
        return cmd_list(root)
    if a.diff_names:
        return cmd_diff_names(root, lock)
    if a.changed_inputs:
        return cmd_changed_inputs(root, a.changed_inputs)
    if a.init:
        return cmd_init(root, lock, a.force, a.live, a.live_system, roots)
    return cmd_check(root, lock, a.live, roots, a.live_system)


if __name__ == "__main__":
    sys.exit(main())
