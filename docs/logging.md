# Logging

Sweepr logs everything it does to a rotating file, so you can audit exactly what was scanned and removed. The logger is set up once in `lib/log.py` and shared by every module.

## Where logs go

| File | Contents |
| --- | --- |
| `sweepr.log` (project directory) | Application log: scans, removals, cache hits, warnings, errors |
| `/tmp/sweepr.stdout.log` | Standard output when run as a LaunchAgent |
| `/tmp/sweepr.stderr.log` | Standard error when run as a LaunchAgent |

The application log is the one that matters day to day. The `/tmp` files only capture raw process output from the launchd service.

## Format and level

Lines look like:

```
2026-07-20 15:06:08 [INFO    ] GET /api/apps → 133 apps
```

The logger runs at `DEBUG`, so the file captures fine-grained detail (cache hits, per-location scan counts, size computation timing) in addition to the higher-level `INFO` events. Access lines for endpoints the UI polls (`/api/apps/sizes`, `/api/storage/progress`, `/api/brew/status`, `/api/storage/roots`) and for icons are logged at `DEBUG` so they don't drown out real events. An unexpected exception in a request handler is logged with its traceback (`log.exception`) and answered with a 500, instead of dropping the connection with the traceback only on stderr.

## Rotation

The log uses a `RotatingFileHandler` capped at 10 MB with 3 backups. When `sweepr.log` reaches the cap it rolls over to `sweepr.log.1`, `sweepr.log.2`, `sweepr.log.3`, and the oldest is discarded. Total log footprint is bounded at roughly 40 MB. Logging is append mode, so history survives restarts.

## Reading a page refresh

A browser refresh reads as a clear initialization sequence. The page is served with no-cache, so every refresh re-requests the shell and all eight JS modules:

```
────────────────────────────────────────────────────────────
Client loaded Sweepr UI: initializing modules + data
  → init module: utils.js
  → init module: core.js
  → init module: apps.js
  → init module: brew.js
  → init module: npm.js
  → init module: orphans.js
  → init module: agents.js
  → init module: main.js
GET /api/apps → 133 apps
```

After the modules load, the data calls follow (`/api/apps`, then background `/api/brew` and `/api/npm`), each logged with its result count. This makes it easy to correlate a burst of requests to a specific refresh. CSS loads are logged at debug level to keep the sequence readable.

## Expected warnings

Some warnings are normal and do not indicate a problem:

- `permission denied for Accounts (...)` and similar: a macOS privacy-protected location was skipped because Sweepr lacks Full Disk Access. The scan continues. See [Troubleshooting](troubleshooting.md).
- `Server already running on port 47836`: a second instance tried to start while one was already up, and exited. See [Troubleshooting](troubleshooting.md).
