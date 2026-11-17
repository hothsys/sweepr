import contextlib
import os
import subprocess
import threading
import time
from pathlib import Path

from lib.log import log
from lib.scanner import calculate_dir_size

DU_TIMEOUT = 600  # seconds; a du stuck on a hung network mount is killed after this

HOME = Path.home()

# (label, path, emoji) for the top-level roots shown in the Storage tab.
# "Home" is the whole user directory; the entries below it are quick shortcuts
# into its most common space consumers. "Libraries" is split into user/system.
_ROOT_DEFS = [
    ("Home", str(HOME), "\U0001f3e0"),
    ("Applications", "/Applications", "\U0001f4e6"),
    ("Documents", str(HOME / "Documents"), "\U0001f4c4"),
    ("Downloads", str(HOME / "Downloads"), "⬇️"),
    ("Desktop", str(HOME / "Desktop"), "\U0001f5a5️"),
    ("Pictures", str(HOME / "Pictures"), "\U0001f5bc️"),
    ("Movies", str(HOME / "Movies"), "\U0001f3ac"),
    ("Music", str(HOME / "Music"), "\U0001f3b5"),
    ("Developer", str(HOME / "Developer"), "\U0001f6e0️"),
    ("Library (User)", str(HOME / "Library"), "\U0001f5c4️"),
    ("Library (System)", "/Library", "\U0001f5c4️"),
]


def get_roots():
    """Return the configured roots that currently exist on disk."""
    roots = []
    for label, path, emoji in _ROOT_DEFS:
        if Path(path).is_dir():
            roots.append({"label": label, "path": str(Path(path)), "emoji": emoji})
    return roots


def _root_paths():
    # Recomputed per call (a handful of stats): a root created after startup, such
    # as ~/Developer, must be in scope as soon as get_roots() starts listing it
    paths = []
    for _, path, _emoji in _ROOT_DEFS:
        p = Path(path)
        if p.is_dir():
            with contextlib.suppress(OSError):
                paths.append(str(p.resolve()))
    return paths


def is_in_scope(path):
    """True when `path` is one of the roots or lives underneath one.

    Resolves symlinks first, so a symlink pointing outside the roots is rejected.
    """
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return False
    return any(resolved == root or resolved.startswith(root + os.sep) for root in _root_paths())


def list_children(path):
    """List the immediate children of `path`.

    Files are sized instantly via `stat`; directories are returned unsized (size
    is filled in later by `stream_dir_sizes`). Symlinks are treated as leaf
    entries so we never follow them into other trees. Returns None when the
    directory itself cannot be read.
    """
    files = []
    dirs = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                if is_dir:
                    dirs.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "is_dir": True,
                            "size": 0,
                            "accessible": True,
                        }
                    )
                else:
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                    files.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "is_dir": False,
                            "size": size,
                            "accessible": True,
                        }
                    )
    except PermissionError:
        log.warning("list_children: permission denied for %s (requires Full Disk Access)", path)
        return None
    except OSError as e:
        log.warning("list_children: failed for %s: %s", path, e)
        return None
    return {"files": files, "dirs": dirs}


def _kill_stuck(proc, target):
    if proc.poll() is None:
        log.warning("stream_dir_sizes: du for %s exceeded %ds, killing it", target, DU_TIMEOUT)
        with contextlib.suppress(OSError):
            proc.kill()


def stream_dir_sizes(path, on_child):
    """Size every immediate subdirectory of `path` with a single `du -d 1 -k`.

    `du` prints each subdirectory's recursive total as it finishes that
    subdirectory (post-order), so `on_child(child_path, size_bytes)` is called
    incrementally, which drives the live fill-in of the UI. Returns the total
    size of `path` in bytes, or None if `du` could not run.
    """
    t0 = time.time()
    target = str(Path(path))
    total = None
    try:
        proc = subprocess.Popen(
            ["du", "-d", "1", "-k", target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # du warns on unreadable dirs; ignore and continue
            text=True,
        )
    except OSError as e:
        log.error("stream_dir_sizes: du failed to start for %s: %s", target, e)
        # Fallback: at least compute the total (on-disk usage, like du).
        return calculate_dir_size(target)

    # Watchdog: reading stdout blocks, so a hung du can only be stopped from outside
    watchdog = threading.Timer(DU_TIMEOUT, _kill_stuck, args=(proc, target))
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in proc.stdout:
            parts = line.rstrip("\n").split("\t", 1)
            if len(parts) != 2:
                continue
            try:
                size = int(parts[0]) * 1024
            except ValueError:
                continue
            child = parts[1]
            if child == target:
                total = size
            else:
                on_child(child, size)
        proc.wait(timeout=2)
    except Exception as e:
        log.warning("stream_dir_sizes: error reading du output for %s: %s", target, e)
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()
        # reap it so no zombie is left behind
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)

    log.info(
        "stream_dir_sizes: %s -> %s bytes in %.2fs",
        target,
        total if total is not None else "?",
        time.time() - t0,
    )
    return total
