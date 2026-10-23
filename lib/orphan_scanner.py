import os
import plistlib
import re
import subprocess
import time
from pathlib import Path

from lib.log import log
from lib.sizes import disk_usage, is_protected_container

HOME = Path.home()

# Deliberately not scanned:
# - ~/Library/Accounts holds the macOS Internet Accounts database.
# - Group Containers are shared between apps and extensions and are usually named
#   <TeamID>.<name>, which can't be matched to an installed app by name.
# - Launch agent/daemon plists: the Agents tab judges those by whether their
#   executable still exists, which is far more reliable than matching names.
SCAN_LOCATIONS = [
    ("Preferences", HOME / "Library" / "Preferences"),
    ("Preferences (ByHost)", HOME / "Library" / "Preferences" / "ByHost"),
    ("Application Support", HOME / "Library" / "Application Support"),
    ("Caches", HOME / "Library" / "Caches"),
    ("Containers", HOME / "Library" / "Containers"),
    ("Saved Application State", HOME / "Library" / "Saved Application State"),
    ("HTTP Storages", HOME / "Library" / "HTTPStorages"),
    ("Privileged Helpers", Path("/Library/PrivilegedHelperTools")),
]

# Apple-owned identifiers that don't start with com.apple
SYSTEM_PREFIXES = (
    "com.apple",
    "group.com.apple.",
    ".GlobalPreferences",
    "MobileMeAccounts",
    "loginwindow",
    "pbs",
    "systemgroup.",
    "org.cups.",
    "is.workflow.",
    "group.is.workflow.",
    "org.swift.",
    "com.openssh.",
    "edu.mit.Kerberos",      # macOS Kerberos configuration
    "org.sparkle-project.",  # update framework shared by many apps
    # Default cache folders of libraries embedded in many apps; named after the
    # library, so they can't be tied to (or blamed on) any one app
    "com.onevcat.Kingfisher.",
    "com.hackemist.SDImageCache",
    "com.crashlytics.",
    "io.sentry",
    "com.launchdarkly.",
)

# Words that appear in many unrelated bundle ids; a match on one of these says
# nothing about whether the entry's app is installed
GENERIC_PARTS = {
    "com", "org", "net", "io", "app", "apps", "mac", "macos", "osx", "desktop", "client",
    "helper", "helpers", "agent", "daemon", "service", "services", "installer", "uninstaller",
    "update", "updater", "launcher", "plugin", "extension", "widget", "server", "core", "main",
    "default", "cache", "data", "user", "pro", "lite", "free", "beta", "the", "inc", "llc",
}

# Apps that tools keep outside the Applications folders, so the app scan never
# sees them. Their data counts as installed while the tool's folder exists.
TOOL_OWNED = {
    # Playwright's private WebKit browser (Playwright.app), under ms-playwright/webkit-*
    "org.webkit.playwright": HOME / "Library" / "Caches" / "ms-playwright",
}

LAUNCHD_PLIST_DIRS = (Path("/Library/LaunchDaemons"), Path("/Library/LaunchAgents"),
                      HOME / "Library" / "LaunchAgents")

SKIP_NAMES = {
    ".DS_Store", ".localized", "CloudDocs",
    "Spotlight", "AppleMediaServices",
}


def scan_orphans(known_bundle_ids, known_app_names):
    t0 = time.time()
    log.info("scan_orphans: starting (known_ids=%d, known_names=%d)",
             len(known_bundle_ids), len(known_app_names))
    _spotlight_cache.clear()  # per scan: apps get installed and removed between scans
    _vendor_cache.clear()
    known_ids_lower = {bid.lower() for bid in known_bundle_ids if bid}
    # Products with a launchd job are installed even without an .app (security
    # and management agents), so their labels count as known ids
    helpers_in_use = _helpers_in_use()
    known_ids_lower |= {n.lower() for n in helpers_in_use if n.count(".") >= 2}
    known_ids_lower |= {bid for bid, folder in TOOL_OWNED.items() if folder.is_dir()}
    known_names_lower = {n.lower() for n in known_app_names if n}
    known_name_parts = set()
    for n in known_names_lower:
        known_name_parts.add(n)
        known_name_parts.add(n.replace(" ", ""))
    # Extract org names from bundle IDs (e.g., "jetbrains" from "com.jetbrains.intellij")
    for bid in known_ids_lower:
        parts = bid.split(".")
        for part in parts:
            if part not in GENERIC_PARTS:
                known_name_parts.add(part)

    orphans = []
    seen = set()

    for category, scan_dir in SCAN_LOCATIONS:
        if not scan_dir.is_dir():
            continue
        try:
            entries = list(scan_dir.iterdir())
            log.debug("scan_orphans: scanning %s (%d entries)", category, len(entries))
        except PermissionError:
            log.warning("scan_orphans: permission denied for %s (%s)", category, scan_dir)
            continue

        for entry in entries:
            name = entry.name
            if name in SKIP_NAMES or name.startswith("."):
                continue
            if any(name.startswith(p) for p in SYSTEM_PREFIXES):
                continue

            entry_id = _extract_bundle_id(name)
            # Only reverse-DNS names (com.vendor.app) can be tied to an app with any
            # confidence. Plain names like "Accounts4.sqlite", "MobileSync" or "pip"
            # belong to macOS, devices or command-line tools as often as to apps.
            if not entry_id:
                continue
            entry_name_clean = _clean_name(name)

            if _matches_known(entry_id, entry_name_clean, known_ids_lower, known_name_parts):
                continue
            if category == "Privileged Helpers" and name in helpers_in_use:
                continue

            real = str(entry.resolve())
            if real in seen:
                continue
            seen.add(real)

            # Last check: an app with this id installed somewhere the app scan
            # doesn't look (/Library/Application Support/..., a build folder)
            if _installed_elsewhere(entry_id):
                log.debug("scan_orphans: %s has an installed app elsewhere, skipping", name)
                continue

            size, complete = disk_usage(entry)
            # Empty, or not fully readable (TCC-protected): we can't tell what it holds
            if size == 0 or not complete:
                continue

            app_name = _derive_app_name(entry_id or name)
            orphans.append({
                "path": str(entry),
                "category": category,
                "size": size,
                "is_directory": entry.is_dir(),
                "likely_app": app_name,
                "bundle_id": entry_id or name,
                "protected": is_protected_container(entry),
            })

    for o in orphans:
        o["vendor"] = _extract_vendor(o["bundle_id"])

    vendor_groups = {}
    for o in orphans:
        vendor = o["vendor"]
        if vendor not in vendor_groups:
            vendor_groups[vendor] = {"vendor": vendor, "apps": {}, "total_size": 0, "file_count": 0}
        app_name = o["likely_app"]
        if app_name not in vendor_groups[vendor]["apps"]:
            vendor_groups[vendor]["apps"][app_name] = {"app_name": app_name, "files": [], "total_size": 0}
        vendor_groups[vendor]["apps"][app_name]["files"].append(o)
        vendor_groups[vendor]["apps"][app_name]["total_size"] += o["size"]
        vendor_groups[vendor]["total_size"] += o["size"]
        vendor_groups[vendor]["file_count"] += 1

    result = []
    for vg in vendor_groups.values():
        vg["apps"] = sorted(vg["apps"].values(), key=lambda a: a["total_size"], reverse=True)
        result.append(vg)
    result.sort(key=lambda g: g["total_size"], reverse=True)
    total_size = sum(g["total_size"] for g in result)
    log.info("scan_orphans: complete — %d vendor groups, %d orphans, %.1f MB in %.2fs",
             len(result), len(orphans), total_size / 1024 / 1024, time.time() - t0)
    return result


_spotlight_cache = {}


def _installed_elsewhere(entry_id):
    """True when Spotlight knows an app bundle with this id, or a parent of it,
    outside the Trash. Parents cover extensions and helpers, whose ids extend the
    app's (com.synology.SynologyDrive.FinderHelper.FinderSync belongs to
    com.synology.SynologyDrive)."""
    bid = entry_id
    for suffix in (".plist", ".savedState"):
        if bid.endswith(suffix):
            bid = bid[:-len(suffix)]
    parts = bid.split(".")
    # Down to three components: com.vendor alone would match any of the vendor's apps
    if any(_spotlight_has(".".join(parts[:n])) for n in range(len(parts), 2, -1)):
        return True
    # Extensions and helpers nested inside an app aren't indexed by Spotlight and
    # can have ids unrelated to the app's (Synology Drive is com.synology.CloudStationUI
    # but ships com.synology.SynologyDrive.FinderHelper.FinderSync), so look inside
    # the vendor's installed apps
    lower = bid.lower()
    for nested in _vendor_bundle_ids(".".join(parts[:2])):
        if lower == nested or lower.startswith(nested + "."):
            log.info("_installed_elsewhere: %s is bundled inside an installed %s app", bid, parts[1])
            return True
    return False


_vendor_cache = {}
_NESTED_SUFFIXES = (".app", ".appex", ".xpc")


def _vendor_bundle_ids(vendor):
    """Lowercased ids of every app, extension and XPC service inside the vendor's
    installed apps (vendor like "com.synology"). Cached per scan."""
    key = vendor.lower()
    if key in _vendor_cache:
        return _vendor_cache[key]
    ids = set()
    query = "kMDItemCFBundleIdentifier == '%s.*'c" % vendor.replace("\\", "").replace("'", "")
    try:
        out = subprocess.run(["mdfind", query], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("_vendor_bundle_ids: mdfind failed for %s: %s", vendor, e)
        out = ""
    for app in out.splitlines():
        if app.endswith(".app") and "/.Trash/" not in app:
            ids |= _nested_ids(app)
    _vendor_cache[key] = ids
    return ids


def _nested_ids(app_path, max_depth=7):
    ids = set()
    base = app_path.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _ in os.walk(app_path, onerror=lambda e: None):
        if dirpath.endswith(_NESTED_SUFFIXES):
            try:
                with open(os.path.join(dirpath, "Contents", "Info.plist"), "rb") as f:
                    bid = plistlib.load(f).get("CFBundleIdentifier")
                if isinstance(bid, str):
                    ids.add(bid.lower())
            except Exception:
                pass
        # Frameworks and localizations never hold extensions; skip them to stay fast
        dirnames[:] = [d for d in dirnames
                       if not d.endswith((".framework", ".lproj", ".dylib")) and d not in ("_CodeSignature", "Headers")]
        if dirpath.count(os.sep) - base >= max_depth:
            dirnames[:] = []
    return ids


def _spotlight_has(bid):
    key = bid.lower()
    if key in _spotlight_cache:
        return _spotlight_cache[key]
    query = "kMDItemCFBundleIdentifier == '%s'c" % bid.replace("\\", "").replace("'", "")
    try:
        out = subprocess.run(["mdfind", query], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("_installed_elsewhere: mdfind failed for %s: %s", bid, e)
        return True  # can't confirm it's gone, so don't offer it
    paths = [p for p in out.splitlines() if p.strip() and "/.Trash/" not in p]
    found = bool(paths)
    if found:
        log.info("_installed_elsewhere: %s is installed at %s", bid, paths[0])
    _spotlight_cache[key] = found
    return found


def _helpers_in_use():
    """Names of privileged helper tools that a LaunchDaemon/Agent plist still
    references. A helper in use belongs to an installed product whatever it's called."""
    names = set()
    for d in LAUNCHD_PLIST_DIRS:
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for plist_path in entries:
            if not plist_path.name.endswith(".plist"):
                continue
            # The plist's own name usually matches its helper; count it even if the
            # plist can't be parsed, so an unreadable plist never orphans its helper
            names.add(plist_path.name[:-len(".plist")])
            try:
                with open(plist_path, "rb") as f:
                    plist = plistlib.load(f)
            except Exception:
                continue
            args = plist.get("ProgramArguments") or []
            for exe in [plist.get("Program")] + list(args[:1]):
                if isinstance(exe, str) and exe.startswith("/Library/PrivilegedHelperTools/"):
                    names.add(os.path.basename(exe))
            label = plist.get("Label")
            if isinstance(label, str):
                names.add(label)
    return names


def _extract_bundle_id(name):
    stem = name
    if stem.endswith(".plist"):
        stem = stem[:-6]
    if stem.endswith(".savedState"):
        stem = stem[:-11]
    if stem.count(".") >= 2:
        return stem
    return ""


def _clean_name(name):
    stem = name
    for suffix in (".plist", ".savedState", ".app"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
    return stem.lower()


def _matches_known(entry_id, entry_name_clean, known_ids_lower, known_name_parts):
    if entry_id:
        eid = entry_id.lower()
        for kid in known_ids_lower:
            if eid == kid or eid.startswith(kid + ".") or kid.startswith(eid + "."):
                return True
        # Check if any part of the entry's bundle ID matches known parts
        for part in eid.split("."):
            if part and len(part) > 2 and part in known_name_parts:
                return True
    if entry_name_clean in known_name_parts:
        return True
    # Check if the entry name is contained in any known name or vice versa
    if len(entry_name_clean) > 3:
        for kn in known_name_parts:
            if len(kn) > 3 and (entry_name_clean in kn or kn in entry_name_clean):
                return True
    return False


KNOWN_VENDORS = {
    "adobe": "Adobe",
    "microsoft": "Microsoft",
    "google": "Google",
    "mozilla": "Mozilla",
    "spotify": "Spotify",
    "dropbox": "Dropbox",
    "slack": "Slack",
    "zoom": "Zoom",
    "jetbrains": "JetBrains",
    "docker": "Docker",
    "github": "GitHub",
    "amazon": "Amazon",
    "synology": "Synology",
    "parallels": "Parallels",
    "oracle": "Oracle",
    "vmware": "VMware",
    "1password": "1Password",
    "telegram": "Telegram",
    "signal": "Signal",
    "notion": "Notion",
    "figma": "Figma",
    "postman": "Postman",
    "sublime": "Sublime Text",
    "valve": "Valve",
    "epic": "Epic Games",
    "ubf8t346g9": "Microsoft",
    "wondershare": "Wondershare",
    "synology": "Synology",
}


def _extract_vendor(identifier):
    name = identifier.lower()
    for suffix in (".plist", ".savedState"):
        if name.endswith(suffix):
            name = name[:-(len(suffix))]
    parts = name.split(".")
    # Check bundle ID parts against known vendors
    for part in parts:
        if part in KNOWN_VENDORS:
            return KNOWN_VENDORS[part]
    # For bundle IDs like com.org.app, use the org part
    if len(parts) >= 3:
        org = parts[1] if parts[0] in ("com", "org", "net", "io") else parts[0]
        if org in KNOWN_VENDORS:
            return KNOWN_VENDORS[org]
        return org.capitalize()
    # For plain names, check if they start with a known vendor
    for vendor_key, vendor_name in KNOWN_VENDORS.items():
        if name.startswith(vendor_key):
            return vendor_name
    # Fallback: use the name itself
    return identifier.split(".")[0].capitalize() if "." in identifier else identifier


def _derive_app_name(identifier):
    name = identifier
    for suffix in (".plist", ".savedState"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    parts = name.split(".")
    if len(parts) >= 3:
        app_part = parts[-1]
        org_part = parts[-2] if len(parts) > 2 else ""
        app_part = re.sub(r'([a-z])([A-Z])', r'\1 \2', app_part)
        if org_part and org_part.lower() not in ("com", "org", "net", "io", "app", "mac", "macos"):
            org_part = org_part.capitalize()
            return f"{org_part} {app_part}"
        return app_part
    return name
