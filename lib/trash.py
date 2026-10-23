import os
import plistlib
import signal
import subprocess
import time
from pathlib import Path

from lib.log import log
from lib.sizes import CONTAINER_MARKER

PROTECTED_CONTAINER_HELP = (
    "macOS protects this app container: other programs, Finder included, aren't allowed to "
    "remove it. It's harmless to leave. To remove it, give your terminal app (Terminal, "
    "iTerm, Hyper...) Full Disk Access in System Settings > Privacy & Security, delete the "
    "folder from it, then turn the access off again.")

PROTECTED_PREFIXES = (
    "/System/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/Library/Apple/",
)

# Sweepr's project root (lib/ is one level down), not just lib/
SELF_DIR = str(Path(__file__).parent.parent.resolve())

# Folders that must never be trashed themselves, though items inside them can be.
PROTECTED_EXACT = {
    str(Path(p).resolve()) for p in (
        "/",
        "/Applications",
        "/Applications/Utilities",
        "/Library",
        "/Users",
        Path.home(),
        Path.home() / "Applications",
        Path.home() / "Library",
        # Sweepr's own launcher and LaunchAgent; remove these with launchagent/uninstall.sh
        "/Applications/Sweepr.app",
        Path.home() / "Library" / "LaunchAgents" / "com.sweepr.server.plist",
    )
}

SYSTEM_APPS_DIR = Path("/System/Applications")

QUIT_GRACE_SECONDS = 8  # time a normally-quitting app gets before it's forced


def move_to_trash(paths):
    log.info("move_to_trash: trashing %d path(s)", len(paths))
    success = []
    failed = []
    for p in paths:
        ok, err = _trash_single(p)
        if ok:
            log.info("move_to_trash: trashed %s", p)
            success.append(p)
        else:
            log.error("move_to_trash: failed to trash %s: %s", p, err)
            failed.append({"path": p, "error": err})
    log.info("move_to_trash: done — %d succeeded, %d failed", len(success), len(failed))
    return {"success": success, "failed": failed}


def running_pids(app_path):
    """PIDs of processes running from inside the app bundle that belong to the
    app: the app itself plus its helpers, XPC services and embedded login items.
    Matching on the bundle path (not the executable name) can't confuse two apps
    that share an executable name.

    App extensions (.appex: widgets, Share and Finder extensions) are excluded.
    macOS hosts and relaunches them on its own whether or not the app is open
    (Office's widgets run permanently), so they neither mean the app is running
    nor can be stopped for good; they go away once the bundle is gone."""
    prefix = str(Path(app_path).resolve()).rstrip("/") + "/"
    try:
        result = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("running_pids: ps failed: %s", e)
        return []
    pids = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].startswith(prefix) and ".appex/" not in parts[1]:
            try:
                pids.append(int(parts[0]))
            except ValueError:
                pass
    return [pid for pid in pids if pid != os.getpid()]


def _main_executable(app_path):
    plist_path = Path(app_path) / "Contents" / "Info.plist"
    try:
        with open(plist_path, "rb") as f:
            exe = plistlib.load(f).get("CFBundleExecutable", "")
    except Exception:
        return None
    if not exe:
        return None
    return str(Path(app_path).resolve() / "Contents" / "MacOS" / exe)


def check_app_running(app_path):
    """True only when the app's own main program is running, i.e. the app is
    open. Helpers and extensions running in the background don't count here
    (removal still stops helpers; see quit_app)."""
    exe = _main_executable(app_path)
    if not exe:
        return False
    try:
        result = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("check_app_running: ps failed: %s", e)
        return False
    running = any(line.strip() == exe for line in result.stdout.splitlines())
    log.debug("check_app_running: %s → %s", app_path, "running" if running else "not running")
    return running


def _wait_for_exit(app_path, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not running_pids(app_path):
            return True
        time.sleep(0.25)
    return not running_pids(app_path)


def quit_app(app_path):
    """Stop everything running from inside the app bundle before it's trashed.

    A running app keeps going from memory after its bundle is deleted (and can
    recreate the support files just removed), so: ask it to quit normally, which
    lets it save; then SIGTERM whatever is left; then SIGKILL. Returns a dict:
    was_running, forced, ok, and error when something couldn't be stopped.
    """
    pids = running_pids(app_path)
    if not pids:
        return {"was_running": False, "forced": False, "ok": True}
    name = Path(app_path).stem
    log.info("quit_app: %s is running (pids %s), asking it to quit", name, pids)

    # Address the app by path, not bundle id: another copy with the same id may be
    # running and must not be quit. "ignoring application responses" keeps a
    # save-changes dialog from blocking us; we poll for exit instead.
    escaped = str(app_path).replace("\\", "\\\\").replace('"', '\\"')
    # "is running" checks without launching; a plain tell would start the app if
    # Launch Services doesn't know it's running (e.g. a helper started directly)
    script = (f'if application "{escaped}" is running then\n'
              f'ignoring application responses\ntell application "{escaped}" to quit\nend ignoring\n'
              f'end if')
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("quit_app: quit request failed for %s: %s", name, e)
    if _wait_for_exit(app_path, QUIT_GRACE_SECONDS):
        log.info("quit_app: %s quit normally", name)
        return {"was_running": True, "forced": False, "ok": True}

    for sig, wait in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
        remaining = running_pids(app_path)
        log.warning("quit_app: %s still running (pids %s), sending %s", name, remaining, sig.name)
        denied = []
        for pid in remaining:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                denied.append(pid)  # owned by root or another user
        if _wait_for_exit(app_path, wait):
            log.info("quit_app: %s stopped with %s", name, sig.name)
            return {"was_running": True, "forced": True, "ok": True}
        if denied and sig == signal.SIGKILL:
            break

    remaining = running_pids(app_path)
    log.error("quit_app: could not stop %s (pids %s still running)", name, remaining)
    return {"was_running": True, "forced": True, "ok": False,
            "error": f"{name} is still running and could not be quit "
                     f"(some of its processes need administrator rights to stop)"}


def find_login_items(app_path):
    """Names of Login Items (System Settings > General > Login Items) that open
    this app. Call it BEFORE trashing: once the bundle moves, System Events
    reports the item's path as missing and it can no longer be matched. Items the
    app registered itself through SMAppService aren't listed here; they stop
    working once the bundle is gone."""
    target = str(Path(app_path)).rstrip("/")
    script = ('tell application "System Events"\n'
              'set out to ""\n'
              'repeat with li in every login item\n'
              'set out to out & (name of li) & tab & (path of li) & linefeed\n'
              'end repeat\n'
              'return out\n'
              'end tell')
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("find_login_items: could not list login items: %s", e)
        return []
    if result.returncode != 0:
        # Most often macOS hasn't granted permission to control System Events
        log.warning("find_login_items: listing failed: %s", result.stderr.strip())
        return []
    names = []
    for line in result.stdout.splitlines():
        item_name, _, item_path = line.partition("\t")
        if item_path.rstrip("/") == target:
            names.append(item_name)
    log.info("find_login_items: %d login item(s) open %s: %s", len(names), target, names)
    return names


def delete_login_items(names):
    """Delete login items by name; returns the names actually removed."""
    removed = []
    for item_name in names:
        esc = item_name.replace("\\", "\\\\").replace('"', '\\"')
        try:
            r = subprocess.run(["osascript", "-e", f'tell application "System Events" to delete login item "{esc}"'],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("delete_login_items: could not remove %r: %s", item_name, e)
            continue
        if r.returncode == 0:
            log.info("delete_login_items: removed login item %r", item_name)
            removed.append(item_name)
        else:
            log.warning("delete_login_items: could not remove %r: %s", item_name, r.stderr.strip())
    return removed


def _trash_single(path):
    if not _verify_safe(path):
        log.warning("_trash_single: blocked — protected path: %s", path)
        return False, "Protected path"

    p = Path(path)
    # lexists, not exists: a dangling symlink (a common leftover) is still removable
    if not os.path.lexists(path):
        log.warning("_trash_single: path does not exist: %s", path)
        return False, "Path does not exist"

    trash_dir = Path.home() / ".Trash"
    dest = trash_dir / p.name
    if os.path.lexists(dest):
        # Keep a real extension (Foo 1.app, notes 1.txt), but don't split
        # dotted folder names (com.google.Chrome -> com.google.Chrome 1)
        if p.is_file() or p.suffix in (".app", ".plist", ".savedState", ".bundle"):
            base, suffix = p.stem, p.suffix
        else:
            base, suffix = p.name, ""
        i = 1
        while os.path.lexists(dest):
            dest = trash_dir / f"{base} {i}{suffix}"
            i += 1
    log.debug("_trash_single: moving %s → %s", path, dest)
    try:
        # A plain rename only. shutil.move falls back to copy-then-delete when the
        # rename is refused, which for a protected folder (another app's container
        # on newer macOS) copied part of it into the Trash, failed on a protected
        # file and left both the partial copy and the original behind.
        os.rename(str(p), str(dest))
        return True, ""
    except OSError as e:
        if isinstance(e, PermissionError) and os.path.lexists(os.path.join(path, CONTAINER_MARKER)):
            # Another app's container: macOS blocks Finder too (it fails with "you
            # don't have permission"), so don't pop that dialog on top of ours
            log.warning("_trash_single: %s is a protected app container: %s", path, e)
            return False, PROTECTED_CONTAINER_HELP
        # EPERM/EACCES: protected or root-owned. EXDEV: on another volume, whose
        # Trash is elsewhere. Finder handles these, asking for permission as needed.
        log.warning("_trash_single: rename refused for %s (%s), falling back to Finder", path, e)
        return _trash_via_osascript(path)


def _trash_via_osascript(path):
    log.debug("_trash_via_osascript: %s", path)
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Finder" to delete (POSIX file "{escaped}" as alias)'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            # A zero exit code is not proof of removal. When the privileged delete
            # is denied (Finder's own auth prompt, or a privilege-management agent
            # intercepting it) Finder still copies the item into the Trash and
            # osascript still exits 0, while the original stays where it was.
            # Confirm the source is actually gone before reporting success.
            if os.path.lexists(path):
                log.error("_trash_via_osascript: Finder reported success but %s still exists", path)
                return False, "Removal was cancelled or requires administrator privileges"
            log.info("_trash_via_osascript: succeeded for %s", path)
            return True, ""
        err = result.stderr.strip() or "Permission denied"
        log.error("_trash_via_osascript: failed for %s: %s", path, err)
        return False, err
    except subprocess.TimeoutExpired:
        log.error("_trash_via_osascript: timed out for %s", path)
        return False, "Operation timed out"
    except OSError as e:
        log.error("_trash_via_osascript: OSError for %s: %s", path, e)
        return False, str(e)


def is_safe_to_trash(path):
    return _verify_safe(path)


def _verify_safe(path):
    resolved = str(Path(path).resolve())
    for prefix in PROTECTED_PREFIXES:
        # The prefixes end in "/", so also block the directories themselves (/usr)
        if resolved.startswith(prefix) or resolved == prefix.rstrip("/"):
            return False
    if resolved == SELF_DIR or resolved.startswith(SELF_DIR + os.sep):
        return False
    # Any folder that contains Sweepr (e.g. ~/Downloads when it lives there)
    if SELF_DIR.startswith(resolved.rstrip(os.sep) + os.sep):
        return False
    if resolved in PROTECTED_EXACT:
        return False
    # A folder in /Applications that mirrors one in /System/Applications
    # (Utilities, and any similar folder a future macOS adds)
    rp = Path(resolved)
    if (str(rp.parent) == "/Applications" and not rp.name.endswith(".app")
            and (SYSTEM_APPS_DIR / rp.name).is_dir()):
        return False
    return True
