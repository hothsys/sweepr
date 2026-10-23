# Architecture

Sweepr is a local web app: a Python HTTP server that serves both a JSON API and a static vanilla-JS frontend. There is no framework, no build step, and no third-party Python dependencies.

## Stack

- **Backend:** Python 3 standard library only (`http.server.ThreadingHTTPServer`), bound to `localhost:47836`. Requests with a foreign `Host`, and POSTs with a foreign `Origin` or a non-JSON `Content-Type`, are rejected (see [Request handling](#request-handling) and [Removal](removal.md#request-checks)).
- **Frontend:** plain HTML, CSS, and vanilla JavaScript under `static/`, using the Josefin Sans font.
- **System integration:** shells out to native macOS tools (`mdls`, `mdfind`, `du`, `sips`, `ps`, `osascript`, `open`, `launchctl`, `plutil`, `brew`, `npm`) rather than bundling libraries.

## Backend modules (`lib/`)

Each module is single-purpose and has no framework dependencies.

| Module | Responsibility |
| --- | --- |
| `scanner.py` | Discover installed applications, batch Spotlight metadata, compute sizes, detect custom uninstallers |
| `sizes.py` | `disk_usage()`: on-disk size like `du` (no symlink following, hard links once) plus whether everything was readable, used by the app, associated-file, and orphan scanners and by app trashing; `is_protected_container()` flags app containers macOS won't let anything remove |
| `associated_files.py` | Find preferences, caches, containers, and support files tied to an app across `~/Library` |
| `orphan_scanner.py` | Scan `~/Library` (and system paths) for leftovers of apps no longer installed |
| `brew_scanner.py` | List and uninstall Homebrew formulae and casks (quick list plus background detail pass) |
| `npm_scanner.py` | List and uninstall globally installed npm packages |
| `launchagent_scanner.py` | Enumerate LaunchAgents/LaunchDaemons, flag stale and failing ones, unload a job with `launchctl bootout` |
| `storage_scanner.py` | Lazily size directories with streaming `du`, list children, guard scope |
| `icons.py` | Extract a PNG icon from each app's `.icns` (falling back to `sips`), with a bounded in-memory cache keyed by app path |
| `trash.py` | Move paths to `~/.Trash`, detect and quit running apps, find and remove their Login Items, guard protected paths |
| `log.py` | Rotating file logger shared by every module |

`server.py` wires these together: it owns the request routing, in-memory caches, background warmup threads, and the single-instance PID lock.

## Frontend modules (`static/js/`)

The JavaScript is split by responsibility. These load as ordinary `<script>` files (not ES modules) so the inline event handlers in generated markup can call the functions directly. `main.js` loads last because it depends on everything above it; the rest resolve at call time through shared global scope.

| File | Responsibility |
| --- | --- |
| `utils.js` | Formatting helpers, HTML escaping, toasts, the confirmation modal, JSON request helpers (`fetchJson`, `postJson`, which send `Content-Type: application/json`), and per-item risk assessment |
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
| GET | `/static/...` | Static assets (HTML, CSS, JS) |
| GET | `/favicon.ico` | Favicon (not JSON) |
| GET | `/api/health` | Liveness check |
| GET | `/api/apps` | Installed applications (cached) |
| GET | `/api/apps/refresh` | Force a fresh application scan |
| GET | `/api/apps/sizes` | Progressive size map; `complete` flag ends polling |
| GET | `/api/apps/{id}/icon` | PNG icon for an app (not JSON; cached by the browser for a day) |
| GET | `/api/apps/{id}/files` | Associated files for an app |
| GET | `/api/apps/{id}/running` | Whether the app is currently running |
| GET | `/api/brew` | Homebrew packages (quick list, then detailed) |
| GET | `/api/brew/refresh` | Force a fresh Homebrew scan |
| GET | `/api/brew/status` | Whether the detailed brew pass has finished, plus any `brew` error and a suggested fix command |
| GET | `/api/npm` | Global npm packages, plus an `error` if the scan failed |
| GET | `/api/npm/refresh` | Force a fresh npm scan |
| GET | `/api/agents` | LaunchAgents/LaunchDaemons with stale flags |
| GET | `/api/orphans` | Orphaned files grouped for the UI |
| GET | `/api/storage/roots` | Top-level storage roots, with cached totals when warm |
| POST | `/api/storage/list` | Immediate children of a directory (files sized inline) |
| POST | `/api/storage/progress` | Growing map of subdirectory sizes for a directory |
| POST | `/api/storage/reveal` | Reveal a storage path in Finder |
| POST | `/api/apps/{id}/trash` | Quit the app if running, then move it plus selected associated files to Trash and remove its Login Items (files must come from `/api/apps/{id}/files`) |
| POST | `/api/apps/{id}/reveal` | Reveal an app in Finder |
| POST | `/api/apps/{id}/launch-uninstaller` | Launch an app's bundled uninstaller |
| POST | `/api/brew/{name}/uninstall` | `brew uninstall` plus `brew autoremove` (package must be in the scanned list) |
| POST | `/api/npm/{name}/uninstall` | `npm uninstall -g` (package must be in the scanned list; `npm` and `corepack` are refused) |
| POST | `/api/agents/trash` | Move a launch agent's plist to Trash, then unload the job with `launchctl bootout` (paths must come from `/api/agents`) |
| POST | `/api/orphans/trash` | Move selected orphan paths to Trash (paths must come from `/api/orphans`) |

The `{id}` for an app is normally its bundle identifier. When two apps share a bundle id (for example two Python launchers), Sweepr falls back to the full path so each stays distinct.

## Request handling

`RequestHandler` in `server.py` checks every request before routing it:

- **Host.** Every GET and POST must carry a `Host` of `localhost:47836`, `127.0.0.1:47836` or `[::1]:47836`, otherwise it gets a 403. This blocks DNS rebinding.
- **Origin and Content-Type (POST only).** A POST with an `Origin` header outside those three hosts gets a 403; a missing `Origin` is allowed, since browsers always send one on POST. The `Content-Type` must be `application/json` or the request gets a 415.
- **Body.** POST bodies are capped at 1 MB (`MAX_BODY`, 413 above that). A bad `Content-Length`, invalid JSON, or JSON that isn't an object gets a 400. An empty body is treated as `{}`. Handlers then validate their own fields (for example `paths` must be a non-empty list of strings).
- **Path.** The query string is dropped and the path is URL-decoded exactly once before dispatch, so `{id}` and `{name}` arrive decoded.
- **Errors.** Unknown routes return a JSON 404. Any unhandled exception in a handler is logged with its traceback and answered with a JSON 500 (`{"error": "Internal error"}`); a client that disconnected mid-response is logged at debug level only.

Error responses are always JSON of the form `{"error": "..."}`.

## Request and data flow

1. **Boot:** `server.py` starts, takes the PID lock, and kicks off four background threads that warm the apps, Homebrew, and npm caches and size the storage roots, so data is ready before the browser asks for it.
2. **Page load:** the browser fetches `index.html`, then the nine JS modules in order. `main.js` calls `fetchApps(false)`.
3. **Instant list:** `/api/apps` returns from the warm cache and the list renders in well under a second. App sizes are not blocking; they arrive later.
4. **Progressive sizes:** `apps.js` polls `/api/apps/sizes` on a timer, filling in each size cell as the background `ThreadPoolExecutor` computes it, then stops when `complete` is true.
5. **Preload:** `core.js` `preloadOtherTabs()` fetches Homebrew and npm in the background so those tabs are instant when clicked. Homebrew then polls `/api/brew/status` until the detailed pass lands and re-renders with descriptions, sizes, and dependency info.

See [Application scanning](scanning.md) for how the app list itself is built, and [Logging](logging.md) for how a page load reads in the log.
