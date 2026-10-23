import atexit
import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lib.log import log
from lib.sizes import disk_usage
from lib.scanner import scan_applications, calculate_dir_size
from lib.icons import get_icon_png
from lib.associated_files import find_associated_files
from lib.trash import move_to_trash, check_app_running, is_safe_to_trash, quit_app, find_login_items, delete_login_items
from lib.brew_scanner import scan_homebrew, scan_homebrew_quick, suggested_fix, uninstall_package as brew_uninstall
from lib.npm_scanner import scan_npm_globals, PROTECTED_PACKAGES as NPM_PROTECTED, uninstall_package as npm_uninstall
from lib.orphan_scanner import scan_orphans
from lib.launchagent_scanner import scan_launch_agents, unload_agent
from lib.storage_scanner import get_roots, list_children, stream_dir_sizes, is_in_scope

PORT = 47836
BIND_HOST = "localhost"
PID_FILE = "/tmp/sweepr.pid"
BASE_DIR = Path(__file__).parent

# Requests must name this server in Host (blocks DNS rebinding), and POSTs from a
# browser must come from its own page (blocks other websites driving the API).
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}", f"[::1]:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}

_apps_cache = None
_brew_cache = None
_npm_cache = None
_npm_error = None

_apps_lock = threading.Lock()
_brew_lock = threading.Lock()
_npm_lock = threading.Lock()

_sizes_cache = {}
_sizes_lock = threading.Lock()
_sizes_complete = False
_sizes_total = 0

# Storage analyzer: per-directory size cache.
# path -> {"sizes": {child_path: bytes}, "complete": bool, "mtime": float|None,
#          "total": int|None, "computed_at": float}
_storage_cache = {}
_storage_lock = threading.Lock()
_storage_scanning = set()
_STORAGE_TTL = 60  # seconds a completed scan is trusted even if mtime shifts
# A directory's mtime only changes when its direct children change, so an
# unchanged mtime can't vouch for deeper changes forever
_STORAGE_MAX_AGE = 600
_STORAGE_CACHE_MAX = 256  # bound the drill cache in this long-running process

MAX_BODY = 1024 * 1024  # largest POST body accepted

# Polled endpoints are logged at DEBUG so they don't flood the log at INFO
_QUIET_PATHS = ("/api/apps/sizes", "/api/storage/progress", "/api/brew/status", "/api/storage/roots")

# Bumped whenever a cache is invalidated or a rescan starts; background work
# started under an older generation discards its results instead of
# overwriting newer data
_sizes_gen = 0
_brew_gen = 0

# Paths the UI was actually shown. Trash requests may only name these, so the
# endpoints can't be used to remove arbitrary files.
_offered_lock = threading.Lock()
_offered_orphans = set()
_offered_agents = {}  # plist path -> agent dict
_offered_files = {}   # app path -> set of associated file paths


def _compute_sizes_background(apps, gen):
    global _sizes_complete
    t0 = time.time()
    log.info("_compute_sizes_background: computing sizes for %d apps (gen %d)", len(apps), gen)

    def compute_one(app):
        with _sizes_lock:
            if gen != _sizes_gen:
                return  # superseded by a newer pass; skip the disk walk
        size = calculate_dir_size(Path(app["path"]))
        with _sizes_lock:
            if gen != _sizes_gen:
                return
            _sizes_cache[app["path"]] = size
            app["size"] = size

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(compute_one, apps))

    with _sizes_lock:
        if gen != _sizes_gen:
            log.info("_compute_sizes_background: gen %d superseded, discarding", gen)
            return
        _sizes_complete = True
    log.info("_compute_sizes_background: complete in %.2fs", time.time() - t0)


def _start_size_computation(apps):
    global _sizes_cache, _sizes_complete, _sizes_total, _sizes_gen
    with _sizes_lock:
        _sizes_gen += 1
        gen = _sizes_gen
        _sizes_cache = {}
        _sizes_complete = False
        _sizes_total = len(apps)
    log.debug("_start_size_computation: starting background thread for %d apps", len(apps))
    threading.Thread(target=_compute_sizes_background, args=(apps, gen), daemon=True).start()


def _invalidate_apps():
    """Drop the app list so the next get_apps() rescans. Taking the lock means a
    scan already in progress finishes first, rather than storing its stale list
    after this."""
    global _apps_cache, _sizes_gen, _sizes_complete
    with _apps_lock:
        _apps_cache = None
    with _sizes_lock:
        _sizes_gen += 1  # stop the old size pass writing into the next one
        _sizes_complete = True


_brew_detailed = False
_brew_error = None

def get_brew(force_refresh=False):
    global _brew_cache, _brew_detailed, _brew_error, _brew_gen
    with _brew_lock:
        if _brew_cache is None or force_refresh:
            log.info("get_brew: cache miss, running quick scan%s", " (forced)" if force_refresh else "")
            _brew_gen += 1
            _brew_cache = scan_homebrew_quick()
            _brew_detailed = False
            _brew_error = None
            threading.Thread(target=_brew_fill_details, args=(_brew_gen,), daemon=True).start()
        else:
            log.debug("get_brew: cache hit (%d packages)", len(_brew_cache))
        return _brew_cache


def _invalidate_brew():
    global _brew_cache, _brew_gen
    with _brew_lock:
        _brew_cache = None
        _brew_gen += 1


def _brew_fill_details(gen):
    global _brew_cache, _brew_detailed, _brew_error
    log.info("_brew_fill_details: starting full brew scan (gen %d)", gen)
    full, err = scan_homebrew()
    with _brew_lock:
        if gen != _brew_gen:
            # An uninstall or refresh happened meanwhile; this data is out of date
            log.info("_brew_fill_details: gen %d superseded, discarding", gen)
            return
        if full is None:
            # Keep the quick list rather than blanking the tab
            _brew_error = err
            log.warning("_brew_fill_details: detailed scan failed, keeping quick list (%d packages)",
                        len(_brew_cache or []))
        else:
            _brew_cache = _merge_brew_lists(_brew_cache or [], full)
            log.info("_brew_fill_details: complete (%d packages)", len(_brew_cache))
        _brew_detailed = True


def _merge_brew_lists(quick, full):
    """Detailed entries replace quick ones, but packages `brew info` couldn't load
    (untrusted tap, unsupported OS condition, renamed formula) stay listed from
    the quick pass instead of vanishing."""
    # keg_name covers renamed formulae, whose Cellar folder (and so the quick
    # list's name) is the old one
    detailed = {(p["type"], p["name"]) for p in full} | {(p["type"], p["keg_name"]) for p in full if p.get("keg_name")}
    kept = [dict(p, details_missing=True) for p in quick if (p["type"], p["name"]) not in detailed]
    if kept:
        log.info("_merge_brew_lists: keeping %d package(s) brew info skipped: %s",
                 len(kept), [p["name"] for p in kept])
    return sorted(full + kept, key=lambda p: p["name"].lower())


def _invalidate_npm():
    global _npm_cache
    with _npm_lock:
        _npm_cache = None


def get_npm(force_refresh=False):
    global _npm_cache, _npm_error
    with _npm_lock:
        if _npm_cache is None or force_refresh:
            log.info("get_npm: cache miss, scanning%s", " (forced)" if force_refresh else "")
            _npm_cache, _npm_error = scan_npm_globals()
        else:
            log.debug("get_npm: cache hit (%d packages)", len(_npm_cache))
        return _npm_cache


def get_apps(force_refresh=False):
    global _apps_cache
    with _apps_lock:
        if _apps_cache is None or force_refresh:
            log.info("get_apps: cache miss, scanning%s", " (forced)" if force_refresh else "")
            _apps_cache = scan_applications()
            _start_size_computation(_apps_cache)
        else:
            system = sum(1 for a in _apps_cache if a.get("is_system"))
            log.debug("get_apps: cache hit (%d apps total, %d system, %d user)",
                      len(_apps_cache), system, len(_apps_cache) - system)
        return _apps_cache


def _other_apps(app):
    return [a for a in get_apps() if a is not app and not a.get("webloc") and not a.get("leftover")]


def find_app(identifier):
    """Match on full path first, then bundle id (some apps share a bundle id).
    `identifier` is already URL-decoded by the request handler."""
    by_bundle = None
    for app in get_apps():
        if app["path"] == identifier:
            return app
        if by_bundle is None and app["bundle_id"] and app["bundle_id"] == identifier:
            by_bundle = app
    return by_bundle


def _storage_scan(path):
    """Background worker: stream `du` sizes for `path` into the storage cache."""
    def on_child(child, size):
        with _storage_lock:
            entry = _storage_cache.get(path)
            if entry is not None:
                entry["sizes"][child] = size

    try:
        total = stream_dir_sizes(path, on_child)
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = None
        with _storage_lock:
            entry = _storage_cache.setdefault(
                path, {"sizes": {}, "complete": False, "mtime": None, "total": None, "computed_at": 0})
            if total is None:
                total = sum(entry["sizes"].values())
            entry["complete"] = True
            entry["mtime"] = mtime
            entry["total"] = total
            entry["computed_at"] = time.time()
        log.info("_storage_scan: %s complete (%.1f MB)", path, (total or 0) / 1024 / 1024)
    finally:
        with _storage_lock:
            _storage_scanning.discard(path)


def _evict_storage_cache_locked(keep):
    """Bound _storage_cache to _STORAGE_CACHE_MAX (call while holding the lock).

    FIFO eviction of the oldest entries, skipping the just-added path and any
    directory currently being scanned.
    """
    if len(_storage_cache) <= _STORAGE_CACHE_MAX:
        return
    # Roots are never evicted: /api/storage/roots only reads the cache, so an
    # evicted root would show a spinner forever
    roots = {r["path"] for r in get_roots()}
    for k in list(_storage_cache):
        if len(_storage_cache) <= _STORAGE_CACHE_MAX:
            break
        if k == keep or k in _storage_scanning or k in roots:
            continue
        del _storage_cache[k]


def _ensure_storage_scan(path):
    """Return a fresh cached entry for `path`, or start a background scan.

    Fresh means a completed scan whose directory mtime is unchanged, or that
    finished within the last _STORAGE_TTL seconds (avoids constant rescans of
    busy directories like ~/Library).
    """
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        mtime = None
    with _storage_lock:
        cached = _storage_cache.get(path)
        if cached and cached["complete"]:
            age = time.time() - cached["computed_at"]
            fresh = age < _STORAGE_TTL or (cached["mtime"] == mtime and age < _STORAGE_MAX_AGE)
            if fresh:
                return cached
        if path in _storage_scanning:
            return cached
        _storage_scanning.add(path)
        # Keep the previous sizes visible while rescanning instead of blanking
        # every folder to 0; pop and re-insert so FIFO eviction sees it as newest
        old = _storage_cache.pop(path, None)
        _storage_cache[path] = {"sizes": dict(old["sizes"]) if old else {}, "complete": False,
                                "mtime": None, "total": None, "computed_at": 0}
        _evict_storage_cache_locked(keep=path)
    threading.Thread(target=_storage_scan, args=(path,), daemon=True).start()
    return None


def _invalidate_storage_above(paths):
    """After trashing, drop cached sizes for every directory containing a trashed
    path so the storage view doesn't keep showing the old totals."""
    with _storage_lock:
        for key in list(_storage_cache):
            if key in _storage_scanning:
                continue
            if any(p == key or p.startswith(key.rstrip(os.sep) + os.sep) for p in paths):
                _storage_cache[key]["computed_at"] = 0
                _storage_cache[key]["mtime"] = None


def _trash(paths):
    result = move_to_trash(paths)
    if result["success"]:
        _invalidate_storage_above(result["success"])
    return result


def _str_list(value):
    """Return value if it's a list of strings, else None."""
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def _storage_breadcrumb(path):
    """Build a clickable trail from the containing root down to `path`."""
    for r in get_roots():
        rp = r["path"]
        if path == rp or path.startswith(rp + os.sep):
            crumbs = [{"label": r["label"], "path": rp}]
            rel = path[len(rp):].strip(os.sep)
            if rel:
                acc = rp
                for part in rel.split(os.sep):
                    acc = acc + os.sep + part
                    crumbs.append({"label": part, "path": acc})
            return crumbs
    return [{"label": Path(path).name or path, "path": path}]


def _warm_storage_roots():
    roots = get_roots()
    log.info("_warm_storage_roots: warming %d roots", len(roots))

    def warm(r):
        p = r["path"]
        with _storage_lock:
            if p in _storage_scanning:
                return
            _storage_scanning.add(p)
            _storage_cache[p] = {"sizes": {}, "complete": False, "mtime": None, "total": None, "computed_at": 0}
        _storage_scan(p)

    # Each root gets its own `du` so a fast root (Documents) resolves in well under
    # a second regardless of a slow one (Library) still walking.
    with ThreadPoolExecutor(max_workers=max(4, len(roots))) as executor:
        list(executor.map(warm, roots))
    log.info("_warm_storage_roots: complete")


class RequestHandler(BaseHTTPRequestHandler):
    def _check_host(self):
        host = self.headers.get("Host", "")
        if host in ALLOWED_HOSTS:
            return True
        log.warning("%s %s: rejected, unexpected Host %r", self.command, self.path, host)
        self._send_error(403, "Forbidden")
        return False

    def _check_post_origin(self):
        # Browsers always send Origin on POST; a missing one is a local non-browser client.
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            log.warning("POST %s: rejected, cross-origin request from %r", self.path, origin)
            self._send_error(403, "Cross-origin requests are not allowed")
            return False
        # Requiring JSON forces a CORS preflight for any cross-origin attempt, which
        # this server never approves.
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            log.warning("POST %s: rejected, Content-Type %r", self.path, ctype)
            self._send_error(415, "Content-Type must be application/json")
            return False
        return True

    def _request_path(self):
        # Decode exactly once, after dropping any query string
        return unquote(urlsplit(self.path).path)

    def _parse_body(self):
        """Read and validate the JSON body into self._body. Returns False after
        sending a 400/413 if it's malformed."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_error(400, "Invalid Content-Length")
            return False
        if length < 0:
            self._send_error(400, "Invalid Content-Length")
            return False
        if length > MAX_BODY:
            self._send_error(413, "Request body too large")
            return False
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw) if raw.strip() else {}
        except (ValueError, UnicodeDecodeError):
            self._send_error(400, "Invalid JSON")
            return False
        if not isinstance(data, dict):
            self._send_error(400, "JSON body must be an object")
            return False
        self._body = data
        return True

    def do_GET(self):
        if not self._check_host():
            return
        try:
            self._dispatch_get(self._request_path())
        except (BrokenPipeError, ConnectionResetError):
            # The browser went away mid-response (tab closed or reloaded): not a bug
            log.debug("GET %s: client disconnected", self.path)
        except Exception:
            log.exception("GET %s: unhandled error", self.path)
            self._try_send_error(500, "Internal error")

    def do_POST(self):
        if not self._check_host() or not self._check_post_origin():
            return
        if not self._parse_body():
            return
        try:
            self._dispatch_post(self._request_path())
        except (BrokenPipeError, ConnectionResetError):
            # The browser went away mid-response (tab closed or reloaded): not a bug
            log.debug("POST %s: client disconnected", self.path)
        except Exception:
            log.exception("POST %s: unhandled error", self.path)
            self._try_send_error(500, "Internal error")

    def _try_send_error(self, status, message):
        try:
            self._send_error(status, message)
        except OSError:
            pass  # client already gone, or headers were already sent

    def _dispatch_get(self, path):
        if path == "/" or path == "" or path == "/static/index.html":
            log.info("─" * 60)
            log.info("Client loaded Sweepr UI: initializing modules + data")
            self._serve_file(BASE_DIR / "static" / "index.html", "text/html")
        elif path == "/favicon.ico":
            self._serve_file(BASE_DIR / "favicon.ico", "image/x-icon")
        elif path.startswith("/static/") and ".." not in path:
            fname = path.rsplit("/", 1)[-1]
            if path.endswith(".js"):
                log.info("  → init module: %s", fname)
            elif path.endswith(".css"):
                log.debug("  → init styles: %s", fname)
            self._serve_file(BASE_DIR / path.lstrip("/"))
        elif path == "/api/health":
            self._send_json({"status": "ok"})
        elif path == "/api/apps":
            apps = get_apps()
            log.info("GET /api/apps → %d apps", len(apps))
            self._send_json({"apps": apps})
        elif path == "/api/apps/refresh":
            apps = get_apps(force_refresh=True)
            log.info("GET /api/apps/refresh → %d apps", len(apps))
            self._send_json({"apps": apps})
        elif path == "/api/apps/sizes":
            with _sizes_lock:
                sizes = dict(_sizes_cache)
            log.debug("GET /api/apps/sizes → %d computed, complete=%s", len(sizes), _sizes_complete)
            self._send_json({"sizes": sizes, "complete": _sizes_complete})
        elif path.startswith("/api/apps/") and path.endswith("/icon"):
            identifier = path[len("/api/apps/"):-len("/icon")]
            self._handle_icon(identifier)
        elif path.startswith("/api/apps/") and path.endswith("/files"):
            identifier = path[len("/api/apps/"):-len("/files")]
            self._handle_files(identifier)
        elif path.startswith("/api/apps/") and path.endswith("/running"):
            identifier = path[len("/api/apps/"):-len("/running")]
            self._handle_running(identifier)
        elif path == "/api/brew":
            packages = get_brew()
            log.info("GET /api/brew → %d packages", len(packages))
            self._send_json({"packages": packages})
        elif path == "/api/brew/refresh":
            packages = get_brew(force_refresh=True)
            log.info("GET /api/brew/refresh → %d packages", len(packages))
            self._send_json({"packages": packages})
        elif path == "/api/brew/status":
            self._send_json({"detailed": _brew_detailed, "error": _brew_error,
                             "fix_command": suggested_fix(_brew_error)})
        elif path == "/api/npm":
            packages = get_npm()
            log.info("GET /api/npm → %d packages", len(packages))
            self._send_json({"packages": packages, "error": _npm_error})
        elif path == "/api/npm/refresh":
            packages = get_npm(force_refresh=True)
            log.info("GET /api/npm/refresh → %d packages", len(packages))
            self._send_json({"packages": packages, "error": _npm_error})
        elif path == "/api/agents":
            agents = scan_launch_agents()
            with _offered_lock:
                _offered_agents.clear()
                _offered_agents.update({a["path"]: a for a in agents})
            log.info("GET /api/agents → %d agents", len(agents))
            self._send_json({"agents": agents})
        elif path == "/api/orphans":
            self._handle_orphans()
        elif path == "/api/storage/roots":
            roots = get_roots()
            home = str(Path.home())
            for r in roots:
                with _storage_lock:
                    missing = r["path"] not in _storage_cache
                if missing:  # e.g. a root created after startup
                    _ensure_storage_scan(r["path"])
            with _storage_lock:
                # Sum of already-finished home subfolders, used to seed Home's
                # partial so it climbs immediately instead of sitting on a spinner
                # while its own (slow) walk of ~/Library finishes.
                home_child_total = sum(
                    c["total"] for r in roots
                    for c in [_storage_cache.get(r["path"])]
                    if c and c["complete"] and r["path"].startswith(home + os.sep)
                )
                for r in roots:
                    p = r["path"]
                    c = _storage_cache.get(p)
                    if c and c["complete"]:
                        r["size"] = c["total"]
                        r["scanning"] = False
                    else:
                        # Live partial: subfolders report as they finish, so show a
                        # growing total instead of a dead spinner for slow roots.
                        partial = sum(c["sizes"].values()) if c and c["sizes"] else 0
                        if p == home:
                            partial = max(partial, home_child_total)
                        r["size"] = partial if partial > 0 else None
                        r["scanning"] = True
            log.debug("GET /api/storage/roots → %d roots", len(roots))
            self._send_json({"roots": roots})
        else:
            log.warning("GET %s → 404", path)
            self._send_error(404, "Not found")

    def _dispatch_post(self, path):
        if path.startswith("/api/apps/") and path.endswith("/trash"):
            identifier = path[len("/api/apps/"):-len("/trash")]
            self._handle_trash(identifier)
        elif path.startswith("/api/apps/") and path.endswith("/reveal"):
            identifier = path[len("/api/apps/"):-len("/reveal")]
            self._handle_reveal(identifier)
        elif path.startswith("/api/apps/") and path.endswith("/launch-uninstaller"):
            identifier = path[len("/api/apps/"):-len("/launch-uninstaller")]
            self._handle_launch_uninstaller(identifier)
        elif path.startswith("/api/brew/") and path.endswith("/uninstall"):
            pkg_name = path[len("/api/brew/"):-len("/uninstall")]
            self._handle_brew_uninstall(pkg_name)
        elif path.startswith("/api/npm/") and path.endswith("/uninstall"):
            pkg_name = path[len("/api/npm/"):-len("/uninstall")]
            self._handle_npm_uninstall(pkg_name)
        elif path == "/api/agents/trash":
            self._handle_agent_trash()
        elif path == "/api/orphans/trash":
            self._handle_orphan_trash()
        elif path == "/api/storage/list":
            self._handle_storage_list()
        elif path == "/api/storage/progress":
            self._handle_storage_progress()
        elif path == "/api/storage/reveal":
            self._handle_storage_reveal()
        else:
            log.warning("POST %s → 404", path)
            self._send_error(404, "Not found")

    def _handle_icon(self, identifier):
        app = find_app(identifier)
        if not app:
            self._send_error(404, "App not found")
            return
        png_data = get_icon_png(app["path"], app["bundle_id"])
        if not png_data:
            self._send_error(404, "No icon available")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png_data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(png_data)

    def _handle_files(self, identifier):
        app = find_app(identifier)
        if not app:
            log.warning("_handle_files: app not found for %s", identifier)
            self._send_error(404, "App not found")
            return
        log.info("_handle_files: searching associated files for '%s'", app["name"])
        files = find_associated_files(app["bundle_id"], app["name"], app["path"], _other_apps(app))
        with _offered_lock:
            _offered_files[app["path"]] = {f["path"] for f in files if not f.get("protected")}
        total = sum(f["size"] for f in files)
        log.info("_handle_files: found %d files (%.1f KB) for '%s'", len(files), total / 1024, app["name"])
        self._send_json({"bundle_id": app["bundle_id"], "files": files, "total_size": total})

    def _handle_running(self, identifier):
        app = find_app(identifier)
        if not app:
            self._send_json({"running": False})
            return
        running = check_app_running(app["path"])
        log.debug("_handle_running: '%s' is %s", app["name"], "running" if running else "not running")
        self._send_json({"running": running})

    def _handle_trash(self, identifier):
        app = find_app(identifier)
        if not app:
            log.warning("_handle_trash: app not found for %s", identifier)
            self._send_error(404, "App not found")
            return
        if app["is_system"]:
            log.warning("_handle_trash: blocked, system app '%s'", app["name"])
            self._send_error(403, "Cannot remove system apps")
            return
        if app.get("is_self"):
            log.warning("_handle_trash: blocked, Sweepr's own launcher '%s'", app["path"])
            self._send_error(403, "Sweepr can't remove itself; run launchagent/uninstall.sh")
            return

        data = self._body
        paths = []
        file_paths = _str_list(data.get("file_paths", []))
        if file_paths is None:
            self._send_error(400, "file_paths must be a list of strings")
            return
        with _offered_lock:
            offered = _offered_files.get(app["path"])
        if offered is None:
            log.info("_handle_trash: no file list cached for '%s', scanning now", app["name"])
            offered = {f["path"] for f in find_associated_files(app["bundle_id"], app["name"], app["path"],
                                                                _other_apps(app)) if not f.get("protected")}
        unknown = [fp for fp in file_paths if fp not in offered]
        if unknown:
            log.warning("_handle_trash: rejected %d path(s) not associated with '%s': %s",
                        len(unknown), app["name"], unknown[:5])
            self._send_error(403, "Request includes files that are not associated with this app")
            return
        for fp in file_paths:
            paths.append(fp)
        include_app = data.get("include_app", True)
        quit_info = None
        login_items = []
        if include_app and not app.get("webloc") and not app.get("leftover"):
            # A running app keeps going from memory after its bundle is trashed (and
            # recreates the support files being removed), so stop it first. If it
            # can't be stopped, remove nothing rather than half-remove it.
            quit_info = quit_app(app["path"])
            if not quit_info["ok"]:
                log.error("_handle_trash: aborting removal of '%s': %s", app["name"], quit_info["error"])
                self._send_error(409, quit_info["error"])
                return
            # Matched now, deleted after the move succeeds (see find_login_items)
            login_items = find_login_items(app["path"])
        if include_app:
            paths.append(app["path"])

        # Sizes come from our own data, never from the client
        file_sizes = {fp: disk_usage(fp)[0] for fp in file_paths}

        log.info("_handle_trash: trashing '%s' + %d associated files", app["name"], len(file_paths))
        result = _trash(paths)
        success_set = set(result["success"])
        total_freed = sum(size for fp, size in file_sizes.items() if fp in success_set)
        if app["path"] in success_set:
            total_freed += app.get("size") or 0
        result["total_freed"] = total_freed
        result["quit"] = quit_info
        result["login_items_removed"] = delete_login_items(login_items) if app["path"] in success_set else []

        log.info("_handle_trash: done: %d succeeded, %d failed, %.1f MB freed",
                 len(result["success"]), len(result["failed"]), total_freed / 1024 / 1024)
        if result["failed"]:
            for f in result["failed"]:
                log.error("_handle_trash: failed to trash %s: %s", f["path"], f["error"])

        if result["success"]:
            with _offered_lock:
                if app["path"] in success_set:
                    _offered_files.pop(app["path"], None)
                elif app["path"] in _offered_files:
                    _offered_files[app["path"]] -= success_set
            _invalidate_apps()

        self._send_json(result)

    def _handle_reveal(self, identifier):
        app = find_app(identifier)
        if not app:
            log.warning("_handle_reveal: app not found for %s", identifier)
            self._send_error(404, "App not found")
            return
        log.info("_handle_reveal: revealing '%s' in Finder", app["name"])
        try:
            subprocess.Popen(["open", "-R", app["path"]])
            self._send_json({"revealed": True})
        except OSError as e:
            log.error("_handle_reveal: failed for '%s': %s", app["name"], e)
            self._send_error(500, str(e))

    def _handle_launch_uninstaller(self, identifier):
        app = find_app(identifier)
        if not app:
            log.warning("_handle_launch_uninstaller: app not found for %s", identifier)
            self._send_error(404, "App not found")
            return
        uninstaller = app.get("uninstaller")
        if not uninstaller:
            log.warning("_handle_launch_uninstaller: no uninstaller for '%s'", app["name"])
            self._send_error(404, "No uninstaller found")
            return
        try:
            kind = uninstaller["type"]
            path = uninstaller["path"]
            log.info("_handle_launch_uninstaller: launching %s uninstaller for '%s': %s",
                     kind, app["name"], path)
            if kind == "app":
                subprocess.Popen(["open", path])
            elif kind == "alias":
                subprocess.Popen(["open", path])
            elif kind == "executable":
                subprocess.Popen([path])
            elif kind == "script":
                subprocess.Popen(["open", "-a", "Terminal", path])
            self._send_json({"launched": True, "type": kind, "path": path})
        except OSError as e:
            log.error("_handle_launch_uninstaller: failed for '%s': %s", app["name"], e)
            self._send_error(500, str(e))

    def _handle_brew_uninstall(self, pkg_name):
        pkg_type = self._body.get("type", "formula")
        if pkg_type not in ("formula", "cask"):
            self._send_error(400, "type must be formula or cask")
            return
        # Only packages the scan listed; this also keeps option-like names
        # ("--force") from ever reaching brew's argument list
        with _brew_lock:
            listed = any(p["name"] == pkg_name and p["type"] == pkg_type for p in (_brew_cache or []))
        if not listed:
            log.warning("_handle_brew_uninstall: rejected %s (%s), not in the scanned list", pkg_name, pkg_type)
            self._send_error(403, "Package is not in the scanned Homebrew list")
            return
        log.info("_handle_brew_uninstall: uninstalling %s (%s)", pkg_name, pkg_type)
        result = brew_uninstall(pkg_name, pkg_type)
        _invalidate_brew()

        if result["success"]:
            log.info("_handle_brew_uninstall: success for %s", pkg_name)
            self._send_json(result)
        else:
            log.error("_handle_brew_uninstall: failed for %s: %s", pkg_name, result["error"])
            self._send_error(500, result["error"])

    def _handle_npm_uninstall(self, pkg_name):
        if pkg_name in NPM_PROTECTED:
            log.warning("_handle_npm_uninstall: blocked, %s is part of Node.js", pkg_name)
            self._send_error(403, f"{pkg_name} ships with Node.js and can't be removed here")
            return
        with _npm_lock:
            listed = any(p["name"] == pkg_name for p in (_npm_cache or []))
        if not listed:
            log.warning("_handle_npm_uninstall: rejected %s, not in the scanned list", pkg_name)
            self._send_error(403, "Package is not in the scanned npm list")
            return
        log.info("_handle_npm_uninstall: uninstalling %s", pkg_name)
        result = npm_uninstall(pkg_name)
        _invalidate_npm()

        if result["success"]:
            log.info("_handle_npm_uninstall: success for %s", pkg_name)
            self._send_json(result)
        else:
            log.error("_handle_npm_uninstall: failed for %s: %s", pkg_name, result["error"])
            self._send_error(500, result["error"])

    def _handle_orphans(self):
        apps = get_apps()
        known_ids = {a["bundle_id"] for a in apps if a["bundle_id"]}
        known_names = {a["name"] for a in apps}
        log.info("_handle_orphans: scanning with %d known apps", len(apps))
        orphans = scan_orphans(known_ids, known_names)
        with _offered_lock:
            _offered_orphans.clear()
            _offered_orphans.update(f["path"] for g in orphans for a in g["apps"] for f in a["files"]
                                    if not f.get("protected"))
        total_size = sum(g["total_size"] for g in orphans)
        log.info("_handle_orphans: found %d groups, %.1f MB", len(orphans), total_size / 1024 / 1024)
        self._send_json({"groups": orphans, "total_size": total_size})

    def _handle_orphan_trash(self):
        paths = _str_list(self._body.get("paths"))
        if not paths:
            self._send_error(400, "paths must be a non-empty list of strings")
            return

        with _offered_lock:
            unknown = [p for p in paths if p not in _offered_orphans]
        if unknown:
            log.warning("_handle_orphan_trash: rejected %d path(s) not in the last orphan scan: %s",
                        len(unknown), unknown[:5])
            self._send_error(403, "Request includes paths that were not found by the orphan scan")
            return

        log.info("_handle_orphan_trash: trashing %d orphan paths", len(paths))
        result = _trash(paths)
        with _offered_lock:
            _offered_orphans.difference_update(result["success"])
        log.info("_handle_orphan_trash: %d succeeded, %d failed",
                 len(result["success"]), len(result["failed"]))
        self._send_json(result)

    def _handle_agent_trash(self):
        paths = _str_list(self._body.get("paths"))
        if not paths:
            self._send_error(400, "paths must be a non-empty list of strings")
            return
        with _offered_lock:
            agents = [_offered_agents.get(p) for p in paths]
        if None in agents:
            unknown = [p for p, a in zip(paths, agents) if a is None]
            log.warning("_handle_agent_trash: rejected %d path(s) not in the last agent scan: %s",
                        len(unknown), unknown[:5])
            self._send_error(403, "Request includes paths that were not found by the agent scan")
            return

        log.info("_handle_agent_trash: removing %d agent(s)", len(agents))
        success, failed, warnings = [], [], []
        for agent in agents:
            path = agent["path"]
            # Check before bootout: unloading a protected job (Sweepr's own) would
            # stop it even though the trash step then refuses the plist.
            if not is_safe_to_trash(path):
                log.warning("_handle_agent_trash: blocked, protected agent %s", path)
                failed.append({"path": path, "error": "Protected path"})
                continue
            # Trash first: if that fails (root-owned plist, cancelled prompt) the job
            # must keep running rather than end up stopped but still installed.
            # bootout targets the label, so it works after the plist has moved.
            result = _trash([path])
            success.extend(result["success"])
            failed.extend(result["failed"])
            if not result["success"]:
                continue
            unloaded, err = unload_agent(agent["label"], agent["scope"])
            if not unloaded:
                warnings.append({"path": path, "label": agent["label"],
                                 "warning": f"Still running until restart (could not unload: {err})"})
        with _offered_lock:
            for p in success:
                _offered_agents.pop(p, None)
        log.info("_handle_agent_trash: %d succeeded, %d failed, %d still loaded",
                 len(success), len(failed), len(warnings))
        self._send_json({"success": success, "failed": failed, "warnings": warnings})

    def _body_path(self):
        """The normalized "path" string from the body, or None if missing or not a string."""
        raw = self._body.get("path")
        if not isinstance(raw, str) or not raw:
            return None
        return str(Path(raw))

    def _handle_storage_list(self):
        norm = self._body_path()
        if not norm:
            self._send_error(400, "No path provided")
            return
        if not is_in_scope(norm):
            log.warning("_handle_storage_list: out of scope: %s", norm)
            self._send_error(403, "Path out of scope")
            return

        listing = list_children(norm)
        if listing is None:
            self._send_error(403, "Directory not accessible")
            return

        _ensure_storage_scan(norm)
        with _storage_lock:
            c = _storage_cache.get(norm) or {}
            sizes = dict(c.get("sizes", {}))
            complete = bool(c.get("complete"))

        entries = []
        for d in listing["dirs"]:
            d = dict(d)
            d["size"] = sizes.get(d["path"], 0)
            entries.append(d)
        entries.extend(listing["files"])
        entries.sort(key=lambda e: e["size"], reverse=True)

        parent = str(Path(norm).parent)
        if not is_in_scope(parent) or parent == norm:
            parent = None

        log.info("_handle_storage_list: %s → %d entries (complete=%s)", norm, len(entries), complete)
        self._send_json({
            "path": norm,
            "parent": parent,
            "breadcrumb": _storage_breadcrumb(norm),
            "entries": entries,
            "complete": complete,
        })

    def _handle_storage_progress(self):
        norm = self._body_path()
        if not norm or not is_in_scope(norm):
            self._send_error(403, "Path out of scope")
            return
        with _storage_lock:
            c = _storage_cache.get(norm) or {}
            sizes = dict(c.get("sizes", {}))
            complete = bool(c.get("complete"))
        log.debug("_handle_storage_progress: %s → %d sized, complete=%s", norm, len(sizes), complete)
        self._send_json({"sizes": sizes, "complete": complete})

    def _handle_storage_reveal(self):
        norm = self._body_path()
        if not norm or not is_in_scope(norm):
            self._send_error(403, "Path out of scope")
            return
        if not Path(norm).exists():
            self._send_error(404, "Path not found")
            return
        log.info("_handle_storage_reveal: revealing %s in Finder", norm)
        try:
            subprocess.Popen(["open", "-R", norm])
            self._send_json({"revealed": True})
        except OSError as e:
            log.error("_handle_storage_reveal: failed for %s: %s", norm, e)
            self._send_error(500, str(e))

    def _serve_file(self, file_path, content_type=None):
        if not file_path.is_file():
            self._send_error(404, "Not found")
            return
        if not content_type:
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        try:
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except OSError as e:
            log.error("_serve_file: failed to read %s: %s", file_path, e)
            self._send_error(500, "Failed to read file")

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status)

    def log_message(self, format, *args):
        path = urlsplit(self.path).path if hasattr(self, "path") else ""
        quiet = path.startswith(_QUIET_PATHS) or path.endswith("/icon")
        (log.debug if quiet else log.info)("HTTP %s", (format % args))


def write_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def cleanup_pid():
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def check_already_running():
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False


def main():
    if check_already_running():
        log.warning("Server already running on port %d", PORT)
        print(f"Server already running. Open http://{BIND_HOST}:{PORT}")
        sys.exit(0)

    write_pid()
    atexit.register(cleanup_pid)

    log.info("=" * 60)
    log.info("Sweepr starting: Python %s, port %d, pid %d", sys.version.split()[0], PORT, os.getpid())
    log.info("Log file: %s", (BASE_DIR / "sweepr.log"))

    server = ThreadingHTTPServer((BIND_HOST, PORT), RequestHandler)
    log.info("Server listening at http://%s:%d", BIND_HOST, PORT)
    print(f"Sweepr running at http://{BIND_HOST}:{PORT}")

    log.info("Starting background threads: apps, brew, npm, storage")
    threading.Thread(target=get_apps, daemon=True).start()
    threading.Thread(target=get_brew, daemon=True).start()
    threading.Thread(target=get_npm, daemon=True).start()
    threading.Thread(target=_warm_storage_roots, daemon=True).start()

    if "--no-browser" not in sys.argv:
        threading.Thread(target=lambda: webbrowser.open(f"http://{BIND_HOST}:{PORT}"), daemon=True).start()

    def shutdown(signum, frame):
        log.info("Sweepr shutting down (signal %d)", signum)
        cleanup_pid()
        os._exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Sweepr shutting down (KeyboardInterrupt)")
        cleanup_pid()
        os._exit(0)


if __name__ == "__main__":
    main()
