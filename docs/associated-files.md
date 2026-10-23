# Associated files

When you select an app, Sweepr looks for the support files it scattered across `~/Library` so you can remove them together. The logic lives in `lib/associated_files.py` and runs on demand via `GET /api/apps/{id}/files`.

## Where it looks

The following locations are searched, and each match is tagged with its category:

| Category | Location |
| --- | --- |
| Preferences | `~/Library/Preferences` |
| Preferences (ByHost) | `~/Library/Preferences/ByHost` |
| Caches | `~/Library/Caches` |
| Application Support | `~/Library/Application Support` |
| Containers | `~/Library/Containers` |
| Group Containers | `~/Library/Group Containers` |
| Logs | `~/Library/Logs` |
| Crash Reports | `~/Library/Logs/DiagnosticReports` |
| Saved Application State | `~/Library/Saved Application State` |
| HTTP Storages | `~/Library/HTTPStorages` |
| WebKit Data | `~/Library/WebKit` |
| Cookies | `~/Library/Cookies` |
| Application Scripts | `~/Library/Application Scripts` |
| Launch Agents | `~/Library/LaunchAgents` |
| System Launch Agents | `/Library/LaunchAgents` |
| System Launch Daemons | `/Library/LaunchDaemons` |
| Privileged Helpers | `/Library/PrivilegedHelperTools` |

## How matches are decided

Sweepr builds a set of match patterns from the app's identity:

- The full bundle id (for example `com.google.Chrome`).
- The last segment of the bundle id (`Chrome`), but only if the id has at least three parts and the segment is distinctive: at least five characters and not a generic word (`GENERIC_TAILS`: `mac`, `app`, `media`, `launcher`, `daemon`, ...). Otherwise `com.postman.mac` would claim everything starting with `mac-`.
- The display name (three characters or more), and the display name with spaces removed.

An entry matches when its name equals a pattern, starts with `pattern.`, starts with `pattern-`, or its stem (name without extension) equals a pattern. This catches files like `com.google.Chrome.plist`, `com.google.Chrome.savedState`, and `Google Chrome` folders while avoiding unrelated names.

`find_associated_files` also receives the other installed apps (`other_apps`) and uses them to avoid claiming what isn't this app's:

- **More specific ids win.** If another installed app's bundle id extends this one (`com.cleverfiles.DiskDrill.Media` versus `com.cleverfiles.DiskDrill`), entries matching it belong to that app and are skipped.
- **Shared ids are flagged.** If another installed app has the *same* bundle id (Photoshop 2025 and 2026), every match is marked `shared_with: [names]`. The detail view shows a "shared" badge and leaves those files unchecked, since the other copy still uses them.

## Spotlight pass

If the app has a bundle id, Sweepr also runs `mdfind "kMDItemCFBundleIdentifier == '...'"` to catch anything the name-based search missed. That query returns every bundle with the id, including other installed copies or versions of the app and apps nested inside other products, so results that are an `.app` or inside one are always dropped, as is anything inside another installed app. Results under `/System/` and `/Library/Managed Preferences/` are excluded too. What remains is tagged with the "Spotlight" category.

## Sizes and de-duplication

Every match is sized with `lib/sizes.py` (on-disk usage like `du`; symlinks aren't followed and hard links count once) and paths are resolved so the same file is never listed twice. App containers that macOS won't let Sweepr read (`is_protected_container`) are listed but flagged `protected`: the detail view shows a "protected by macOS" badge with the checkbox disabled, and they are left out of the trashable list (see [Protected app containers](removal.md#protected-app-containers)). The trash endpoint only accepts files from this list, and computes the space freed itself. The detail view lets you uncheck individual files before removing; unchecked files are left alone.

## Full Disk Access

Some of these locations are protected by macOS privacy controls (TCC), for example `Cookies` and parts of `Containers`. Without Full Disk Access, listing them raises a permission error. Sweepr logs this at debug level and simply skips that location; the rest of the scan continues. This is expected and harmless. See [Troubleshooting](troubleshooting.md) for details and options.
