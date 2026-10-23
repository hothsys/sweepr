# Application scanning

How Sweepr discovers installed applications and populates the Applications tab. The logic lives in `lib/scanner.py`, orchestrated by `get_apps()` in `server.py`.

## Where it looks

Three directories are scanned:

- `/Applications`
- `/System/Applications`
- `~/Applications`

Within each, Sweepr recognises several kinds of entry:

- **`.app` bundles:** the normal case; metadata comes from `Contents/Info.plist`.
- **`.webloc` files:** web shortcuts (for example Chrome "app" bookmarks). These are shown with a globe icon and a "webloc" badge, and can be removed like anything else.
- **Vendor subfolders:** a folder that contains a `.app` directly inside it (common for Adobe). Symlinks are resolved so the real bundle is found. A bundle whose name contains an uninstall keyword is not listed as an app of its own; it is offered as the uninstaller instead (see below).
- **Leftover folders:** a folder under `/Applications` that has no `.app` anywhere inside it (searched up to `LEFTOVER_APP_SEARCH_DEPTH`, 4 levels, because suites like Adobe Creative Cloud nest their apps that deep) but still holds files. These are surfaced with a "leftover" badge so you can reclaim the space. `.localized` folders are ignored.
- **System folders are never leftovers.** `/Applications/Utilities` is an ordinary folder for third-party tools; Apple's utilities (Activity Monitor, Terminal, and so on) live in `/System/Applications/Utilities`, and Finder shows the two merged. If `/Applications/Utilities` holds no `.app` at the top level, it would otherwise look like a leftover and be labelled "Safe to remove". `_is_system_folder()` skips any folder on the system volume or with a same-named counterpart in `/System/Applications`. A folder that can't be walked is also not treated as a leftover.

## Metadata (batched Spotlight)

Reading metadata one app at a time is slow. Instead, Sweepr collects every discovered path and issues a single `mdls -raw` call for all of them (`batch_spotlight_metadata`, which calls `_mdls_batch`), using a null separator to split the results. One process replaces dozens. Paths that no longer exist are dropped first, because `mdls` stops at the first missing path and shifts every later value. If the number of values returned still doesn't match, the batch is split in half and each half retried, down to single apps, whose metadata is skipped if it still can't be read.

Three attributes are read:

| Attribute | Shown as |
| --- | --- |
| `kMDItemContentModificationDate` | "App version date" (when the developer built or last modified the app) |
| `kMDItemLastUsedDate` | "Last used" |
| `kMDItemAppStoreCategory` | Category label |

The "App version date" label is deliberate. macOS creation dates often reflect the developer's build date or reset on OS upgrades, so the value is presented as the developer's date and not as anything you did. See [Troubleshooting](troubleshooting.md) if a date looks wrong.

## Sizes (asynchronous)

Directory sizes are the slow part of a scan, so they are computed off the critical path:

1. The initial scan returns with `size` set to `0` for every app bundle (web shortcuts and leftover folders carry a quick size from the scan), so the list renders immediately.
2. `server.py` starts a background `ThreadPoolExecutor` (`_compute_sizes_background`, four workers) that walks each entry and records its size with `calculate_dir_size`. Sizes come from `disk_usage` in `lib/sizes.py`: on-disk usage like `du` (allocated blocks), with symlinks not followed and hard links counted once.
3. The frontend polls `/api/apps/sizes`, filling in each size cell as it lands, and stops when the server reports `complete`.

## Caching and warmup

- Results are cached in memory (`_apps_cache`). A normal `/api/apps` request serves the cache.
- `/api/apps/refresh` forces a rescan and restarts size computation.
- On boot the server warms the cache in a background thread, so the first page load is already fast.
- After a removal the apps cache is invalidated so the next scan reflects reality.

The log line `get_apps: cache hit (133 apps total, 71 system, 62 user)` reports the full scan count including system apps. The UI hides system apps by default, which is why the visible count is lower.

## System-app detection

An app is treated as a system app (and blocked from removal) when its resolved path starts with `/System/` or `/Library/Apple/`, or its bundle id starts with `com.apple.`.

Sweepr's own launcher (bundle id `com.sweepr.launcher`, created by `launchagent/install.sh`) is flagged `is_self` and also can't be removed from the UI; use `launchagent/uninstall.sh` instead.

## Custom uninstaller detection

Some apps ship their own uninstaller, which is safer than dragging the bundle to the Trash. `find_uninstaller()` looks, in order, for:

1. A nested `.app` in `Contents/Frameworks/` or `Contents/Helpers/` whose name contains an uninstall keyword.
2. An executable in `Contents/MacOS/` with an uninstall-related name.
3. A shell script in `Contents/Resources/`.
4. A sibling in the app's own vendor folder, or in the folder a symlinked app was found through (an `.app`, a `.bundle`'s executable, or an extensionless file such as a macOS alias). Adobe uninstallers are often alias files with no extension. A folder named `Applications` is not searched this way.
5. A separate `.app` next to the app (typically in `/Applications`) whose name contains an uninstall keyword and shares a meaningful word with the app's name.

Uninstall keywords are `uninstall`, `remover`, `remove`, `cleanup`, `clean up`, and `unsetup`. When an uninstaller is found it is surfaced in the detail view and can be launched directly. See [Removal and uninstallers](removal.md).

## Icons

`lib/icons.py` extracts a PNG from each app's `.icns`. It parses the icns container directly and prefers modern PNG-encoded entries (`ic07` through `ic14`); if that fails it falls back to `sips` to convert and downscale to 128px. Extracted icons (and failed lookups) are cached in memory keyed by app path, not bundle id, because apps that share a bundle id (Photoshop 2025 and 2026) have different icons. The cache is bounded to 512 entries (`_ICON_CACHE_MAX`), and responses carry a one-day `Cache-Control` header.
