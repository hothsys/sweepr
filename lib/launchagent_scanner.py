import os
import plistlib
import re
import shlex
import subprocess
from pathlib import Path

from lib.log import log

HOME = Path.home()

SELF_LABEL = "com.sweepr.server"

# launchctl exit codes meaning "that service isn't loaded"
_NOT_LOADED_CODES = (3, 113)

# Programs that run a script named in their arguments; for these the script is
# what has to exist, not the interpreter (which always does)
INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "perl", "ruby", "node",
                "osascript", "python", "python3", "python2"}
_PYTHON_RE = re.compile(r"^python\d+(\.\d+)*$")

SCAN_DIRS = [
    ("User", HOME / "Library" / "LaunchAgents"),
    ("System", Path("/Library/LaunchAgents")),
    ("System Daemons", Path("/Library/LaunchDaemons")),
]


def scan_launch_agents():
    log.info("scan_launch_agents: starting")
    status = _launchd_status()
    agents = []
    for scope, scan_dir in SCAN_DIRS:
        if not scan_dir.is_dir():
            continue
        try:
            entries = sorted(scan_dir.iterdir())
        except PermissionError:
            log.warning("scan_launch_agents: permission denied for %s", scan_dir)
            continue

        for entry in entries:
            if not entry.name.endswith(".plist"):
                continue
            agent = _parse_agent(entry, scope)
            if agent:
                _apply_status(agent, status)
                agents.append(agent)

    stale = sum(1 for a in agents if a["stale"])
    failing = sum(1 for a in agents if a.get("failing"))
    log.info("scan_launch_agents: found %d agents (%d stale, %d failing)", len(agents), stale, failing)
    return agents


def _domain_for(scope):
    return "system" if scope == "System Daemons" else f"gui/{os.getuid()}"


def _launchd_status():
    """{(domain, label): (pid, last_exit)} for every loaded job, from one
    `launchctl print` per domain. pid 0 = not running; last_exit None = never exited."""
    status = {}
    for domain in ("system", f"gui/{os.getuid()}"):
        try:
            out = subprocess.run(["launchctl", "print", domain], capture_output=True,
                                 text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("_launchd_status: launchctl print %s failed: %s", domain, e)
            continue
        in_services = False
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("services = {"):
                in_services = True
                continue
            if in_services:
                if stripped == "}":
                    break
                parts = stripped.split(None, 2)
                if len(parts) == 3:
                    pid, code, label = parts
                    try:
                        status[(domain, label)] = (int(pid), None if code == "-" else int(code))
                    except ValueError:
                        pass
    return status


def _apply_status(agent, status):
    entry = status.get((_domain_for(agent["scope"]), agent["label"]))
    agent["loaded"] = entry is not None
    agent["pid"] = entry[0] if entry and entry[0] > 0 else None
    agent["last_exit"] = entry[1] if entry else None
    # Loaded, not running, and its last run ended in an error: launchd keeps
    # retrying it (or it fails every time it's triggered). Signals (negative
    # codes) are how jobs are normally stopped, so they don't count.
    agent["failing"] = bool(entry and agent["pid"] is None and entry[1] is not None and entry[1] > 0)


def _load_plist(plist_path):
    """Parse with plistlib, falling back to plutil, which accepts some files
    plistlib rejects. Returns (dict or None, error message)."""
    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
        return (data, "") if isinstance(data, dict) else (None, "not a dictionary")
    except PermissionError as e:
        return None, f"permission denied: {e}"
    except Exception as e:
        first_error = str(e)
    try:
        result = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", str(plist_path)],
                                capture_output=True, timeout=5)
        if result.returncode == 0:
            data = plistlib.loads(result.stdout)
            if isinstance(data, dict):
                return data, ""
    except (OSError, subprocess.TimeoutExpired, Exception) as e:
        log.debug("_load_plist: plutil fallback failed for %s: %s", plist_path, e)
    return None, first_error


def _executable(plist, plist_path):
    """The program launchd actually runs. Program wins over ProgramArguments[0]
    (which is then only argv[0]); BundleProgram is relative to the app bundle the
    plist was installed from, so it can't be checked from here."""
    program = plist.get("Program")
    if isinstance(program, str) and program:
        return program
    args = plist.get("ProgramArguments")
    if isinstance(args, list) and args and isinstance(args[0], str):
        return args[0]
    return ""


def _script_target(exe, args):
    """For interpreter-run jobs (bash script.sh, bash -c "...", python run.py),
    the absolute path of the script, or None."""
    name = os.path.basename(exe)
    if name not in INTERPRETERS and not _PYTHON_RE.match(name):
        return None
    rest = [a for a in args[1:] if isinstance(a, str)]
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("-c", "-e") and i + 1 < len(rest):
            # Inline command: the first word is usually the script it runs
            try:
                words = shlex.split(rest[i + 1])
            except ValueError:
                return None
            return words[0] if words and words[0].startswith("/") else None
        if arg.startswith("-"):
            i += 1
            continue
        return arg if arg.startswith("/") else None
    return None


def _exe_exists(exe):
    """True/False when we can tell, None when we can't. Relative names (sh, node)
    resolve via launchd's PATH, and permission errors don't mean the file is gone,
    so both are unknown rather than missing."""
    if not exe:
        return None
    exe = os.path.expanduser(exe)
    if not os.path.isabs(exe):
        return None
    try:
        os.stat(exe)
        return True
    except FileNotFoundError:
        # A missing file inside an unreadable folder can also raise this; only call
        # it missing when the parent folder is readable
        parent = os.path.dirname(exe)
        return False if os.access(parent, os.R_OK | os.X_OK) else None
    except (PermissionError, NotADirectoryError):
        return None
    except OSError:
        return None


def _parse_agent(plist_path, scope):
    plist, err = _load_plist(plist_path)
    if plist is None:
        # Still list it, so an unreadable agent isn't silently invisible
        log.warning("_parse_agent: could not parse %s: %s", plist_path, err)
        return {
            "label": plist_path.stem,
            "path": str(plist_path),
            "scope": scope,
            "executable": "",
            "exe_exists": None,
            "stale": False,
            "run_at_load": False,
            "keep_alive": False,
            "is_self": plist_path.stem == SELF_LABEL,
            "parse_error": err,
        }

    label = plist.get("Label", plist_path.stem)
    exe = _executable(plist, plist_path)
    args = plist.get("ProgramArguments") if isinstance(plist.get("ProgramArguments"), list) else []
    script = _script_target(exe, args) if exe else None
    exe_exists = _exe_exists(exe)
    if exe_exists and script:
        exe_exists = _exe_exists(script)  # the interpreter always exists; the script may not
    stale = exe_exists is False
    stale_reason = "its program is missing" if stale else None
    if not exe and not plist.get("BundleProgram"):
        # Nothing to run (Google blanks old Keystone plists this way): launchd
        # can't do anything with it, so it's as dead as a missing program
        stale, stale_reason = True, "it declares nothing to run"

    run_at_load = plist.get("RunAtLoad", False)
    keep_alive = plist.get("KeepAlive", False)

    return {
        "label": label,
        "path": str(plist_path),
        "scope": scope,
        "executable": exe,
        "exe_exists": exe_exists,
        "stale": stale,
        "run_at_load": run_at_load,
        "keep_alive": keep_alive,
        "is_self": label == SELF_LABEL,
        "script": script,
        "stale_reason": stale_reason,
    }


def unload_agent(label, scope):
    """Stop a launchd job before its plist is trashed, so removal takes effect now
    rather than at the next logout or restart. Returns (unloaded_or_not_loaded, error)."""
    domain = "system" if scope == "System Daemons" else f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    log.info("unload_agent: launchctl bootout %s", target)
    try:
        result = subprocess.run(["launchctl", "bootout", target],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("unload_agent: failed for %s: %s", target, e)
        return False, str(e)
    if result.returncode == 0:
        log.info("unload_agent: unloaded %s", target)
        return True, ""
    if result.returncode in _NOT_LOADED_CODES:
        log.info("unload_agent: %s was not loaded", target)
        return True, ""
    err = result.stderr.strip() or f"launchctl exited {result.returncode}"
    log.warning("unload_agent: could not unload %s: %s", target, err)
    return False, err
