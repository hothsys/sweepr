# Storage analyzer

The Storage analyzer shows where disk space actually goes. Because it is read-only (analysis plus "Show in Finder", never deletion), it is deliberately not one of the cleaner tabs: it opens as its own full-screen mode from the **Analyze Storage** button in the header, and closes with **Back to Sweepr**. It starts from a set of top-level locations, computes real recursive sizes, and lets you drill into subfolders to find what is eating space. The backend lives in `lib/storage_scanner.py`, wired up in `server.py`; the frontend is `static/js/storage.js`.

## Roots

The tab opens on a curated set of locations (only those that exist are shown):

your home folder (`~`), `/Applications`, `~/Documents`, `~/Downloads`, `~/Desktop`, `~/Pictures`, `~/Movies`, `~/Music`, `~/Developer`, `~/Library` (User), and `/Library` (System).

Home is the whole user directory; the entries below it are quick shortcuts into its most common space consumers. Every drill target is constrained to live under one of these roots (`is_in_scope`), so the analyzer never wanders into `/System` or other sensitive areas even though it only reveals, never deletes.

On the roots screen, small locations resolve almost instantly. Large ones (Home, `~/Library`) genuinely take longer to walk the first time because of their file count, so they show a live-climbing partial total while scanning rather than a blank spinner, then settle on the exact figure. Results are cached, so later visits are instant.

## How it stays fast

Sizing is lazy, streamed, and cached:

1. **One `du -d 1 -k` per opened directory.** `du` sizes every immediate subdirectory in a single optimized pass and prints each child's total as it finishes that child. Immediate files are listed instantly with `os.scandir` (no recursion needed for a file's own size).
2. **Streamed results.** The `du` output is read line by line on a background thread, so child sizes fill in live. That live fill is the "churn" you see in the UI, and it is real progress, not a fake percentage.
3. **Cached per directory**, keyed by path, so re-visiting a folder is instant and busy folders like `~/Library` are not rescanned on every click. An entry is fresh if it finished within `_STORAGE_TTL` (60 s), or if the directory's mtime is unchanged and it is younger than `_STORAGE_MAX_AGE` (10 min). The cap matters because a directory's mtime only changes when its direct children change, so deeper changes would otherwise never trigger a rescan. Trashing anything also marks every cached directory above it as stale. While a stale entry rescans, the previous sizes stay visible instead of dropping to zero. The cache is bounded (`_STORAGE_CACHE_MAX`, FIFO), but the top-level roots are never evicted, and `/api/storage/roots` restarts a scan for any root missing from the cache (for example one created after startup). `is_in_scope()` re-reads the root list on every call for the same reason.
4. **A hung `du` is killed.** A watchdog kills `du` after `DU_TIMEOUT` (600 s), for example when it is stuck on an unresponsive network mount, and the process is always reaped so no zombie is left.
5. **Roots warmed on boot.** A background thread sizes the roots when the server starts, so totals and the first drill level are usually ready before you open the tab.

Folder sizes use `du` (disk-allocated blocks), which reflects actual storage occupied. The Homebrew and npm scanners also use `du`, and the Applications, associated-files and Orphans views measure the same way (`disk_usage` in `lib/sizes.py` counts allocated blocks), so figures agree across tabs. Files listed directly in a folder are the exception: they show their apparent size (`st_size` from the `os.scandir` listing), which can differ from disk usage for sparse or compressed files. If `du` cannot be started at all, the folder's total falls back to `disk_usage`.

## Navigating

The analyzer is a single full-width list, largest first.

- **List:** every item in the current folder, biggest at the top. Click a folder to drill in; click a file to select it. A "Back" row at the top returns to the parent folder, or to the locations list when you are at a root.
- **Breadcrumb:** the trail above the list. It starts with a clickable "All locations" that returns to the locations list; click any segment to jump back up.
- **Search and sort:** the analyzer's own search box filters the current folder; sort switches between size (the default) and name.
- **Show in Finder:** each row has a "Show in Finder" button (revealed on hover) that opens that item in Finder. Nothing is ever deleted from here.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/storage/roots` | The roots, with cached totals when warm |
| POST | `/api/storage/list` | Immediate children of a directory (files sized inline) |
| POST | `/api/storage/progress` | Growing map of subdirectory sizes for a directory |
| POST | `/api/storage/reveal` | Reveal a path in Finder |

All path parameters are sent in the JSON body and validated against the allowed roots; an out-of-scope path returns 403.

## Permissions

Some `~/Library` and `/Library` subpaths are protected by macOS privacy controls (TCC). Without Full Disk Access, `du` and `os.scandir` skip them (logged, not fatal), so totals under those areas may be slightly under-reported. This is expected and consistent with the other tabs. See [Troubleshooting](troubleshooting.md).
