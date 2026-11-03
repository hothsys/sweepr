import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone

from lib.log import log

# Ship with Node.js itself; uninstalling them breaks npm and node, and npm can't
# reinstall itself once it's gone. Never offered for removal.
PROTECTED_PACKAGES = {"npm", "corepack"}


HOME = os.path.expanduser("~")


def _version_key(name):
    return [int(p) if p.isdigit() else -1 for p in re.split(r"[^0-9]+", name) if p]


def _nvm_npm():
    """npm from nvm's default version (alias/default), else its newest version."""
    nvm_dir = os.environ.get("NVM_DIR") or os.path.join(HOME, ".nvm")
    versions_dir = os.path.join(nvm_dir, "versions", "node")
    try:
        versions = [v for v in os.listdir(versions_dir) if v.startswith("v")]
    except OSError:
        return None
    if not versions:
        return None
    versions.sort(key=_version_key, reverse=True)
    chosen = versions[0]
    try:
        with open(os.path.join(nvm_dir, "alias", "default")) as f:
            alias = f.read().strip()
        # "16", "v16.20", "v16.20.2" pick the newest match; "node", "lts/*" etc. keep the newest
        wanted = alias.lstrip("v")
        if wanted and wanted[0].isdigit():
            match = [v for v in versions if v[1:] == wanted or v[1:].startswith(wanted + ".")]
            if match:
                chosen = match[0]
    except OSError:
        pass
    npm = os.path.join(versions_dir, chosen, "bin", "npm")
    return npm if os.access(npm, os.X_OK) else None


def find_npm():
    """Path to the npm to use, or None.

    PATH comes first (what the user's shell would run). It's often missing npm,
    though: Node version managers add themselves to PATH from shell startup
    files (nvm frequently lazily), which a server started by launchd, by
    Sweepr.app, or from a non-interactive shell never runs. So the usual install
    locations are checked too.
    """
    found = shutil.which("npm")
    if found:
        return found
    candidates = [
        _nvm_npm(),
        os.path.join(HOME, "Library", "Application Support", "fnm", "aliases", "default", "bin", "npm"),
        os.path.join(HOME, ".local", "share", "fnm", "aliases", "default", "bin", "npm"),
        os.path.join(HOME, ".fnm", "aliases", "default", "bin", "npm"),
        os.path.join(HOME, ".volta", "bin", "npm"),
        os.path.join(HOME, ".asdf", "shims", "npm"),
        os.path.join(HOME, ".local", "share", "mise", "shims", "npm"),
        "/opt/homebrew/bin/npm",
        "/usr/local/bin/npm",
    ]
    for c in candidates:
        if c and os.access(c, os.X_OK):
            log.info("find_npm: npm not on PATH, using %s", c)
            return c
    log.warning("find_npm: npm not found on PATH or in nvm/fnm/Volta/asdf/mise/Homebrew locations")
    return None


def _npm_env(npm):
    """npm runs itself with `#!/usr/bin/env node`, so its own folder (where that
    Node's `node` lives) has to come first on PATH."""
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(npm) + os.pathsep + env.get("PATH", "")
    return env


NOT_FOUND = ("npm was not found (checked PATH, nvm, fnm, Volta, asdf, mise and Homebrew)")


def scan_npm_globals():
    """Returns (packages, error). error is None on success; on failure packages
    is empty and error says why, so the UI can tell "none installed" from "broken"."""
    t0 = time.time()
    npm = find_npm()
    if not npm:
        return [], NOT_FOUND
    log.info("scan_npm_globals: starting (%s)", npm)
    try:
        result = subprocess.run(
            [npm, "list", "-g", "--json", "--depth=0"],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, env=_npm_env(npm),
        )
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        log.error("scan_npm_globals: npm list timed out")
        return [], "npm list timed out"
    except OSError as e:
        log.error("scan_npm_globals: could not run %s: %s", npm, e)
        return [], f"could not run {npm}: {e}"
    except json.JSONDecodeError as e:
        log.error("scan_npm_globals: failed to parse npm output: %s", e)
        return [], "could not parse npm output"

    npm_root = _get_npm_root(npm)
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


def _get_npm_root(npm):
    try:
        result = subprocess.run(
            [npm, "root", "-g"], env=_npm_env(npm),
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
    npm = find_npm()
    if not npm:
        return {"success": False, "error": NOT_FOUND}
    try:
        result = subprocess.run(
            [npm, "uninstall", "-g", name], env=_npm_env(npm),
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
