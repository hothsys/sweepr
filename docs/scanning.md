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
- **Vendor subfolders:** a folder that contains a `.app` inside it (common for Adobe). Symlinks and macOS alias entries are resolved so the real bundle is found.
- **Leftover folders:** a folder under `/Applications` that has no `.app` anywhere inside it (searched up to `LEFTOVER_APP_SEARCH_DEPTH`, 4 levels, because suites like Adobe Creative Cloud nest their apps that deep) but still holds files. These are surfaced with a "leftover" badge so you can reclaim the space. `.localized` folders are ignored.
- **System folders are never leftovers.** `/Applications/Utilities` is an ordinary folder for third-party tools; Apple's utilities (Activity Monitor, Terminal, and so on) live in `/System/Applications/Utilities`, and Finder shows the two merged. If `/Applications/Utilities` holds no `.app` at the top level, it would otherwise look like a leftover and be labelled "Safe to remove". `_is_system_folder()` skips any folder on the system volume or with a same-named counterpart in `/System/Applications`. A folder that can't be walked is also not treated as a leftover.

## Metadata (batched Spotlight)

Reading metadata one app at a time is slow. Instead, Sweepr collects every discovered path and issues a single `mdls` call for all of them (`batch_spotlight_metadata`), using a null separator to split the results. One process replaces dozens.

Three attributes are read:

| Attribute | Shown as |
| --- | --- |
| `kMDItemContentModificationDate` | "App version date" (when the developer built or last modified the app) |
| `kMDItemLastUsedDate` | "Last used" |
| `kMDItemAppStoreCategory` | Category label |

The "App version date" label is deliberate. macOS creation dates often reflect the developer's build date or reset on OS upgrades, so the value is presented as the developer's date and not as anything you did. See [Troubleshooting](troubleshooting.md) if a date looks wrong.

## Sizes (asynchronous)

Directory sizes are the slow part of a scan, so they are computed off the critical path:

1. The initial scan returns with `size` set to `0` for every app, so the list renders immediately.
2. `server.py` starts a background `ThreadPoolExecutor` (`_compute_sizes_background`) that walks each bundle and records its size.
3. The frontend polls `/api/apps/sizes`, filling in each size cell as it lands, and stops when the server reports `complete`.

## Caching and warmup

- Results are cached in memory (`_apps_cache`). A normal `/api/apps` request serves the cache.
- `/api/apps/refresh` forces a rescan and restarts size computation.
- On boot the server warms the cache in a background thread, so the first page load is already fast.
- After a removal the apps cache is invalidated so the next scan reflects reality.

The log line `get_apps: cache hit (133 apps total, 71 system, 62 user)` reports the full scan count including system apps. The UI hides system apps by default, which is why the visible count is lower.

## System-app detection

An app is treated as a system app (and blocked from removal) when its resolved path starts with `/System/` or `/Library/Apple/`, or its bundle id starts with `com.apple.`.

## Custom uninstaller detection

Some apps ship their own uninstaller, which is safer than dragging the bundle to the Trash. `find_uninstaller()` looks, in order, for:

1. A nested `.app` in `Contents/Frameworks/` or `Contents/Helpers/` whose name contains an uninstall keyword.
2. An executable in `Contents/MacOS/` with an uninstall-related name.
3. A shell script in `Contents/Resources/`.
4. A sibling in the same folder (an `.app`, a `.bundle`, or a macOS alias file). Adobe uninstallers are often alias files with no extension.
5. A separate `.app` in `/Applications` whose name matches and contains an uninstall keyword.

Uninstall keywords are `uninstall`, `remover`, `remove`, `cleanup`, `clean up`, and `unsetup`. When an uninstaller is found it is surfaced in the detail view and can be launched directly. See [Removal and uninstallers](removal.md).

## Icons

`lib/icons.py` extracts a PNG from each app's `.icns`. It parses the icns container directly and prefers modern PNG-encoded entries (`ic07` through `ic14`); if that fails it falls back to `sips` to convert and downscale to 128px. Extracted icons are cached in memory keyed by bundle id, so repeated requests are free.
