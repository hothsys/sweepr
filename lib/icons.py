import os
import struct
import subprocess
import tempfile
import threading
from pathlib import Path

from lib.log import log

# Keyed by app path: apps that share a bundle id (Photoshop 2025/2026) have
# different icons. Bounded because this is a long-running process.
_icon_cache = {}
_icon_lock = threading.Lock()
_ICON_CACHE_MAX = 512

PNG_TYPES = [b"ic07", b"ic08", b"ic13", b"ic14", b"ic11", b"ic12", b"ic10", b"ic09"]
PNG_MAGIC = b"\x89PNG"
ICNS_MAGIC = b"icns"


def get_icon_png(app_path, bundle_id):
    cache_key = app_path
    with _icon_lock:
        if cache_key in _icon_cache:
            log.debug("get_icon_png: cache hit for %s", cache_key)
            return _icon_cache[cache_key]
    png_data = _load_icon(app_path)
    with _icon_lock:
        if len(_icon_cache) >= _ICON_CACHE_MAX:
            _icon_cache.pop(next(iter(_icon_cache)))
        _icon_cache[cache_key] = png_data
    return png_data


def _load_icon(app_path):
    icns_path = _find_icns_path(app_path)
    if not icns_path:
        log.debug("get_icon_png: no icns found for %s", app_path)
        return None

    png_data = _extract_from_icns(icns_path)
    if not png_data:
        log.debug("get_icon_png: direct extract failed, trying sips for %s", icns_path)
        png_data = _convert_with_sips(icns_path)

    if png_data:
        log.debug("get_icon_png: extracted icon (%d bytes) for %s", len(png_data), app_path)
    else:
        log.warning("get_icon_png: failed to extract icon for %s", app_path)

    return png_data


def _find_icns_path(app_path):
    import plistlib

    plist_path = Path(app_path) / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return None
    try:
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
    except Exception as e:
        log.warning("_find_icns_path: failed to read plist for %s: %s", app_path, e)
        return None

    icon_file = plist.get("CFBundleIconFile", "")
    if not icon_file:
        return None
    if not icon_file.endswith(".icns"):
        icon_file += ".icns"

    resources = Path(app_path) / "Contents" / "Resources"
    icns_path = resources / icon_file
    if icns_path.is_file():
        return str(icns_path)

    if resources.is_dir():
        for f in resources.iterdir():
            if f.suffix == ".icns" and f.is_file():
                return str(f)
    return None


def _extract_from_icns(icns_path):
    try:
        with open(icns_path, "rb") as f:
            data = f.read()
    except OSError as e:
        log.warning("_extract_from_icns: failed to read %s: %s", icns_path, e)
        return None

    if len(data) < 8 or data[:4] != ICNS_MAGIC:
        return None

    file_size = struct.unpack(">I", data[4:8])[0]
    entries = {}
    offset = 8
    while offset < min(file_size, len(data)):
        if offset + 8 > len(data):
            break
        entry_type = data[offset : offset + 4]
        entry_size = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        if entry_size < 8:
            break
        entry_data = data[offset + 8 : offset + entry_size]
        if entry_data[:4] == PNG_MAGIC:
            entries[entry_type] = entry_data
        offset += entry_size

    for ptype in PNG_TYPES:
        if ptype in entries:
            return entries[ptype]
    if entries:
        return next(iter(entries.values()))
    return None


def _convert_with_sips(icns_path):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        result = subprocess.run(
            ["sips", "-s", "format", "png", icns_path, "--out", tmp_path, "--resampleWidth", "128"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            log.warning("_convert_with_sips: sips returned %d for %s", result.returncode, icns_path)
        with open(tmp_path, "rb") as f:
            png_data = f.read()
        if png_data and png_data[:4] == PNG_MAGIC:
            return png_data
    except subprocess.TimeoutExpired:
        log.warning("_convert_with_sips: timed out for %s", icns_path)
    except OSError as e:
        log.warning("_convert_with_sips: failed for %s: %s", icns_path, e)
    finally:
        # Removed on every path, including timeouts (it used to leak then)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return None
