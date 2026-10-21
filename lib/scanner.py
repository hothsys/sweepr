import os
import plistlib
import subprocess
import time
from pathlib import Path

from lib.log import log
from lib.sizes import disk_usage


SCAN_DIRS = [
    Path("/Applications"),
    Path("/System/Applications"),
    Path.home() / "Applications",
]

SYSTEM_BUNDLE_PREFIXES = (
    "com.apple.",
)

SYSTEM_PATHS = (
    "/System/",
    "/Library/Apple/",
)

SYSTEM_APPS_DIR = Path("/System/Applications")

# Bundle id of the launcher that launchagent/install.sh creates
SELF_BUNDLE_ID = "com.sweepr.launcher"

# How deep to look for a nested .app before calling a folder a leftover.
# Suites like Adobe Creative Cloud keep their apps two or three levels down.
LEFTOVER_APP_SEARCH_DEPTH = 4


def scan_applications(include_size=False):
    t0 = time.time()
    log.info("scan_applications: starting (include_size=%s)", include_size)
    apps = []
    seen_paths = set()
    app_entries = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.is_dir():
            continue
        try:
            entries = sorted(scan_dir.iterdir())
        except PermissionError:
            log.warning("scan_applications: permission denied reading %s", scan_dir)
            continue
        for entry in entries:
            if entry.name.endswith(".webloc") and entry.is_file():
                info = _build_webloc_info(entry)
                if info:
                    apps.append(info)
                    log.debug("scan_applications: found webloc %s", entry.name)
            elif entry.name.endswith(".app") and entry.is_dir():
                _collect_app(entry, app_entries, seen_paths)
            elif entry.is_dir() and not entry.name.startswith("."):
                parent_name = entry.name
                found_app = False
                try:
                    for sub in entry.iterdir():
                        resolved = sub
                        if sub.is_symlink():
                            resolved = Path(os.path.realpath(sub))
                        if resolved.name.endswith(".app") and resolved.is_dir():
                            _collect_app(resolved, app_entries, seen_paths, parent_name, entry if sub.is_symlink() else None)
                            found_app = True
                        elif sub.name.endswith(".app") or (resolved.is_dir() and resolved.name.endswith(".app")):
                            _collect_app(resolved, app_entries, seen_paths, parent_name, entry if sub.is_symlink() else None)
                            found_app = True
                except PermissionError:
                    log.warning("scan_applications: permission denied reading %s", entry)
                    continue
                if not found_app:
                    if _is_system_folder(entry):
                        log.info("scan_applications: %s mirrors a system folder, not a leftover", entry)
                        continue
                    if _contains_app(entry):
                        log.info("scan_applications: %s holds a nested .app, not a leftover", entry)
                        continue
                    info = _build_leftover_info(entry)
                    if info:
                        apps.append(info)
                        log.debug("scan_applications: found leftover folder %s", entry.name)

    log.info("scan_applications: discovered %d app entries, fetching metadata", len(app_entries))
    all_paths = [str(e[0]) for e in app_entries]
    metadata_map = batch_spotlight_metadata(all_paths)

    for app_path, parent_name, discovered_in in app_entries:
        info = _build_app_info(app_path, parent_name, discovered_in,
                               metadata_map.get(str(app_path), {}), include_size)
        if info:
            apps.append(info)

    apps.sort(key=lambda a: a["name"].lower())
    log.info("scan_applications: complete — %d apps in %.2fs", len(apps), time.time() - t0)
    return apps


def _build_webloc_info(path):
    try:
        with open(path, "rb") as f:
            plist = plistlib.load(f)
        url = plist.get("URL", "")
    except Exception as e:
        log.warning("_build_webloc_info: failed to parse %s: %s", path, e)
        url = ""
    size = path.stat().st_size if path.exists() else 0
    return {
        "name": path.stem,
        "bundle_id": "",
        "version": "",
        "path": str(path),
        "size": size,
        "is_system": False,
        "icon_file": "",
        "last_updated": None,
        "last_used": None,
        "category": "Web Shortcut",
        "uninstaller": None,
        "webloc": True,
        "webloc_url": url,
    }


def _build_leftover_info(path):
    if path.name.endswith(".localized"):
        return None
    size = 0
    uninstaller = None
    try:
        for entry in path.iterdir():
            if entry.is_file() and not entry.suffix and _is_uninstaller_name(entry.name):
                uninstaller = {"type": "alias", "path": str(entry), "name": entry.name}
                log.debug("_build_leftover_info: found uninstaller %s in leftover %s", entry.name, path.name)
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    size += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    except PermissionError:
        pass
    if size == 0:
        return None
    return {
        "name": path.name,
        "bundle_id": "",
        "version": "",
        "path": str(path),
        "size": size,
        "is_system": False,
        "icon_file": "",
        "last_updated": None,
        "last_used": None,
        "category": "Leftover",
        "uninstaller": uninstaller,
        "leftover": True,
    }


def _is_system_folder(path):
    """True for a folder on the system volume, or one that macOS merges with a
    folder of the same name in /System/Applications (for example
    /Applications/Utilities, which Finder shows alongside Activity Monitor,
    Terminal and the other Apple utilities)."""
    resolved = str(path.resolve())
    if any(resolved.startswith(prefix) for prefix in SYSTEM_PATHS):
        return True
    return (SYSTEM_APPS_DIR / path.name).is_dir()


def _contains_app(path, max_depth=LEFTOVER_APP_SEARCH_DEPTH):
    base_depth = str(path).rstrip(os.sep).count(os.sep)
    unreadable = []
    # os.walk swallows errors unless onerror is given, so an unreadable subfolder
    # would otherwise look empty
    for dirpath, dirnames, filenames in os.walk(path, onerror=unreadable.append):
        if any(n.endswith(".app") for n in dirnames + filenames):
            return True
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
    if unreadable:
        log.info("_contains_app: %s is partly unreadable (%s), assuming it may hold an app",
                 path, unreadable[0])
        return True  # can't see everything, so don't call it a safe leftover
    return False


def _collect_app(app_path, app_entries, seen_paths, parent_name=None, discovered_in=None):
    real = str(app_path.resolve())
    if real in seen_paths:
        return
    seen_paths.add(real)
    if _is_uninstaller_name(app_path.stem):
        return
    plist_path = app_path / "Contents" / "Info.plist"
    if plist_path.is_file():
        app_entries.append((app_path, parent_name, discovered_in))


def _build_app_info(app_path, parent_name, discovered_in, metadata, include_size):
    plist_path = app_path / "Contents" / "Info.plist"
    try:
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
    except Exception as e:
        log.warning("_build_app_info: failed to read plist for %s: %s", app_path, e)
        return None

    name = (
        plist.get("CFBundleDisplayName")
        or plist.get("CFBundleName")
        or app_path.stem
    )
    if parent_name and parent_name.lower() != name.lower():
        name = f"{name} ({parent_name})"
    bundle_id = plist.get("CFBundleIdentifier", "")
    version = plist.get("CFBundleShortVersionString", plist.get("CFBundleVersion", ""))
    icon_file = plist.get("CFBundleIconFile", "")

    uninstaller = find_uninstaller(app_path, parent_name, discovered_in)

    return {
        "name": name,
        "bundle_id": bundle_id,
        "version": str(version),
        "path": str(app_path),
        "size": calculate_dir_size(app_path) if include_size else 0,
        "is_system": is_system_app(app_path, bundle_id),
        "is_self": bundle_id == SELF_BUNDLE_ID,
        "icon_file": icon_file,
        "last_updated": metadata.get("kMDItemContentModificationDate"),
        "last_used": metadata.get("kMDItemLastUsedDate"),
        "category": metadata.get("kMDItemAppStoreCategory"),
        "uninstaller": uninstaller,
    }


def get_app_info(app_path, parent_name=None, discovered_in=None):
    metadata = get_spotlight_metadata(str(app_path))
    return _build_app_info(app_path, parent_name, discovered_in, metadata, True)


UNINSTALL_KEYWORDS = ("uninstall", "remover", "remove", "cleanup", "clean up", "unsetup")


def _list_dir(path):
    """Directory entries, or [] if it's missing or unreadable (one unreadable
    folder inside one app must not abort the whole app scan)."""
    try:
        return list(path.iterdir())
    except OSError as e:
        if path.exists():
            log.debug("_list_dir: cannot read %s: %s", path, e)
        return []


def find_uninstaller(app_path, parent_name=None, discovered_in=None):
    # 1. Nested .app bundles in Contents/Frameworks/ or Contents/Helpers/
    for subdir in ("Frameworks", "Helpers"):
        search_dir = app_path / "Contents" / subdir
        if search_dir.is_dir():
            for entry in _list_dir(search_dir):
                if entry.name.endswith(".app") and _is_uninstaller_name(entry.name):
                    log.debug("find_uninstaller: found nested app uninstaller %s for %s", entry.name, app_path.name)
                    return {"type": "app", "path": str(entry), "name": entry.stem}

    # 2. Executables in Contents/MacOS/ with uninstall-related names
    macos_dir = app_path / "Contents" / "MacOS"
    if macos_dir.is_dir():
        for entry in _list_dir(macos_dir):
            if entry.is_file() and os.access(entry, os.X_OK) and _is_uninstaller_name(entry.name):
                log.debug("find_uninstaller: found executable uninstaller %s for %s", entry.name, app_path.name)
                return {"type": "executable", "path": str(entry), "name": entry.name}

    # 3. Shell scripts in Contents/Resources/
    resources_dir = app_path / "Contents" / "Resources"
    if resources_dir.is_dir():
        for entry in _list_dir(resources_dir):
            if entry.is_file() and entry.suffix in (".sh", ".bash", ".zsh", "") and _is_uninstaller_name(entry.name):
                if entry.suffix in (".sh", ".bash", ".zsh") or os.access(entry, os.X_OK):
                    log.debug("find_uninstaller: found script uninstaller %s for %s", entry.name, app_path.name)
                    return {"type": "script", "path": str(entry), "name": entry.name}

    # 4. Sibling entries in the parent directory
    search_dirs = set()
    parent_dir = app_path.parent
    if parent_dir != app_path and parent_dir.name != "Applications":
        search_dirs.add(parent_dir)
    if discovered_in is not None:
        search_dirs.add(discovered_in)

    for search_parent in search_dirs:
        try:
            for entry in search_parent.iterdir():
                if entry.name.startswith("."):
                    continue
                if entry.resolve() == app_path.resolve():
                    continue
                resolved = Path(os.path.realpath(entry)) if entry.is_symlink() else entry
                if _is_uninstaller_name(entry.name):
                    if resolved.is_dir() and resolved.name.endswith(".app"):
                        log.debug("find_uninstaller: found sibling app uninstaller %s for %s", entry.name, app_path.name)
                        return {"type": "app", "path": str(resolved), "name": entry.stem}
                    if resolved.is_dir() and resolved.name.endswith(".bundle"):
                        exec_path = _bundle_executable(resolved)
                        if exec_path:
                            log.debug("find_uninstaller: found bundle uninstaller %s for %s", entry.name, app_path.name)
                            return {"type": "executable", "path": exec_path, "name": entry.name}
                    if entry.is_file() and not entry.suffix:
                        log.debug("find_uninstaller: found alias/file uninstaller %s for %s", entry.name, app_path.name)
                        return {"type": "alias", "path": str(entry), "name": entry.name}
        except PermissionError:
            log.warning("find_uninstaller: permission denied reading %s", search_parent)

    # 5. Separate .app in /Applications with matching uninstall name
    if not _is_uninstaller_name(app_path.stem):
        app_stem = app_path.stem.lower()
        try:
            for sibling in app_path.parent.iterdir():
                if sibling == app_path or not sibling.name.endswith(".app"):
                    continue
                if _is_uninstaller_name(sibling.name) and _names_overlap(sibling.stem, app_stem):
                    log.debug("find_uninstaller: found sibling uninstaller app %s for %s", sibling.name, app_path.name)
                    return {"type": "app", "path": str(sibling), "name": sibling.stem}
        except PermissionError:
            log.warning("find_uninstaller: permission denied reading parent of %s", app_path)

    return None


def _is_uninstaller_name(name):
    lower = name.lower()
    return any(kw in lower for kw in UNINSTALL_KEYWORDS)


def _names_overlap(a, b):
    skip = {"the", "app", "and", "for", "remove", "uninstall", "client", "desktop"}
    words_a = {w.lower() for w in a.replace("-", " ").replace("_", " ").split() if len(w) > 2 and w.lower() not in skip}
    words_b = {w.lower() for w in b.replace("-", " ").replace("_", " ").split() if len(w) > 2 and w.lower() not in skip}
    return bool(words_a & words_b)


def _bundle_executable(bundle_path):
    plist_path = bundle_path / "Contents" / "Info.plist"
    if plist_path.is_file():
        try:
            with open(plist_path, "rb") as f:
                plist = plistlib.load(f)
            exe = plist.get("CFBundleExecutable", "")
            if exe:
                exe_path = bundle_path / "Contents" / "MacOS" / exe
                if exe_path.is_file():
                    return str(exe_path)
        except Exception as e:
            log.warning("_bundle_executable: failed to read plist for %s: %s", bundle_path, e)
    macos_dir = bundle_path / "Contents" / "MacOS"
    if macos_dir.is_dir():
        for f in macos_dir.iterdir():
            if f.is_file() and os.access(f, os.X_OK):
                return str(f)
    return None


MDLS_ATTRS = [
    "kMDItemContentModificationDate",
    "kMDItemLastUsedDate",
    "kMDItemAppStoreCategory",
]


def batch_spotlight_metadata(app_paths):
    if not app_paths:
        return {}
    t0 = time.time()
    # mdls stops at the first path it can't find and prints the error to stdout
    # with exit code 0, shifting every later value, so only pass real paths
    existing = [p for p in app_paths if os.path.exists(p)]
    metadata_map = _mdls_batch(existing)
    log.info("batch_spotlight_metadata: fetched metadata for %d apps in %.2fs",
             len(metadata_map), time.time() - t0)
    return metadata_map


def _mdls_batch(paths):
    """One mdls call for many paths. Values come back null-separated, one per
    attribute (in sorted attribute order) per path. If the count is off, part of
    the output can't be trusted, so split the batch and retry the halves."""
    if not paths:
        return {}
    sorted_attrs = sorted(MDLS_ATTRS)
    args = ["mdls", "-raw", "-nullMarker", ""]
    for attr in MDLS_ATTRS:
        args.extend(["-name", attr])
    args.extend(paths)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("_mdls_batch: mdls failed for %d paths: %s", len(paths), e)
        return {}
    values = result.stdout.split("\0")
    if len(values) != len(paths) * len(sorted_attrs):
        if len(paths) == 1:
            log.warning("_mdls_batch: unexpected mdls output for %s, skipping its metadata", paths[0])
            return {}
        log.warning("_mdls_batch: got %d values for %d paths, retrying in halves",
                    len(values), len(paths))
        mid = len(paths) // 2
        merged = _mdls_batch(paths[:mid])
        merged.update(_mdls_batch(paths[mid:]))
        return merged

    metadata_map = {}
    for i, path in enumerate(paths):
        meta = {}
        for j, attr in enumerate(sorted_attrs):
            val = values[i * len(sorted_attrs) + j].strip()
            if not val or val == "(null)":
                continue
            if attr in ("kMDItemContentModificationDate", "kMDItemLastUsedDate"):
                val = val.replace(" +0000", "Z")
            meta[attr] = val.strip('"')
        metadata_map[path] = meta
    return metadata_map


def get_spotlight_metadata(app_path):
    try:
        args = ["mdls"]
        for attr in MDLS_ATTRS:
            args.extend(["-name", attr])
        args.append(app_path)
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        metadata = {}
        for line in result.stdout.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if value == "(null)" or not value:
                continue
            if key in ("kMDItemContentModificationDate", "kMDItemLastUsedDate"):
                value = value.replace(" +0000", "Z")
            metadata[key] = value.strip('"')
        return metadata
    except Exception as e:
        log.warning("get_spotlight_metadata: failed for %s: %s", app_path, e)
        return {}


def is_system_app(app_path, bundle_id):
    resolved = str(app_path.resolve())
    for prefix in SYSTEM_PATHS:
        if resolved.startswith(prefix):
            return True
    if bundle_id:
        for prefix in SYSTEM_BUNDLE_PREFIXES:
            if bundle_id.startswith(prefix):
                return True
    return False


def calculate_dir_size(path):
    size, complete = disk_usage(path)
    if not complete:
        log.debug("calculate_dir_size: %s was only partly readable", path)
    return size
