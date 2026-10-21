import os
import stat

from lib.log import log


def disk_usage(path):
    """Return (bytes, complete) for a file or directory.

    Bytes are allocated blocks, like `du`, so sparse files (Docker.raw) count what
    they actually occupy. Symlinks are not followed or counted (os.path.getsize
    followed them, double-counting framework links inside app bundles), and
    hard-linked files are counted once. `complete` is False if any part could not
    be read, so callers can avoid presenting a partial size as the whole story.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return 0, False
    if stat.S_ISLNK(st.st_mode):
        return 0, True
    if not stat.S_ISDIR(st.st_mode):
        return st.st_blocks * 512, True

    total = 0
    complete = True
    seen_inodes = set()

    def onerror(err):
        nonlocal complete
        complete = False
        log.debug("disk_usage: cannot read %s: %s", getattr(err, "filename", path), err)

    for dirpath, _, filenames in os.walk(path, onerror=onerror):
        for name in filenames:
            try:
                fst = os.lstat(os.path.join(dirpath, name))
            except OSError:
                complete = False
                continue
            if stat.S_ISLNK(fst.st_mode):
                continue
            if fst.st_nlink > 1:
                key = (fst.st_dev, fst.st_ino)
                if key in seen_inodes:
                    continue
                seen_inodes.add(key)
            total += fst.st_blocks * 512
    return total, complete
