import json
import os
import re as _re
import subprocess
import time
from datetime import datetime, timezone

from lib.log import log

UNINSTALL_TIMEOUT = 900
AUTOREMOVE_TIMEOUT = 300
_BREW_ENV = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_INSTALL_CLEANUP="1")


def scan_homebrew_quick():
    """Names straight from the Cellar and Caskroom folders, with install details
    from each keg's receipt. No `brew` process is started, so this is instant and
    works even when brew itself is failing."""
    t0 = time.time()
    log.info("scan_homebrew_quick: starting")
    cellar = _get_cellar_path()
    info = _local_formula_info(cellar)
    packages = [_blank_package(name, "formula") for name in info]
    caskroom = os.path.join(os.path.dirname(cellar), "Caskroom") if cellar else ""
    cask_count = 0
    try:
        for name in os.listdir(caskroom) if caskroom else []:
            cask_dir = os.path.join(caskroom, name)
            if name.startswith(".") or not os.path.isdir(cask_dir):
                continue
            pkg = _blank_package(name, "cask")
            versions = [v for v in os.listdir(cask_dir) if not v.startswith(".")]
            if versions:
                pkg["version"] = max(versions, key=_version_key)
            meta = _cask_metadata(caskroom, name)
            pkg["apps"] = meta["apps"]
            pkg["has_zap"] = meta["has_zap"]
            packages.append(pkg)
            cask_count += 1
    except OSError as e:
        log.warning("scan_homebrew_quick: cannot read Caskroom %s: %s", caskroom, e)

    _annotate_from_receipts(packages, info, fill_basics=True)
    packages.sort(key=lambda p: p["name"].lower())
    log.info("scan_homebrew_quick: complete — %d formulae, %d casks in %.2fs",
             len(packages) - cask_count, cask_count, time.time() - t0)
    return packages


def _cask_metadata(caskroom, token):
    """The app bundles a cask installed and whether it defines a zap (Homebrew's
    curated list of the app's settings and data), from the newest
    Caskroom/<token>/.metadata/<version>/<timestamp>/Casks/<token>.json."""
    result = {"apps": [], "has_zap": False}
    meta_root = os.path.join(caskroom, token, ".metadata")
    try:
        candidates = []
        for version in os.listdir(meta_root):
            vdir = os.path.join(meta_root, version)
            for stamp in os.listdir(vdir) if os.path.isdir(vdir) else []:
                f = os.path.join(vdir, stamp, "Casks", token + ".json")
                if os.path.isfile(f):
                    candidates.append(f)
        if not candidates:
            return result
        with open(max(candidates, key=os.path.getmtime)) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        log.debug("_cask_metadata: none for %s: %s", token, e)
        return result
    for artifact in data.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        for app in artifact.get("app") or []:
            # entries are "Name.app" or {"target": ...} / ["Name.app", {"target": "Other.app"}]
            if isinstance(app, str) and app.endswith(".app"):
                result["apps"].append(os.path.basename(app))
            elif isinstance(app, dict) and str(app.get("target", "")).endswith(".app"):
                result["apps"][-1:] = [os.path.basename(app["target"])]
        if artifact.get("zap"):
            result["has_zap"] = True
    return result


def cask_app_index():
    """{app bundle file name: cask token} for every installed cask, so the Apps
    tab can tell which apps Homebrew manages."""
    cellar = _get_cellar_path()
    caskroom = os.path.join(os.path.dirname(cellar), "Caskroom") if cellar else ""
    index = {}
    try:
        tokens = [t for t in os.listdir(caskroom) if not t.startswith(".")] if caskroom else []
    except OSError:
        return index
    for token in tokens:
        for app in _cask_metadata(caskroom, token)["apps"]:
            index[app] = token
    return index


def _blank_package(name, pkg_type):
    return {"name": name, "version": "", "description": "", "type": pkg_type,
            # Casks are always installed on request; formulae get this from the receipt
            "installed_on_request": True if pkg_type == "cask" else None,
            "installed_at": None, "size": 0, "outdated": False, "pinned": False, "dependencies": 0}


def _version_key(version):
    """Natural sort key, so 10.0 sorts after 9.0 and 1.10 after 1.9_1."""
    return [(0, int(part)) if part.isdigit() else (1, part) for part in _re.split(r"(\d+)", version) if part]


def _active_keg_version(cellar, name, versions):
    """The keg Homebrew links (opt/<name> points at it), else the newest version."""
    opt = os.path.join(os.path.dirname(cellar), "opt", name)
    try:
        target = os.path.realpath(opt)
        if os.path.dirname(target) == os.path.join(cellar, name):
            version = os.path.basename(target)
            if version in versions:
                return version
    except OSError:
        pass
    return max(versions, key=_version_key)


SERVICE_PLIST_DIRS = (
    os.path.expanduser("~/Library/LaunchAgents"),
    "/Library/LaunchAgents",
    "/Library/LaunchDaemons",
)


def _local_formula_info(cellar):
    """Read each keg's INSTALL_RECEIPT.json straight from the Cellar. This works
    even when `brew info` is failing, and its runtime_dependencies give the reverse
    view brew info lacks: which installed formulae need each package."""
    t0 = time.time()
    info = {}
    if not cellar or not os.path.isdir(cellar):
        log.warning("_local_formula_info: no Cellar at %r", cellar)
        return info
    for name in os.listdir(cellar):
        keg_root = os.path.join(cellar, name)
        if not os.path.isdir(keg_root):
            continue
        versions = [v for v in os.listdir(keg_root) if os.path.isdir(os.path.join(keg_root, v))]
        if not versions:
            continue
        version = _active_keg_version(cellar, name, versions)
        entry = {"version": version, "installed_on_request": None, "installed_at": None,
                 "runtime_deps": [], "used_by": [], "tap": None}
        try:
            with open(os.path.join(keg_root, version, "INSTALL_RECEIPT.json")) as f:
                receipt = json.load(f)
            entry["installed_on_request"] = bool(receipt.get("installed_on_request"))
            entry["tap"] = (receipt.get("source") or {}).get("tap")
            entry["installed_at"] = _format_timestamp(receipt.get("time"))
            entry["runtime_deps"] = [d.get("full_name", "").split("/")[-1]
                                     for d in receipt.get("runtime_dependencies") or []]
        except (OSError, ValueError) as e:
            log.debug("_local_formula_info: no usable receipt for %s: %s", name, e)
        info[name] = entry
    for name, entry in info.items():
        for dep in entry["runtime_deps"]:
            if dep in info:
                info[dep]["used_by"].append(name)
    log.info("_local_formula_info: read %d receipts in %.2fs", len(info), time.time() - t0)
    return info


def _registered_services():
    names = set()
    for d in SERVICE_PLIST_DIRS:
        try:
            for f in os.listdir(d):
                if f.startswith("homebrew.mxcl.") and f.endswith(".plist"):
                    names.add(f[len("homebrew.mxcl."):-len(".plist")])
        except OSError:
            continue
    return names


def _annotate_from_receipts(packages, info, fill_basics=False):
    """Add used_by and service to every package. With fill_basics (the quick list,
    which has no brew info data) also take version, install date and
    installed_on_request from the receipt; None means it couldn't be determined."""
    services = _registered_services()
    app_dirs = ("/Applications", os.path.expanduser("~/Applications"))
    for pkg in packages:
        pkg["used_by"] = []
        if pkg["type"] == "cask" and pkg.get("apps"):
            # Removed by dragging to the Trash: Homebrew still lists the cask, and
            # `brew upgrade` reinstalls the app when a new version comes out
            pkg["app_missing"] = not any(os.path.exists(os.path.join(d, a)) for d in app_dirs for a in pkg["apps"])
        # brew services only manages formulae
        pkg["service"] = pkg["type"] == "formula" and pkg["name"] in services
        if pkg["type"] != "formula":
            continue
        entry = info.get(pkg.get("keg_name") or pkg["name"])
        if entry:
            pkg["used_by"] = sorted(entry["used_by"])
        if entry and not pkg.get("tap"):
            pkg["tap"] = entry["tap"]
        if fill_basics:
            pkg["version"] = entry["version"] if entry else ""
            pkg["installed_at"] = entry["installed_at"] if entry else None
            pkg["installed_on_request"] = entry["installed_on_request"] if entry else None
            pkg["dependencies"] = len(entry["runtime_deps"]) if entry else 0


def suggested_fix(error):
    """Pull the command out of brew errors phrased as 'Please resolve this by
    running:' followed by an indented command line, for example the Xcode
    license prompt ('sudo xcodebuild -license accept')."""
    if not error:
        return None
    lines = error.splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.rstrip().endswith("running:"):
            cmd = lines[i + 1].strip()
            if cmd:
                return cmd
    return None


def scan_homebrew():
    """Returns (packages, error). On failure packages is None and error is a
    short message for the UI, so callers can keep an earlier list instead of
    replacing it with nothing."""
    t0 = time.time()
    log.info("scan_homebrew: starting full scan (brew info --json=v2 --installed)")
    try:
        result = subprocess.run(
            ["brew", "info", "--json=v2", "--installed"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            log.error("scan_homebrew: brew info returned %d: %s", result.returncode, err)
            return None, err or f"brew info exited {result.returncode}"
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        log.error("scan_homebrew: brew info timed out")
        return None, "brew info timed out"
    except OSError as e:
        log.error("scan_homebrew: brew not found or failed: %s", e)
        return None, f"brew not found or failed: {e}"
    except json.JSONDecodeError as e:
        log.error("scan_homebrew: failed to parse brew output: %s", e)
        return None, "could not parse brew output"

    log.debug("scan_homebrew: brew info returned in %.2fs, parsing", time.time() - t0)

    packages = []
    cellar = _get_cellar_path()

    size_paths = []
    for f in data.get("formulae", []):
        pkg = _parse_formula(f, cellar)
        if pkg:
            packages.append(pkg)
            if pkg.get("_cellar_path"):
                size_paths.append(pkg["_cellar_path"])

    sizes = _batch_dir_sizes(size_paths)
    for pkg in packages:
        cp = pkg.pop("_cellar_path", None)
        if cp and cp in sizes:
            pkg["size"] = sizes[cp]

    cask_count = 0
    for c in data.get("casks", []):
        pkg = _parse_cask(c)
        if pkg:
            packages.append(pkg)
            cask_count += 1

    # After the casks are added, so they get service/used_by/app_missing too
    _annotate_from_receipts(packages, _local_formula_info(cellar))
    packages.sort(key=lambda p: p["name"].lower())
    log.info("scan_homebrew: complete — %d formulae, %d casks in %.2fs",
             len(packages) - cask_count, cask_count, time.time() - t0)
    return packages, None


_cellar_path = None


def _get_cellar_path():
    # Resolved once per process: both scan passes need it and it never changes
    global _cellar_path
    if _cellar_path:
        return _cellar_path
    _cellar_path = _find_cellar_path()
    return _cellar_path


def _find_cellar_path():
    try:
        result = subprocess.run(
            ["brew", "--cellar"],
            capture_output=True, text=True, timeout=5,
        )
        path = result.stdout.strip()
        if result.returncode == 0 and os.path.isdir(path):
            log.debug("_get_cellar_path: %s", path)
            return path
        log.warning("_get_cellar_path: brew --cellar gave %r (exit %d), using default",
                    path, result.returncode)
    except Exception as e:
        log.warning("_get_cellar_path: failed, using default: %s", e)
    for default in ("/opt/homebrew/Cellar", "/usr/local/Cellar"):
        if os.path.isdir(default):
            return default
    return ""


def _parse_formula(f, cellar):
    installed = f.get("installed", [])
    if not installed:
        return None
    # The linked keg is the one in use when several versions are installed
    linked = f.get("linked_keg")
    inst = next((i for i in installed if i.get("version") == linked), installed[-1])
    version = inst.get("version", "")
    timestamp = inst.get("time", 0)
    # A renamed formula keeps its old folder name in the Cellar (sdl2-compat is
    # installed as Cellar/sdl2), listed under oldnames or aliases
    keg_name = f["name"]
    if cellar:
        for candidate in [f["name"]] + list(f.get("oldnames") or []) + list(f.get("aliases") or []):
            if os.path.isdir(os.path.join(cellar, candidate)):
                keg_name = candidate
                break
    cellar_path = os.path.join(cellar, keg_name, version) if cellar else None
    return {
        "name": f["name"],
        "keg_name": keg_name,
        "tap": f.get("tap"),
        "version": version,
        "description": f.get("desc", ""),
        "type": "formula",
        "installed_on_request": inst.get("installed_on_request"),  # None if brew omits it: unknown, not "dependency"
        "installed_at": _format_timestamp(timestamp),
        "size": 0,
        "_cellar_path": cellar_path if cellar_path and os.path.isdir(cellar_path) else None,
        "outdated": f.get("outdated", False),
        "pinned": f.get("pinned", False),
        "dependencies": len(f.get("dependencies", [])),
    }


def _parse_cask(c):
    timestamp = c.get("installed_time", 0)
    return {
        "name": c.get("token", ""),
        "tap": c.get("tap"),
        "has_zap": any(isinstance(a, dict) and a.get("zap") for a in c.get("artifacts") or []),
        "apps": [os.path.basename(x) for a in c.get("artifacts") or [] if isinstance(a, dict)
                 for x in (a.get("app") or []) if isinstance(x, str) and x.endswith(".app")],
        "version": c.get("installed", ""),
        "description": c.get("desc", ""),
        "type": "cask",
        "installed_on_request": True,
        "installed_at": _format_timestamp(timestamp),
        "size": 0,
        "outdated": c.get("outdated", False),
        "pinned": False,
        "dependencies": len(c.get("depends_on", {}).get("formula", []) if isinstance(c.get("depends_on"), dict) else []),
    }


def _format_timestamp(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError):
        return None


# Taps that ship with Homebrew; never removed
CORE_TAPS = {"homebrew/core", "homebrew/cask", "homebrew/services", "homebrew/bundle",
             "homebrew/cask-fonts", "homebrew/cask-versions"}


def is_third_party_tap(tap):
    return bool(tap) and tap.lower() not in CORE_TAPS


def untap_if_unused(tap):
    """`brew untap` a third-party tap once nothing installed comes from it. A tap
    left behind keeps getting updated and its packages can come back.
    Returns (untapped, message)."""
    if not is_third_party_tap(tap):
        return False, f"{tap} ships with Homebrew"
    try:
        listed = subprocess.run(["brew", "list", "--full-name"], capture_output=True, text=True,
                                timeout=60, stdin=subprocess.DEVNULL, env=_BREW_ENV)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("untap_if_unused: brew list failed: %s", e)
        return False, "couldn't check what else uses the tap"
    still = [n for n in listed.stdout.split() if n.lower().startswith(tap.lower() + "/")]
    if still:
        log.info("untap_if_unused: keeping %s, still used by %s", tap, still)
        return False, f"{tap} is still used by {', '.join(s.split('/')[-1] for s in still)}"
    try:
        r = subprocess.run(["brew", "untap", tap], capture_output=True, text=True,
                           timeout=120, stdin=subprocess.DEVNULL, env=_BREW_ENV)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("untap_if_unused: brew untap %s failed: %s", tap, e)
        return False, f"brew untap {tap} failed"
    if r.returncode != 0:
        log.warning("untap_if_unused: brew untap %s exited %d: %s", tap, r.returncode, r.stderr.strip())
        return False, r.stderr.strip() or f"brew untap {tap} failed"
    log.info("untap_if_unused: untapped %s", tap)
    return True, ""


def uninstall_package(name, pkg_type, zap=False):
    if not name or not _is_safe_name(name):
        log.warning("uninstall_package: rejected invalid name %r", name)
        return {"success": False, "error": "Invalid package name"}

    log.info("uninstall_package: brew uninstall %s (type=%s)", name, pkg_type)
    try:
        cmd = ["brew", "uninstall"]
        if pkg_type == "cask":
            cmd.append("--cask")
            if zap:
                # Homebrew's own list of the app's settings, caches and data
                cmd.append("--zap")
        cmd.append(name)
        # Large casks with uninstall scripts can take minutes; killing brew midway
        # leaves a half-removed package. stdin is closed so a sudo prompt fails
        # fast instead of hanging on the terminal that started the server.
        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=UNINSTALL_TIMEOUT,
                                stdin=subprocess.DEVNULL, env=_BREW_ENV)
        if result.returncode == 0:
            log.info("uninstall_package: successfully uninstalled %s in %.1fs", name, time.time() - t0)
            removed = _autoremove()
            return {"success": True, "output": result.stdout.strip(), "autoremoved": removed}
        err = result.stderr.strip() or "Unknown error"
        log.error("uninstall_package: failed for %s: %s", name, err)
        return {"success": False, "error": err}
    except subprocess.TimeoutExpired:
        log.error("uninstall_package: timed out uninstalling %s", name)
        return {"success": False, "error": "Operation timed out"}
    except OSError as e:
        log.error("uninstall_package: OSError for %s: %s", name, e)
        return {"success": False, "error": str(e)}


def _autoremove():
    """Run brew autoremove and return the names it removed, so the UI can say so."""
    try:
        log.info("_autoremove: running brew autoremove")
        result = subprocess.run(["brew", "autoremove"], capture_output=True, text=True,
                                timeout=AUTOREMOVE_TIMEOUT, stdin=subprocess.DEVNULL, env=_BREW_ENV)
    except Exception as e:
        log.warning("_autoremove: failed: %s", e)
        return []
    if result.returncode != 0:
        log.warning("_autoremove: exited %d: %s", result.returncode, result.stderr.strip())
        return []
    # Output looks like "==> Autoremoving 2 unneeded formulae:" then one name per line
    removed = [line.strip() for line in result.stdout.splitlines()
               if line.strip() and not line.startswith("==>")]
    log.info("_autoremove: removed %d package(s): %s", len(removed), removed)
    return removed


def _is_safe_name(name):
    return all(c.isalnum() or c in "-_@./+" for c in name)


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
        log.debug("_batch_dir_sizes (brew): computed sizes for %d paths in %.2fs", len(sizes), time.time() - t0)
        return sizes
    except Exception as e:
        log.error("_batch_dir_sizes (brew): failed: %s", e)
        return {}
