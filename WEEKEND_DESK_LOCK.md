# Weekend desk lock

The trading system keeps running all week. Remote Desktop opens by itself for one weekend window, set once, then closes by itself. The dedicated Gmail stays sealed on the home PC and is used only when that desk fails to open.

The two programs are in `desk/`. They stay outside the Java server, the receiver, and the sender.

## The doors

Weekdays are the trading days. The window is the weekend span chosen once, in one timezone.

| Door | Weekdays | Weekend window | Role |
| --- | --- | --- | --- |
| Remote Desktop | Blocked | Open | The desk. The weekday block is the lock. |
| Sender to the Java server | Open | Open | Trading stays available. This port is never on the lock. |
| Receiver to the Java server | Open on localhost | Open on localhost | Both stay on the VPS. The firewall does not sit between them. |
| Provider reset link | Sealed | One command, and only if the desk failed | The dedicated Gmail is how the provider console is reached. |

## Two programs

The same timezone and the same weekend hours are set once in each program.

### Window keeper (Windows VPS)

A scheduled task running as SYSTEM, every minute. Inside the window it allows inbound Remote Desktop. Outside it blocks that port.

If the Java server crashes, this task is still running, so the desk still reopens at the next window. The receiver keeps posting to localhost either way.

### Link app (home PC)

Holds one sealed file: an app password for the dedicated Gmail. The seal is a free public time lock. There is no account and no fee. The key for the file does not exist until the window starts.

One command, `link`, reads the newest mail from the VPS provider and shows that reset link. It does this only during the window, and only after Remote Desktop has refused a connection.

## What you do

**Once, at setup.** Create the dedicated Gmail, turn on its app password, clear recovery paths that point at the everyday mailbox, remove the account from every device, seal the app password, and install the keeper with the weekend hours.

**A normal week.** Nothing. Trade from the sender. The desk stays shut. The seal stays shut.

**A weekend with changes to load.** Remote Desktop is already open. Load the changes and disconnect. Leave the seal alone.

**A weekend when the desk will not open.** Run `link`. It shows the provider's reset link. Open that link, fix the machine, and close the app.

**At the end of every window.** The home PC reseals the same app password for the next window and deletes the old file. The Gmail password is not changed and is not shown.

## One file, one folder

After the window starts, an old copy of the sealed file can be opened by anyone who has that copy. The reseal matters only if that file is the only copy. Keep it in a folder that does not sync to Drive, OneDrive, or a phone. If the home disk dies and that file is gone, the provider console stays out of reach. Trading on the sender continues.

## How a failed desk is told from a dead network

`link` asks the public time beacon whether the window is open, then tries Remote Desktop. A hang or a timeout stays silent, because that is also what a home-network blip looks like. It shows the reset link only when the beacon says the window is open and the desktop port actively refuses the connection.

A machine that is fully powered off stays silent too. That outage still needs the provider console, which needs this link, so a powered-off VPS during the window is the case this design does not cover. Showing the link whenever the VPS cannot be reached would also show it when the home connection fails, and the first time that mailbox path appears, the lock on the file is spent for the week.

## Left as they are

The Java server, the receiver, and the sender stay on their current jobs. Signals, gates, and the sender panel do not learn about this lock. The sender's port stays open all week so trading does not depend on the desk.

## Left out on purpose

No mailbox password inside an Expert Advisor. No OAuth project. No weekly Gmail password change. The provider console stays up at the host; the way into it is the sealed link.

## Programs

The window keeper is `desk/keeper`. Link is `desk/link`. The weekend hours both of them use are in `desk/shared`.

Copy `desk/desk.env.example` to `desk/desk.env` on each machine. The timezone and the weekend hours are the same in both copies.

On the VPS, from an elevated PowerShell at the repo root: `py -3 -m desk.keeper install`. That turns Remote Desktop on, arms the block, and registers the SYSTEM task that runs every minute.

On the home PC, from an elevated PowerShell: `py -3 -m desk.link install`, then `py -3 -m desk.link seal`. `seal` asks for the app password once and locks it until the next window. The reseal task runs every minute and, once the window has ended, locks that same password for the next window.

When the desk will not open, run `desk\link.cmd`.

