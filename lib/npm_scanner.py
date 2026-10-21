import json
import os
import subprocess
import time
from datetime import datetime, timezone

from lib.log import log

# Ship with Node.js itself; uninstalling them breaks npm and node, and npm can't
# reinstall itself once it's gone. Never offered for removal.
PROTECTED_PACKAGES = {"npm", "corepack"}


def scan_npm_globals():
    """Returns (packages, error). error is None on success; on failure packages
    is empty and error says why, so the UI can tell "none installed" from "broken"."""
    t0 = time.time()
    log.info("scan_npm_globals: starting")
    try:
        result = subprocess.run(
            ["npm", "list", "-g", "--json", "--depth=0"],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        log.error("scan_npm_globals: npm list timed out")
        return [], "npm list timed out"
    except OSError as e:
        log.error("scan_npm_globals: npm not found: %s", e)
        return [], "npm was not found on PATH"
    except json.JSONDecodeError as e:
        log.error("scan_npm_globals: failed to parse npm output: %s", e)
        return [], "could not parse npm output"

    npm_root = _get_npm_root()
    if not npm_root:
        log.error("scan_npm_globals: could not determine npm root")
        return [], "could not determine the npm global folder"

    log.debug("scan_npm_globals: npm root = %s", npm_root)

    deps = data.get("dependencies", {})
    packages = []
    pkg_dirs = []
    for name, info in deps.items():
        pkg = _parse_package(name, info, npm_root)
        if pkg:
            packages.append(pkg)
            pkg_dirs.append(os.path.join(npm_root, name))

    sizes = _batch_dir_sizes(pkg_dirs)
    for pkg in packages:
        pkg_dir = os.path.join(npm_root, pkg["name"])
        if pkg_dir in sizes:
            pkg["size"] = sizes[pkg_dir]

    packages.sort(key=lambda p: p["name"].lower())
    log.info("scan_npm_globals: complete — %d packages in %.2fs", len(packages), time.time() - t0)
    return packages, None


def _get_npm_root():
    try:
        result = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True, text=True, timeout=5,
        )
        root = result.stdout.strip()
        if root and os.path.isdir(root):
            return root
        log.warning("_get_npm_root: npm root %r is not a directory", root)
    except Exception as e:
        log.error("_get_npm_root: failed: %s", e)
    return None


def _parse_package(name, info, npm_root):
    version = info.get("version", "")
    pkg_dir = os.path.join(npm_root, name)
    installed_at = _get_install_date(pkg_dir)

    description = ""
    pkg_json = os.path.join(pkg_dir, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json) as f:
                pj = json.load(f)
                description = pj.get("description", "")
        except Exception as e:
            log.warning("_parse_package: failed to read package.json for %s: %s", name, e)

    return {
        "name": name,
        "version": version,
        "description": description,
        "size": 0,
        "installed_at": installed_at,
        "protected": name in PROTECTED_PACKAGES,
    }


def _get_install_date(pkg_dir):
    try:
        stat = os.stat(pkg_dir)
        ts = getattr(stat, "st_birthtime", stat.st_ctime)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError as e:
        log.warning("_get_install_date: failed for %s: %s", pkg_dir, e)
        return None


def uninstall_package(name):
    if not name or not _is_safe_name(name):
        log.warning("uninstall_package (npm): rejected invalid name %r", name)
        return {"success": False, "error": "Invalid package name"}

    log.info("uninstall_package (npm): npm uninstall -g %s", name)
    try:
        result = subprocess.run(
            ["npm", "uninstall", "-g", name],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            log.info("uninstall_package (npm): successfully uninstalled %s", name)
            return {"success": True, "output": result.stdout.strip()}
        err = result.stderr.strip() or "Unknown error"
        log.error("uninstall_package (npm): failed for %s: %s", name, err)
        return {"success": False, "error": err}
    except subprocess.TimeoutExpired:
        log.error("uninstall_package (npm): timed out uninstalling %s", name)
        return {"success": False, "error": "Operation timed out"}
    except OSError as e:
        log.error("uninstall_package (npm): OSError for %s: %s", name, e)
        return {"success": False, "error": str(e)}


def _is_safe_name(name):
    return all(c.isalnum() or c in "-_@/.+" for c in name)


def _batch_dir_sizes(paths):
    valid = [p for p in paths if p and os.path.isdir(p)]
    if not valid:
        return {}
    t0 = time.time()
    try:
        result = subprocess.run(
            ["du", "-sk"] + valid,
            capture_output=True, text=True, timeout=30,
        )
        sizes = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                sizes[parts[1]] = int(parts[0]) * 1024
        log.debug("_batch_dir_sizes (npm): computed sizes for %d paths in %.2fs", len(sizes), time.time() - t0)
        return sizes
    except Exception as e:
        log.error("_batch_dir_sizes (npm): failed: %s", e)
        return {}
