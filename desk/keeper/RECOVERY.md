# Keeper errors and lockout recovery (VPS)

## What the keeper does on an error

`keeper run` exits 1 and prints a line starting with `ALERT:` when anything goes wrong: a refused RDP_PORT,
more than 4 block ports, a netsh command that fails, a broken or missing desk.env, or a window state it
cannot work out.

- Inside the window: no rule is changed. The next run, one minute later, tries again.
- Outside the window, or when the window state is unknown: it fails closed. The existing block rules
  (WeekendDeskLock-TCP, -UDP and the old WeekendDeskLock) are switched on and the allow rules switched off,
  using only `netsh advfirewall firewall set rule name=... new enable=...`. Ports are not changed.
  If one netsh call fails it still does the others. The ALERT says clearly when NO block rule could be
  confirmed enabled.
- If neither WeekendDeskLock-TCP nor -UDP exists (fresh VPS), the scheduled run (`--scheduled`, used by
  keeper-task.cmd) creates them, enabled, on 3389 plus the listener port when it passes the port check.
  A run started by hand only prints an ALERT.

## Where to look

On the VPS (the home PC cannot see these):
- `%ProgramData%\WeekendDesk\keeper-status.json`: time (UTC), ok, error, window, what was done, and the
  state of every rule. Rewritten by every run, so `"ok": true` means the last run was fine.
  Diagnostics only: the keeper never reads it to decide anything.
- Event Viewer > Windows Logs > Application, source WeekendDesk, event ID 900 (one per failed run):
  `Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='WeekendDesk'; Id=900} -MaxEvents 20`

## Getting back in when Remote Desktop is blocked

1. The provider's web console (the VPS screen in the browser, not Remote Desktop). It does not go through
   the Windows firewall. Check now, before you need it, that it works:
   - log in to the provider's site, open the VPS console, and sign in to Windows there;
   - run `py -3 -m desk.keeper run` in an elevated PowerShell there and read the output;
   - write down where the console is and which account it needs.
   If the provider has no such console, there is no emergency path; plan for that.
2. In the console, elevated, at the repo root:
   - read keeper-status.json, fix the cause (usually desk.env);
   - then run `py -3 -m desk.keeper run` there and check that it prints no ALERT.
   There is no override: outside the window Remote Desktop stays blocked until the window opens.
   An `open-now.json` left in `%ProgramData%\WeekendDesk` by an older version is ignored; delete it.
3. To shrink the port lists: `py -3 -m desk.keeper reset-ports` inside the open window
   (outside it only with `--force`).

## The WeekendDesk folder

`install` and every `run` set `%ProgramData%\WeekendDesk` to SYSTEM and Administrators full
control only, inheritance removed (icacls with the SIDs S-1-5-18 and S-1-5-32-544, so it works in any
language; Users, Authenticated Users, Everyone and CREATOR OWNER grants are removed). `install`
stops if that fails; `run` only warns. Check: `icacls %ProgramData%\WeekendDesk`.

## Install

`py -3 -m desk.keeper install` refuses to run outside the window unless `--force`: installing switches
the block on at once, and it is not verified that a Remote Desktop session you are using survives that.
Install inside the window, or from the provider's console.
