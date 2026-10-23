# Running as a service

Sweepr can run continuously in the background as a macOS LaunchAgent, so the UI is always a browser refresh away without a terminal open. This covers how that works and how to control it. For the quick command reference, see the README.

## The LaunchAgent

The agent is defined by `~/Library/LaunchAgents/com.sweepr.server.plist`, which `bash launchagent/install.sh` generates from the template `launchagent/com.sweepr.server.plist` and loads with `launchctl load`. Key settings:

| Key | Value | Effect |
| --- | --- | --- |
| `Label` | `com.sweepr.server` | The identifier used with `launchctl` |
| `ProgramArguments` | `<python3> <path>/server.py --no-browser` | Runs the server without opening a browser; `install.sh` fills in the absolute paths of `python3` (from `which python3`) and `server.py` |
| `EnvironmentVariables` `PATH` | `/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` | So `brew` and `npm` are found |
| `RunAtLoad` | `true` | Starts at login |
| `KeepAlive` | `false` | Does not auto-restart when stopped |
| `StandardOutPath` / `StandardErrorPath` | `/tmp/sweepr.stdout.log` / `.stderr.log` | Raw process output |

Because `KeepAlive` is `false`, `launchctl stop` genuinely stops it for the session; it will not immediately respawn. `RunAtLoad` means it comes back at the next login.

## The Sweepr.app launcher

`install.sh` also creates `/Applications/Sweepr.app` (bundle id `com.sweepr.launcher`, icon rendered from `logo.svg`). Opening it checks `/api/health`; if the server is up it just opens the browser at `http://localhost:47836`, otherwise it starts `server.py --no-browser` itself with the same `PATH` (the launcher opens the browser, so the server mustn't open a second tab), waits up to 5 seconds for it to answer, and shows an error dialog if it never does. A server started this way is a plain child process, not the launchd job (see the gotcha below).

`bash launchagent/uninstall.sh` reverses all of this: it unloads and deletes the plist, stops the process named in `/tmp/sweepr.pid` (whichever way it was started), and removes `Sweepr.app`. The project folder is left in place. Sweepr refuses to trash its own launcher or LaunchAgent from the UI; use the script.

## The `--no-browser` flag

By default, starting `server.py` opens your browser at the app URL. The LaunchAgent passes `--no-browser` so a login does not pop open a browser tab every time, and `Sweepr.app` passes it because it opens the browser itself. You open the UI yourself when you want it.

## Single instance

On startup the server writes its PID to `/tmp/sweepr.pid` and, if that file already points to a live process, logs `Server already running on port 47836`, prints the URL, and exits instead of double-binding the port. The PID file is removed on shutdown (SIGTERM, SIGINT/Ctrl+C, or normal interpreter exit). Only the PID file is checked: if something unrelated holds port 47836, startup fails when binding instead.

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

(or `Sweepr.app` starts it because nothing was running), that process is not tracked by launchd, so `launchctl stop` will not stop it, and it will hold the port and PID file. If launchd then tries to start its own copy, that copy sees the PID file and exits with "Server already running." To stop a manually started instance, use the PID file directly:

```bash
kill "$(cat /tmp/sweepr.pid)"
```

Mixing the two start methods is the usual cause of a server you "cannot stop." Pick one: rely on `launchctl` for the background service, or run it by hand for a foreground session. See [Troubleshooting](troubleshooting.md).
