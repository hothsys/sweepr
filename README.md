# <img src="logo.svg" width="50" height="42" valign="bottom" />&nbsp;&nbsp;Sweepr

A macOS system cleaner that finds and removes applications, Homebrew packages, npm globals, LaunchAgents, and orphaned files left behind by uninstalled apps.

## Install

```bash
git clone https://github.com/hothsys/sweepr
cd sweepr
python3 server.py
```

That's it. The browser opens automatically. No pip install. No build step. Python comes with macOS.

## Optional: run at login

To have `Sweepr` start automatically when you log in and appear in your Applications folder:

```bash
bash launchagent/install.sh
```

To undo:

```bash
bash launchagent/uninstall.sh
```

## Features

- **Applications**: scan `/Applications`, `/System/Applications`, and `~/Applications` with icons, version info, and size
- **Associated files**: finds related preferences, caches, containers, and support files across `~/Library` when removing an app
- **Storage analyzer**: a separate full-screen mode (opened from the "Analyze Storage" button, since it is read-only) that drills into top-level folders (Home, Documents, Downloads, Library, Applications, and more) with live-computed sizes, largest first
- **Orphan detection**: scans `~/Library` (and `/Library/PrivilegedHelperTools`) for files belonging to apps that are no longer installed
- **Homebrew**: lists all installed formulae and casks; uninstall directly from the UI
- **npm**: lists all globally installed npm packages; uninstall directly from the UI
- **LaunchAgents**: lists user and system LaunchAgents and LaunchDaemons, flags stale or failing ones, and removes them (unloading the job too)
- **Custom uninstaller detection**: finds official uninstallers bundled with apps and surfaces them before you trash anything
- **Risk assessment**: warns before removing dependencies, system-integrated apps, or toolchain packages
- **Show in Finder**: reveal an app, or any folder or file in the storage analyzer, in Finder
- **Dark mode**: follows your system setting with a manual toggle
- **Progressive loading**: app list appears in under 1 second; sizes load in the background

## Requirements

- macOS
- Python 3.8+ (included with macOS)

No external Python packages required.

## Starting and stopping the application

If you started Sweepr manually with `python3 server.py`, press **Ctrl+C** in the terminal.

If it's running as a login item:

```bash
# Stop for this session only. It will restart automatically on next login
launchctl stop com.sweepr.server

# Start it again after stopping (without needing to log out)
launchctl start com.sweepr.server

# Remove it as a login item permanently and stop it now
bash launchagent/uninstall.sh

# Re-add it as a login item after uninstalling
bash launchagent/install.sh
```

## Logging

Sweepr writes a rotating log to `sweepr.log` in the project directory. Watch it live:

```bash
tail -f sweepr.log
```

The log captures startup, every HTTP request, scan durations, file operations, subprocess calls, and errors. It rotates at 10 MB and keeps 3 backups (`sweepr.log.1`, `sweepr.log.2`, `sweepr.log.3`).

## How It Works

`server.py` is a Python HTTP server with no framework dependencies. Scanning and removal logic lives in small single-purpose modules under `lib/`, and the frontend is plain HTML, CSS, and vanilla JavaScript under `static/`, split by responsibility into modules under `static/js/`, with no build step and no `node_modules`. On startup, applications are scanned using batched Spotlight metadata queries and parallel size calculation so the UI loads instantly, while Homebrew, npm, and the storage analyzer's top-level folder sizes are warmed in the background.

For the full breakdown of backend modules, the frontend module split, the API endpoints, and the request and data flow, see the [Architecture](docs/architecture.md) doc.

## Documentation

Detailed deep-dives on each part of Sweepr live in [`docs/`](docs/):

- [Architecture](docs/architecture.md): modules, API endpoints, request and data flow
- [Application scanning](docs/scanning.md): discovery, Spotlight metadata, asynchronous sizes, icons
- [Associated files](docs/associated-files.md): how related `~/Library` files are found for an app
- [Storage analyzer](docs/storage.md): the separate full-screen analyzer, fast lazy sizing, and drill-down
- [Orphan detection](docs/orphans.md): finding leftovers of apps that are no longer installed
- [Homebrew and npm](docs/homebrew-and-npm.md): the two-phase brew scan, npm globals, and uninstall
- [LaunchAgents](docs/launch-agents.md): the Agents tab and stale-agent detection
- [Removal and uninstallers](docs/removal.md): the Trash mechanism and custom uninstaller support
- [Risk assessment](docs/risk-assessment.md): how the safe/caution banners are decided
- [Running as a service](docs/service.md): the LaunchAgent, starting/stopping, and single-instance behavior
- [Logging](docs/logging.md): log files, rotation, and reading a refresh in the log
- [Troubleshooting](docs/troubleshooting.md): common messages and how to resolve them

## Disclaimer

Sweepr moves files to the Trash; it does not permanently delete them immediately. However, removing the wrong application or associated files can break software or affect system behaviour. **Use at your own risk.** Always verify what you are removing before confirming. The authors accept no responsibility for data loss or system issues resulting from use of this software.

## License

MIT

---

Built with [Claude Code](https://claude.ai/claude-code)