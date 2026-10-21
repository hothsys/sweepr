# Running as a service

Sweepr can run continuously in the background as a macOS LaunchAgent, so the UI is always a browser refresh away without a terminal open. This covers how that works and how to control it. For the quick command reference, see the README.

## The LaunchAgent

The agent is defined by `~/Library/LaunchAgents/com.sweepr.server.plist`. Key settings:

| Key | Value | Effect |
| --- | --- | --- |
| `Label` | `com.sweepr.server` | The identifier used with `launchctl` |
| `ProgramArguments` | `python3 server.py --no-browser` | Runs the server without opening a browser |
| `EnvironmentVariables` `PATH` | includes `/opt/homebrew/bin`, `/usr/local/bin` | So `brew` and `npm` are found |
| `RunAtLoad` | `true` | Starts at login |
| `KeepAlive` | `false` | Does not auto-restart when stopped |
| `StandardOutPath` / `StandardErrorPath` | `/tmp/sweepr.stdout.log` / `.stderr.log` | Raw process output |

Because `KeepAlive` is `false`, `launchctl stop` genuinely stops it for the session; it will not immediately respawn. `RunAtLoad` means it comes back at the next login.

## The `--no-browser` flag

By default, starting `server.py` opens your browser at the app URL. The LaunchAgent passes `--no-browser` so a login does not pop open a browser tab every time. You open the UI yourself when you want it.

## Single instance

On startup the server writes its PID to `/tmp/sweepr.pid` and, if that file already points to a live process, exits with `Server already running on port 47836` instead of double-binding the port. The PID file is cleaned up on a clean shutdown (SIGTERM/SIGINT).

## Starting and stopping

```bash
# Start (for this login session)
launchctl start com.sweepr.server

# Stop (for this login session; returns at next login)
launchctl stop com.sweepr.server
```

## Gotcha: manual instances are invisible to launchctl

`launchctl start` and `launchctl stop` only control the copy launchd manages. If you start the server by hand:

```bash
python3 server.py
```

that process is not tracked by launchd, so `launchctl stop` will not stop it, and it will hold the port and PID file. If launchd then tries to start its own copy, that copy sees the PID file and exits with "Server already running." To stop a manually started instance, use the PID file directly:

```bash
kill "$(cat /tmp/sweepr.pid)"
```

Mixing the two start methods is the usual cause of a server you "cannot stop." Pick one: rely on `launchctl` for the background service, or run it by hand for a foreground session. See [Troubleshooting](troubleshooting.md).
