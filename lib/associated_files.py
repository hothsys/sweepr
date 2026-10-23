import os
import subprocess
import time
from pathlib import Path

from lib.log import log
from lib.sizes import disk_usage, is_protected_container

HOME = Path.home()

SEARCH_LOCATIONS = [
    ("Preferences", HOME / "Library" / "Preferences"),
    ("Caches", HOME / "Library" / "Caches"),
    ("Application Support", HOME / "Library" / "Application Support"),
    ("Containers", HOME / "Library" / "Containers"),
    ("Logs", HOME / "Library" / "Logs"),
    ("Launch Agents", HOME / "Library" / "LaunchAgents"),
    ("Saved Application State", HOME / "Library" / "Saved Application State"),
    ("HTTP Storages", HOME / "Library" / "HTTPStorages"),
    ("WebKit Data", HOME / "Library" / "WebKit"),
    ("Cookies", HOME / "Library" / "Cookies"),
    ("Group Containers", HOME / "Library" / "Group Containers"),
    ("Application Scripts", HOME / "Library" / "Application Scripts"),
    ("Crash Reports", HOME / "Library" / "Logs" / "DiagnosticReports"),
    ("Preferences (ByHost)", HOME / "Library" / "Preferences" / "ByHost"),
    ("System Launch Agents", Path("/Library/LaunchAgents")),
    ("System Launch Daemons", Path("/Library/LaunchDaemons")),
    ("Privileged Helpers", Path("/Library/PrivilegedHelperTools")),
]


# Bundle-id tails too generic to match on alone ("com.foo.mac" would claim
# everything starting with "mac-")
GENERIC_TAILS = {
    "app", "mac", "macos", "osx", "desktop", "client", "helper", "launcher", "agent",
    "daemon", "console", "media", "update", "updater", "service", "main", "core",
    "beta", "electron", "installer", "uninstaller", "player", "viewer", "editor",
}
MIN_TAIL_LEN = 5


def find_associated_files(bundle_id, app_name, app_path, other_apps=()):
    """other_apps: the rest of the installed apps (dicts with bundle_id, name, path),
    used to avoid offering another app's data or the other app itself."""
    t0 = time.time()
    log.info("find_associated_files: searching for '%s' (bundle_id=%s)", app_name, bundle_id or "none")
    results = []
    seen_paths = set()
    app_path_resolved = str(Path(app_path).resolve())
    other_app_paths = [str(Path(a["path"]).resolve()) for a in other_apps if a.get("path")]
    # Other apps' ids that are more specific than ours (com.x.DiskDrill.Media vs
    # com.x.DiskDrill): anything matching them is theirs, not ours
    more_specific_ids = [a["bundle_id"].lower() for a in other_apps
                         if bundle_id and a.get("bundle_id")
                         and a["bundle_id"].lower().startswith(bundle_id.lower() + ".")]
    # Other apps sharing our exact bundle id (Photoshop 2025 and 2026)
    sharing_apps = sorted({a["name"] for a in other_apps
                           if bundle_id and a.get("bundle_id") == bundle_id})

    patterns = _build_patterns(bundle_id, app_name)
    log.debug("find_associated_files: patterns = %s", patterns)

    for category, search_dir in SEARCH_LOCATIONS:
        if not search_dir.is_dir():
            continue
        try:
            entries = list(search_dir.iterdir())
        except PermissionError:
            log.debug("find_associated_files: permission denied for %s (%s) — requires Full Disk Access", category, search_dir)
            continue
        for entry in entries:
            entry_name = entry.name
            entry_stem = entry_name.rsplit(".", 1)[0] if "." in entry_name else entry_name
            matched = False
            for pattern in patterns:
                if (
                    entry_name == pattern
                    or entry_name.startswith(pattern + ".")
                    or entry_name.startswith(pattern + "-")
                    or entry_stem == pattern
                ):
                    matched = True
                    break
            if not matched:
                continue
            lower = entry_name.lower()
            if any(lower == oid or lower.startswith(oid + ".") for oid in more_specific_ids):
                log.debug("find_associated_files: %s belongs to a more specific installed app", entry_name)
                continue
            real = str(entry.resolve())
            if real == app_path_resolved or real in seen_paths:
                continue
            seen_paths.add(real)
            results.append(_file_entry(str(entry), category, sharing_apps))

    if bundle_id:
        spotlight_results = _mdfind_search(bundle_id, app_path_resolved)
        for fpath in spotlight_results:
            if fpath in seen_paths:
                continue
            # mdfind returns every bundle with this id: other copies or versions of
            # the app, or an app nested in another product. Never offer an app.
            if fpath.endswith(".app") or ".app/" in fpath:
                log.debug("find_associated_files: skipping Spotlight hit inside an app bundle: %s", fpath)
                continue
            if any(fpath == op or fpath.startswith(op + "/") for op in other_app_paths):
                continue
            seen_paths.add(fpath)
            if not os.path.lexists(fpath):
                continue
            results.append(_file_entry(fpath, "Spotlight", sharing_apps))

    results.sort(key=lambda r: r["category"])
    total_size = sum(r["size"] for r in results)
    log.info("find_associated_files: found %d files (%.1f KB) for '%s' in %.2fs",
             len(results), total_size / 1024, app_name, time.time() - t0)
    return results


def _file_entry(path, category, sharing_apps):
    size, _ = disk_usage(path)
    entry = {
        "path": path,
        "category": category,
        "size": size,
        "is_directory": os.path.isdir(path) and not os.path.islink(path),
    }
    if is_protected_container(path):
        entry["protected"] = True
    if sharing_apps:
        entry["shared_with"] = sharing_apps
    return entry


def _build_patterns(bundle_id, app_name):
    patterns = []
    if bundle_id:
        patterns.append(bundle_id)
        parts = bundle_id.split(".")
        tail = parts[-1] if len(parts) >= 3 else ""
        if len(tail) >= MIN_TAIL_LEN and tail.lower() not in GENERIC_TAILS:
            patterns.append(tail)
    if app_name and len(app_name) >= 3:
        patterns.append(app_name)
        clean = app_name.replace(" ", "")
        if clean != app_name:
            patterns.append(clean)
    return list(dict.fromkeys(p for p in patterns if p))  # drop duplicates, keep order


def _mdfind_search(bundle_id, app_path_resolved):
    log.debug("_mdfind_search: searching for bundle_id=%s", bundle_id)
    try:
        result = subprocess.run(
            ["mdfind", "kMDItemCFBundleIdentifier == '%s'" % bundle_id.replace("\\", "").replace("'", "")],
            capture_output=True, text=True, timeout=5,
        )
        paths = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            resolved = str(Path(line).resolve())
            if resolved == app_path_resolved:
                continue
            if resolved.startswith("/System/") or resolved.startswith("/Library/Managed Preferences/"):
                continue
            paths.append(resolved)
        log.debug("_mdfind_search: %d results for %s", len(paths), bundle_id)
        return paths
    except subprocess.TimeoutExpired:
        log.warning("_mdfind_search: timed out for %s", bundle_id)
        return []
    except OSError as e:
        log.warning("_mdfind_search: failed for %s: %s", bundle_id, e)
        return []
