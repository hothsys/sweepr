# LaunchAgents

The Agents tab lists macOS LaunchAgents and LaunchDaemons and flags the ones that are stale, meaning they point to a program that no longer exists. The scan lives in `lib/launchagent_scanner.py` and runs via `GET /api/agents`.

## What are LaunchAgents?

LaunchAgents and LaunchDaemons are plist files that tell macOS to run a program automatically, typically at login or in the background. Apps often install them for auto-updaters, helpers, and background services, and just as often fail to remove them when uninstalled. Those leftovers keep trying to run (or simply sit there) long after the app is gone.

## Where it looks

| Scope | Location |
| --- | --- |
| User | `~/Library/LaunchAgents` |
| System | `/Library/LaunchAgents` |
| System Daemons | `/Library/LaunchDaemons` |

Only `.plist` files are considered.

## Stale detection

For each plist, Sweepr reads the program launchd actually runs: the `Program` key if present (in that case `ProgramArguments[0]` is only argv[0]), otherwise the first element of `ProgramArguments`. Plists that `plistlib` rejects are retried through `plutil` (except when the file can't be read at all because of permissions); one that still can't be read is listed anyway with a `parse_error` and an unknown executable, rather than silently disappearing.

An agent is marked **stale** only when Sweepr can positively tell its executable is missing (`_exe_exists`). It reports *unknown* (`exe_exists: null`, never stale) when:

- the path is relative or a bare name (`sh`, `node`), which launchd resolves through its own `PATH`;
- the file can't be checked because of permissions (several security agents keep their binaries in folders a normal user can't read);
- the plist uses `BundleProgram` (relative to an app bundle).

Treating "couldn't check" as "missing" used to mark live security agents (Carbon Black, Rapid7) as stale and safe to remove.

A plist that declares nothing to run at all (no `Program`, `ProgramArguments` or `BundleProgram`) is stale too: launchd can't do anything with it. Google blanks its old Keystone plists to `<dict/>` this way once its newer updater takes over.

For jobs run through an interpreter (`sh`, `bash`, `zsh`, `python3`, `perl`, `ruby`, `node`, `osascript`, ...; the full list is `INTERPRETERS`), the interpreter always exists, so Sweepr checks the script instead (`_script_target`): the first non-option argument, or for `-c`/`-e` (`bash -c "..."`) the first word of the command. Only an absolute script path is checked; otherwise the interpreter's own result stands. It's shown as `script`.

Stale agents are the safe ones to remove, since they can never run.

## Status from launchd

One `launchctl print` per domain (`system` and `gui/<uid>`) gives every loaded job's PID and last exit code (`_launchd_status`). Each agent gets `loaded`, `pid` (when running) and `last_exit`, and is marked **failing** when it's loaded, not running, and its last run exited with an error (a positive code; negative codes are signals, which is how jobs are normally stopped). A failing job is often a duplicate or broken setup. For example, the same Homebrew service installed both system-wide (`sudo brew services`) and per user has one copy failing on every attempt because the other already runs. Jobs that run once at boot and then unload show as "not loaded", which is normal.

Each agent record also reports:

| Field | Meaning |
| --- | --- |
| `label` | The agent's `Label` (or the plist filename) |
| `scope` | User, System, or System Daemons |
| `executable` | The program it would launch |
| `exe_exists` | Whether that program (or its script) is present (`null` when unknown or none is declared) |
| `script` | For interpreter-run jobs, the script that is checked instead |
| `stale` / `stale_reason` | Whether it can never run, and why ("its program is missing" or "it declares nothing to run") |
| `parse_error` | Set only when the plist couldn't be read |
| `is_self` | Sweepr's own agent (`com.sweepr.server`) |
| `run_at_load` | Whether it runs at login/boot (`RunAtLoad`) |
| `keep_alive` | Whether macOS restarts it if it exits (`KeepAlive`) |

## Removal

The UI lists stale agents first, then failing ones, then the rest (unless sorted by name), and rates stale agents safe and the rest caution (see [Risk assessment](risk-assessment.md)). `POST /api/agents/trash` only accepts plists from the last `GET /api/agents` scan (403 otherwise). For each one it first refuses protected paths (`is_safe_to_trash`, the same check the Trash move uses), so a protected job is never unloaded. It then moves the plist to `~/.Trash` using the standard safe mechanism so macOS won't load it again, and only if that worked unloads the job with `launchctl bootout` (`unload_agent()` in `lib/launchagent_scanner.py`), which stops it now. In that order, a trash that fails (a root-owned plist, a cancelled prompt) leaves the job running and installed, never stopped-but-still-installed. bootout targets the label, so it works after the plist has moved. User and `/Library/LaunchAgents` jobs unload in your `gui/<uid>` domain. `/Library/LaunchDaemons` jobs live in the `system` domain and need root to unload; for those the plist is still trashed, but the response includes a warning and the UI tells you the job keeps running until restart. A job that isn't loaded (bootout exit code 3 or 113) counts as success; any other bootout failure becomes a warning the same way. Sweepr's own agent (`com.sweepr.server`, flagged `is_self`) can't be removed here; use `launchagent/uninstall.sh`. See [Removal and uninstallers](removal.md).
