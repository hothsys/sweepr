# Troubleshooting

Common messages and issues, and what to do about them.

## "permission denied for ..." in the log

```
scan_orphans: permission denied for Accounts (/Users/you/Library/Accounts)
```

macOS protects certain folders (Accounts, Cookies, Mail, Safari, Messages, and parts of Containers) under its privacy system (TCC). A plain process cannot read them without Full Disk Access. Sweepr catches the error, skips that one location, and continues. Nothing breaks and the other locations still scan.

Options:

1. **Leave it (recommended).** These locations rarely hold removable orphans and are sensitive. The scan works fine without them.
2. **Grant Full Disk Access:** System Settings, Privacy & Security, Full Disk Access, add your Python binary. Not recommended for a tool whose job is deleting files, since it widens what Sweepr can touch.

## "Server already running on port 47836"

A second instance tried to start while one was already running. Sweepr keeps a PID file at `/tmp/sweepr.pid`; on startup it checks whether that process is alive and, if so, exits with this warning instead of double-binding the port.

Usually this is benign. If it appears when you did not expect a server to be up, something is already holding the port. Find and stop it:

```bash
# What is listening on the port
lsof -nP -iTCP:47836 -sTCP:LISTEN

# Stop the tracked instance
kill "$(cat /tmp/sweepr.pid)"
```

If you run Sweepr as a LaunchAgent, note that `launchctl stop` only controls the copy launchd started. An instance you started by hand (`python3 server.py`) is invisible to `launchctl` and must be stopped with `kill` as above. See [Starting and stopping the application](../README.md) in the README, and [Running as a service](service.md).

## Homebrew or npm tab is empty

When Sweepr runs as a LaunchAgent it has a minimal environment. If `brew` or `npm` is not on `PATH`, those scans find nothing. The plist and launcher set `PATH` to include `/opt/homebrew/bin` and `/usr/local/bin`; confirm those entries are present. Running the server manually in a normal shell (where `brew`/`npm` are on `PATH`) also resolves it.

If the Homebrew tab shows a "Homebrew needs attention" banner, `brew` itself is failing. The banner shows the fix command when Homebrew provides one. Check the log for `scan_homebrew: brew info returned`. A common cause is an Xcode or Command Line Tools update that needs its license accepted: run `sudo xcodebuild -license accept`, then click refresh in the Homebrew tab.

## An app's date looks wrong

Sweepr labels the app date as "App version date" and sources it from `kMDItemContentModificationDate`. This reflects when the developer built or last modified the app, not when you installed it, and it can shift after a macOS upgrade. It is intentionally not presented as your install date. See [Application scanning](scanning.md).

## An app reappears after removal, or shows "0 associated files"

The list only drops an app when the server confirms its bundle path was actually trashed, so a partial failure will not make an app vanish. If you see "0 associated files," the name and bundle id genuinely matched nothing under `~/Library`; that is common for small or sandboxed apps. See [Associated files](associated-files.md).

## The app icon is blank

Icons are extracted from each app's `.icns` and cached. A blank icon usually means the bundle has no `CFBundleIconFile`, or macOS's own icon cache is stale for a freshly installed build. Extraction details are in [Application scanning](scanning.md).

## The process shows as "python3" in Activity Monitor

macOS names a process after its binary, and Sweepr runs on the system Python without bundling its own. Showing "Sweepr" instead would require packaging it as a standalone binary (for example with PyInstaller), which is out of scope for this zero-dependency build.
