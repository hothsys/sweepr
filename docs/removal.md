# Removal and uninstallers

How Sweepr actually removes things. Everything is moved to the Trash rather than deleted outright, so mistakes are recoverable. The core lives in `lib/trash.py`.

## Move to Trash

`move_to_trash(paths)` handles a batch and reports which paths succeeded and which failed. For each path (`_trash_single`):

1. **Safety check.** The path must not be or sit under a protected prefix (`/System`, `/usr`, `/bin`, `/sbin`, `/Library/Apple`), must not be inside Sweepr's project folder, and must not be a folder that contains Sweepr (such as `~/Downloads` when Sweepr lives there). Certain folders are also refused by exact path, though items inside them can still be removed: `/`, `/Applications`, `/Applications/Utilities`, `/Library`, `/Users`, your home folder, `~/Applications`, `~/Library`, `/Applications/Sweepr.app`, `~/Library/LaunchAgents/com.sweepr.server.plist`, and any non-`.app` folder in `/Applications` that has a same-named counterpart in `/System/Applications`. Protected paths are refused.
2. **Existence check.** Missing paths are reported as failures rather than silently ignored. The check doesn't follow symlinks, so a broken symlink (a common leftover) can still be removed.
3. **Move.** The file is moved into `~/.Trash` with a plain rename, never a copy. (`shutil.move` used to fall back to copy-then-delete when the rename was refused; for another app's container, which newer macOS protects, that copied part of it into the Trash, failed on a protected file, and left both behind.) If the rename is refused, because the item is protected or on another volume, Finder is asked to do it instead (see Finder fallback below). The exception is another app's container (it has a `.com.apple.containermanagerd.metadata.plist` marker): newer macOS blocks Finder from removing those too, which only adds a "you don't have permission" dialog, so Sweepr skips Finder and returns `PROTECTED_CONTAINER_HELP` instead.

If a name already exists in the Trash, a numeric suffix is added so nothing is overwritten: before the extension for files and for `.app`, `.plist`, `.savedState` and `.bundle` items (`notes 1.txt`, `Foo 1.app`), at the end for other folders, including those with dots in their names (`com.google.Chrome 1`, not `com.google 1.Chrome`).

### Protected app containers

On newer macOS, a sandboxed app's container (`~/Library/Containers/<id>`, and group containers) may be readable only by that app. `is_protected_container()` in `lib/sizes.py` detects this by trying to read the container's marker file. The orphan and associated-file scans still list protected containers, so you know they exist, but mark them `protected`: they show a "protected by macOS" badge with a disabled checkbox, are excluded from the selected total, and are never in the server's offered set. To remove one, give your terminal app Full Disk Access, delete the folder from it, then turn the access off. Leaving it is harmless. macOS applies privacy permissions to the app that launched a process, so a Sweepr started with `python3 server.py` from a terminal has that terminal's permissions, while the LaunchAgent copy has its own. Whether the protection applies therefore depends on how Sweepr was started as well as the macOS version and privacy settings: on one Mac Sweepr could read containers normally, while on another both Sweepr and Finder were refused.

### Why not delete directly?

Renaming into `~/.Trash` is fast and, crucially, does not trigger a macOS authentication prompt for files you own. An earlier approach that asked Finder to do the delete popped a credentials dialog every time. Finder is now only a fallback.

### Finder fallback

If the rename fails (permission, protection, another volume), Sweepr falls back to asking Finder to trash the file via `osascript` (`_trash_via_osascript`, 30 s timeout). The only exception is the protected app container described above. This handles the occasional file that needs Finder's privileges, at the cost of being slower.

A zero exit code from `osascript` isn't taken as proof. When Finder's privileged delete is denied (its own authentication prompt is cancelled, or a privilege-management agent intercepts it), Finder can still copy the item into the Trash and report success while the original stays put. So Sweepr checks that the source path is actually gone, and if it isn't, reports the path as failed ("Removal was cancelled or requires administrator privileges").

## Running apps

A running app keeps going from memory after its bundle is deleted, even after the Trash is emptied, and it can recreate the support files that were just removed. (Cheat Sheet kept answering a long press of Command with a blank overlay this way.) So before trashing an app, `_handle_trash` stops it with `quit_app()` in `lib/trash.py`:

1. **Find its processes.** `running_pids()` lists every process whose executable lives inside the bundle (from `ps -axo pid=,comm=`), so helpers, XPC services and embedded login items are included, and two apps that share an executable name can't be confused. App extensions (`.appex`: widgets, Share and Finder extensions) are left out: macOS runs and relaunches them on its own whether or not the app is open (Office's widgets run permanently), so they can't be stopped for good and disappear once the bundle is gone.

   The "Running" badge (`check_app_running`) is narrower still: it only counts the app's own main program (`Contents/MacOS/<CFBundleExecutable>`), meaning the app is actually open. The frontend fetches it when an app is selected, again on Refresh, and every 5 seconds while that app's detail is showing (`watchRunning` in `static/js/apps.js`, paused while the page is hidden or another tab is open). Only the badge is updated, so checkbox choices aren't reset.
2. **Ask it to quit.** An AppleEvent quit addressed to the bundle's path (not its bundle id, so another copy with the same id is left alone), guarded by `is running` so it can never launch the app. This lets the app save; Sweepr waits up to `QUIT_GRACE_SECONDS` (8 s). The confirmation dialog tells you to save open work first.
3. **Force it.** Anything still running gets `SIGTERM`, then `SIGKILL`.
4. **Refuse if it can't be stopped.** If processes remain (typically root-owned helpers), nothing is removed and the request fails with 409 and an explanation, rather than leaving a deleted app running.

The response includes `quit` (`was_running`, `forced`), and the success message says whether the app was quit or force-quit.

## Login items

Before the move, `find_login_items()` asks System Events for login items (System Settings > General > Login Items) that open the app. It has to happen first: once the bundle moves, System Events reports the item's path as missing. If the app is trashed, `delete_login_items()` removes them, and the response lists them in `login_items_removed`. Background items an app registered itself through `SMAppService` aren't in that list; they stop working once the bundle is gone. macOS may ask once for permission to let Sweepr (Python) control System Events; if that's denied, removal still works and the login item is left in place (logged as a warning).

## What gets removed together

The trash request for an app (`POST /api/apps/{id}/trash`) carries the app bundle plus the associated files you left checked. System apps and Sweepr's own launcher are refused with a 403 before anything else. The server only accepts files from the app's last `/files` result (or, if it has none cached, from a fresh scan), never protected containers, and sizes them itself to total the space reclaimed (it used to trust sizes sent by the client). After a successful removal the apps cache is invalidated.

The app is only dropped from the list if its own path is in the success set, so a partial failure never makes an app silently disappear.

## Custom uninstallers

Dragging a bundle to the Trash is the blunt option. Many apps (Adobe especially) ship a real uninstaller that also unwinds system integration, and for leftover folders the uninstaller may be the only clean path. When [scanning](scanning.md) finds one, the detail view offers to run it instead.

`POST /api/apps/{id}/launch-uninstaller` launches it based on its detected type:

| Type | How it launches |
| --- | --- |
| `app` | `open` the `.app` bundle |
| `alias` | `open` the macOS alias (resolves to the real target) |
| `executable` | run the binary directly |
| `script` | open in Terminal |

Leftover folders are checked too: an extensionless file in the folder whose name marks it as an uninstaller is offered as an `alias` uninstaller, alongside the plain Trash move. (The risk banner still rates a leftover folder as safe; see [Risk assessment](risk-assessment.md).)

## Orphans and agents

Orphan cleanup (`POST /api/orphans/trash`) and LaunchAgent removal (`POST /api/agents/trash`) use the same `move_to_trash` path, so the same safety guards and Finder fallback apply everywhere. They are separate handlers (`_handle_orphan_trash` and `_handle_agent_trash`). Agent removal also checks the plist is safe to trash before touching it, and after a successful move unloads the job (see [LaunchAgents](launch-agents.md)).

## Request checks

Before any of the above, the server checks the request itself:

- **Host and Origin.** Requests must name this server in `Host` (`localhost:47836`, `127.0.0.1:47836` or `[::1]:47836`), and a POST that carries an `Origin` header must come from one of those. Binding to `localhost` keeps other machines out but not web pages open in your own browser, which can send requests to `localhost`; this check stops them.
- **JSON only.** POSTs must use `Content-Type: application/json` (otherwise 415), with a JSON object body of at most 1 MB (`MAX_BODY`; larger gets 413). A browser has to ask permission (a CORS preflight) before sending that cross-origin, and Sweepr never grants it.
- **Only what was offered.** A trash request may only name paths the matching scan showed you: associated files from `/api/apps/{id}/files`, orphans from `/api/orphans`, agents from `/api/agents`. Items marked protected are never in these sets. Anything else is refused with a 403, so the endpoints can't be used to remove arbitrary files. Malformed bodies (a non-list `file_paths`, an empty or non-string `paths`) get a 400.

## Removing Sweepr itself

Sweepr lists its own launcher (`/Applications/Sweepr.app`) and LaunchAgent (`com.sweepr.server`) but won't remove either; both show "Use uninstall.sh". Trashing only the launcher would leave the server running and restarting at every login. Run `bash launchagent/uninstall.sh` instead: it unloads the agent, stops the server and removes the app. The project folder is left for you to delete.
