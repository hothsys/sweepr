# Architecture

Sweepr is a local web app: a Python HTTP server that serves both a JSON API and a static vanilla-JS frontend. There is no framework, no build step, and no third-party Python dependencies.

## Stack

- **Backend:** Python 3 standard library only (`http.server.ThreadingHTTPServer`), bound to `localhost:47836`. Requests with a foreign `Host`, and POSTs with a foreign `Origin` or a non-JSON `Content-Type`, are rejected (see [Removal](removal.md#request-checks)).
- **Frontend:** plain HTML, CSS, and vanilla JavaScript under `static/`, using the Josefin Sans font.
- **System integration:** shells out to native macOS tools (`mdls`, `mdfind`, `du`, `sips`, `pgrep`, `osascript`, `brew`, `npm`) rather than bundling libraries.

## Backend modules (`lib/`)

Each module is single-purpose and has no framework dependencies.

| Module | Responsibility |
| --- | --- |
| `scanner.py` | Discover installed applications, batch Spotlight metadata, compute sizes, detect custom uninstallers |
| `sizes.py` | `disk_usage()`: on-disk size like `du` (no symlink following, hard links once) plus whether everything was readable; used by every scanner |
| `associated_files.py` | Find preferences, caches, containers, and support files tied to an app across `~/Library` |
| `orphan_scanner.py` | Scan `~/Library` (and system paths) for leftovers of apps no longer installed |
| `brew_scanner.py` | List and uninstall Homebrew formulae and casks (quick list plus background detail pass) |
| `npm_scanner.py` | List and uninstall globally installed npm packages |
| `launchagent_scanner.py` | Enumerate LaunchAgents/LaunchDaemons and flag stale ones |
| `storage_scanner.py` | Lazily size directories with streaming `du`, list children, guard scope |
| `icons.py` | Extract a PNG icon from each app's `.icns`, with an in-memory cache |
| `trash.py` | Move paths to `~/.Trash`, detect running apps, guard protected paths |
| `log.py` | Rotating file logger shared by every module |

`server.py` wires these together: it owns the request routing, in-memory caches, background warmup threads, and the single-instance PID lock.

## Frontend modules (`static/js/`)

The JavaScript is split by responsibility. These load as ordinary `<script>` files (not ES modules) so the inline event handlers in generated markup can call the functions directly. `main.js` loads last because it depends on everything above it; the rest resolve at call time through shared global scope.

| File | Responsibility |
| --- | --- |
| `utils.js` | Formatting helpers, HTML/JS escaping, toasts, and per-item risk assessment |
| `core.js` | Shared state, DOM references, theme handling, tab switching, background preloading |
| `apps.js` | Applications tab: list, details, associated files, trashing, size polling |
| `brew.js` | Homebrew tab: async detail loading and uninstall |
| `npm.js` | npm globals tab |
| `orphans.js` | Orphaned-file tab, grouped by category |
| `agents.js` | LaunchAgents tab, including stale-agent detection |
| `storage.js` | Storage analyzer: full-screen mode (own overlay), drill-down list, breadcrumb, reveal |
| `main.js` | Wires up event listeners and bootstraps the app; loaded last |

## API endpoints

All responses are JSON unless noted.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Serves `index.html` |
| GET | `/static/...` | Static assets (HTML, CSS, JS, favicon) |
| GET | `/api/health` | Liveness check |
| GET | `/api/apps` | Installed applications (cached) |
| GET | `/api/apps/refresh` | Force a fresh application scan |
| GET | `/api/apps/sizes` | Progressive size map; `complete` flag ends polling |
| GET | `/api/apps/{id}/icon` | PNG icon for an app |
| GET | `/api/apps/{id}/files` | Associated files for an app |
| GET | `/api/apps/{id}/running` | Whether the app is currently running |
| GET | `/api/brew` | Homebrew packages (quick list, then detailed) |
| GET | `/api/brew/refresh` | Force a fresh Homebrew scan |
| GET | `/api/brew/status` | Whether the detailed brew pass has finished |
| GET | `/api/npm` | Global npm packages |
| GET | `/api/npm/refresh` | Force a fresh npm scan |
| GET | `/api/agents` | LaunchAgents/LaunchDaemons with stale flags |
| GET | `/api/orphans` | Orphaned files grouped for the UI |
| GET | `/api/storage/roots` | Top-level storage roots, with cached totals when warm |
| POST | `/api/storage/list` | Immediate children of a directory (files sized inline) |
| POST | `/api/storage/progress` | Growing map of subdirectory sizes for a directory |
| POST | `/api/storage/reveal` | Reveal a storage path in Finder |
| POST | `/api/apps/{id}/trash` | Move an app plus selected associated files to Trash (files must come from `/api/apps/{id}/files`) |
| POST | `/api/apps/{id}/reveal` | Reveal an app in Finder |
| POST | `/api/apps/{id}/launch-uninstaller` | Launch an app's bundled uninstaller |
| POST | `/api/brew/{name}/uninstall` | `brew uninstall` plus `brew autoremove` |
| POST | `/api/npm/{name}/uninstall` | `npm uninstall -g` |
| POST | `/api/agents/trash` | Unload a launch agent with `launchctl bootout`, then move its plist to Trash (paths must come from `/api/agents`) |
| POST | `/api/orphans/trash` | Move selected orphan paths to Trash (paths must come from `/api/orphans`) |

The `{id}` for an app is normally its bundle identifier. When two apps share a bundle id (for example two Python launchers), Sweepr falls back to the full path so each stays distinct.

## Request and data flow

1. **Boot:** `server.py` starts, takes the PID lock, and kicks off three background threads that warm the apps, Homebrew, and npm caches so data is ready before the browser asks for it.
2. **Page load:** the browser fetches `index.html`, then the eight JS modules in order. `main.js` calls `fetchApps(false)`.
3. **Instant list:** `/api/apps` returns from the warm cache and the list renders in well under a second. App sizes are not blocking; they arrive later.
4. **Progressive sizes:** `apps.js` polls `/api/apps/sizes` on a timer, filling in each size cell as the background `ThreadPoolExecutor` computes it, then stops when `complete` is true.
5. **Preload:** `core.js` `preloadOtherTabs()` fetches Homebrew and npm in the background so those tabs are instant when clicked. Homebrew then polls `/api/brew/status` until the detailed pass lands and re-renders with descriptions, sizes, and dependency info.

See [Application scanning](scanning.md) for how the app list itself is built, and [Logging](logging.md) for how a page load reads in the log.
