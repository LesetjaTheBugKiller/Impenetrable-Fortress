# RDP login log (tamper-evident)

The keeper can keep its own record of Remote Desktop logons on the VPS. Each entry is signed with
a key that never leaves the VPS, and each entry also carries the signature of the one before it.
If a line is edited, deleted, inserted or moved, `verify` says which line.

## What is recorded

| Log | Event | Logged as |
| --- | --- | --- |
| Security | 4624, logon type 10 only | `logon` |
| Security | 4625, logon type 3 or 10 (failed RDP, with or without NLA) | `logon_failed` |
| TerminalServices-RemoteConnectionManager/Operational | 1149 | `rdp_authenticated` |
| TerminalServices-LocalSessionManager/Operational | 21 / 24 / 25 | `session_logon` / `session_disconnected` / `session_reconnected` |

Each line: `seq`, `time`, `kind`, `user`, `domain`, `ip`, `channel`, `record`, `event_id`, `kid` (key id),
`prev` (MAC of the line before), `mac` (HMAC-SHA256 of everything else).

## Turn it on (VPS, once)

1. Elevated PowerShell at the repo root: `py -3 -m desk.keeper rdp-log init`. This creates the key at
   `%ProgramData%\WeekendDesk\rdp-log.key`: 32 random bytes, Windows DPAPI (LocalMachine), file readable only by SYSTEM and Administrators.
   It refuses to overwrite an existing key, because a new key would make the old log stop verifying.
2. In the VPS `desk.env`, set `RDPLOG_ENABLED=yes`. The keeper task, which already runs every minute as SYSTEM,
   then appends new events after it sets the firewall. A logging failure prints a line and never stops the firewall work.
3. Optional: `py -3 -m desk.keeper rdp-log collect` collects once by hand.

## Check it

- `py -3 -m desk.keeper rdp-log verify`: exit 0 = intact, 2 = tampered, 1 = setup problem (for example no key).
- `py -3 -m desk.keeper rdp-log head`: prints `seq:mac` for the newest line. **Keep that line off the VPS**
  (paste it into the home PC, an email to yourself, a note).
- Later: `py -3 -m desk.keeper rdp-log verify --anchor 42:3f9a...` (repeat `--anchor` for several).
  This catches a rewritten chain and a cut-off tail, which the MACs alone cannot.

Example of a tampered log (line 2 edited, seq 4 deleted):

```
RDP log: C:\ProgramData\WeekendDesk\rdp-log.jsonl
  line 2     seq 2      EDITED       MAC does not match the contents: logon VPS\micca from 10.0.0.1 at 2026-10-10T20:02:00.000Z
  line 4     seq 5      MISSING      seq 4 deleted before this line
  line 4     seq 5      BROKEN LINK  does not follow the line before it (deleted, inserted, or moved)
TAMPERED: 3 problem(s) in 5 entries.
```

Other flags: `OUT OF ORDER`, `UNREADABLE`, `WRONG KEY`, `ANCHOR DIFF` (chain rewritten since the anchor), `ANCHOR LOST` (anchored line gone).

## Honest limits

- SYSTEM or any administrator on the VPS can read the key and re-sign a whole new chain. Only an anchor kept off the VPS catches that.
- Without an anchor, deleting lines from the end is not detectable.
- It records what Windows logged. If the Security log was cleared or its auditing turned off before collection
  (collection runs every minute), those events never reach this log. Event 1102 "log cleared" is not collected yet.
- The first run looks back 7 days. After that it continues from the last record number per log.
- Entries are only as true as the event (for example, the IP is what Windows reported).
- Collection is skipped in any keeper run that exits 1 (port or firewall error, broken desk.env, window state
  unknown). Windows keeps the events, and the next successful run picks them up from the last record number.
  Such runs are recorded in %ProgramData%\WeekendDesk\keeper-status.json and as Application event 900
  (source WeekendDesk); see desk/keeper/RECOVERY.md.

## Settings (`desk.env`, VPS)

`RDPLOG_ENABLED` (default off), `RDPLOG_PATH` (default `%ProgramData%\WeekendDesk\rdp-log.jsonl`),
`RDPLOG_KEY_PATH` (default `%ProgramData%\WeekendDesk\rdp-log.key`).
