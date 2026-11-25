# <img src="logo.svg" width="100" valign="middle" hspace="10" vspace="4"/>Sweepr

*A macOS system cleaner that finds and removes applications, Homebrew packages, npm globals, LaunchAgents, and orphaned files left behind by uninstalled apps.*

Sweepr scans your Mac for installed apps and leftover files, warns before risky removals, and moves what you remove to the Trash. It runs as a small local Python server with a browser UI.

## Quick Start

Requirements: macOS and Python 3.8+ (included with macOS). No pip install, no build step, no external Python packages.

```bash
git clone https://github.com/hothsys/sweepr
cd sweepr
python3 server.py
```

The browser opens automatically. To stop Sweepr, press **Ctrl+C** in the terminal.

To start Sweepr at login and add it to your Applications folder, run `bash launchagent/install.sh` (undo with `bash launchagent/uninstall.sh`). See [Running as a service](docs/service.md) for details.

## Documentation

Full documentation lives in the [`docs/`](docs/) folder:

| Page | What it covers |
| --- | --- |
| [Features](docs/features.md) | Feature overview |
| [Architecture](docs/architecture.md) | Modules, API endpoints, request and data flow, how it works |
| [Application scanning](docs/scanning.md) | Discovery, Spotlight metadata, asynchronous sizes, icons |
| [Associated files](docs/associated-files.md) | How related `~/Library` files are found for an app |
| [Storage analyzer](docs/storage.md) | The separate full-screen analyzer, fast lazy sizing, and drill-down |
| [Orphan detection](docs/orphans.md) | Finding leftovers of apps that are no longer installed |
| [Homebrew and npm](docs/homebrew-and-npm.md) | The two-phase brew scan, npm globals, and uninstall |
| [LaunchAgents](docs/launch-agents.md) | The Agents tab and stale-agent detection |
| [Removal and uninstallers](docs/removal.md) | The Trash mechanism and custom uninstaller support |
| [Risk assessment](docs/risk-assessment.md) | How the safe and caution banners are decided |
| [Running as a service](docs/service.md) | The LaunchAgent, starting and stopping, single-instance behavior |
| [Logging](docs/logging.md) | Log files, rotation, and reading a refresh in the log |
| [Troubleshooting](docs/troubleshooting.md) | Common messages and how to resolve them |

## License

This project is licensed under the [MIT](LICENSE.md) License. See the [LICENSE.md](LICENSE.md) file for details.

Sweepr moves files to the Trash; it does not permanently delete them immediately. However, removing the wrong application or associated files can break software or affect system behaviour. **Use at your own risk.** Always verify what you are removing before confirming. The authors accept no responsibility for data loss or system issues resulting from use of this software.

---

Built with [Claude Code](https://claude.ai/claude-code)
