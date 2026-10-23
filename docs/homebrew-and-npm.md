# Homebrew and npm

Sweepr lists and removes Homebrew packages (`lib/brew_scanner.py`) and globally installed npm packages (`lib/npm_scanner.py`) directly from the UI.

## Homebrew: two-phase scan

A full `brew info --json=v2 --installed` call can take several seconds, which would make the tab feel slow. Sweepr splits the work:

1. **Quick pass (`scan_homebrew_quick`):** lists the `Cellar` and `Caskroom` folders directly instead of running `brew info`, so it is instant and works even when `brew info` is failing. The only `brew` call is a one-time `brew --cellar` (5 s timeout) to locate the Cellar, cached for the life of the process and falling back to `/opt/homebrew/Cellar` or `/usr/local/Cellar`. Version, install date, "installed on request" and dependencies (the receipt's `runtime_dependencies`) come from each keg's `INSTALL_RECEIPT.json` (`_local_formula_info`). Cask versions come from the newest folder under `Caskroom/<name>`. When several versions are installed, the active one is the keg `opt/<name>` links to, otherwise the newest by natural version order (`10.0` after `9.0`). Descriptions, sizes and outdated status wait for the detailed pass.
2. **Detailed pass (`scan_homebrew`):** a background thread runs the full `brew info` call (30 s timeout), parses each formula and cask, and computes formula sizes. Its results are merged into the quick list (`_merge_brew_lists`) rather than replacing it, so packages `brew info` can't load (untrusted tap, unsupported OS condition) stay listed with `details_missing`. Renamed formulae keep their old folder name in the Cellar (`sdl2-compat` lives in `Cellar/sdl2`); `keg_name` records it, from `oldnames`/`aliases`. Each pass carries a generation number, and a pass superseded by an uninstall or refresh discards its results instead of overwriting newer data.

The frontend polls `GET /api/brew/status` every 1.5 s until `detailed` is true, then refetches `/api/brew` and re-renders with the complete data. If the status request itself fails it retries with backoff, and after five failures it drops the loading banner and shows a "Lost contact with Sweepr" error instead. A "Loading Homebrew packages..." banner shows above the list until the detailed pass lands. If `brew info` fails (for example after an Xcode update, until the new license is accepted), the quick list is kept, `/api/brew/status` returns the failure in `error`, and the Homebrew tab shows a banner above the list until a refresh succeeds. When brew's message ends with "Please resolve this by running:" and a command, `suggested_fix()` extracts it into `fix_command`, and the banner shows that command with a Copy button and a "Check again" button. Because install reasons and dependencies come from the receipts, risk labels stay accurate even when the detailed pass fails.

### What each package shows

| Field | Source |
| --- | --- |
| Version | the linked keg's version from `brew info` (the receipt in the quick pass) |
| Description | formula/cask `desc` |
| Installed date | install timestamp |
| Size | `du -sk` on the Cellar path (batched for all formulae in one call); casks aren't sized |
| Outdated | `brew info` outdated flag |
| Pinned | `brew info` pinned flag |
| Dependencies | count of declared dependencies (`brew info`); receipt runtime dependencies in the quick pass |
| Installed on request | whether you asked for it, versus pulled in as a dependency; always true for casks. In the quick pass it comes from the receipt (`null` if there is none), in the detailed pass from `brew info` |
| Used by | installed formulae whose receipts list it in `runtime_dependencies` (both passes) |
| Service | a `homebrew.mxcl.<name>.plist` exists in a LaunchAgents/LaunchDaemons folder (both passes) |

These fields drive risk assessment. See [Risk assessment](risk-assessment.md).

### Uninstall

`POST /api/brew/{name}/uninstall` runs `brew uninstall` (with `--cask` when the body's `type` is `cask`; any `type` other than `formula` or `cask` is a 400), then, only if that succeeded, `brew autoremove`, which removes every dependency on the machine that nothing needs any more, not only the removed package's. The confirmation dialog says so, and the response lists what autoremove removed (`autoremoved`), which the UI includes in its message. Only packages in the scanned list (matching name *and* type, since a formula and a cask can share a name) are accepted; anything else is a 403. That also keeps option-like names out of brew's arguments, and `_is_safe_name` additionally rejects names with characters outside letters, digits and `-_@./+`. The uninstall gets up to `UNINSTALL_TIMEOUT` (15 min) because killing brew midway can leave a half-removed package, and stdin is closed so a cask that wants `sudo` fails fast instead of hanging. Autoremove gets `AUTOREMOVE_TIMEOUT` (5 min); if it fails, the uninstall still counts as a success with an empty `autoremoved`. Both run with `HOMEBREW_NO_AUTO_UPDATE` and `HOMEBREW_NO_INSTALL_CLEANUP` set. A failed uninstall returns a 500 with brew's error. The brew cache is invalidated afterward either way, which also bumps the generation so an in-flight detailed pass is discarded.

## npm globals

`scan_npm_globals` runs `npm list -g --json --depth=0` (30 s timeout) and `npm root -g` to find the global folder, then reads each package's `package.json` for a description and stats its directory for an install date. Sizes are computed with a single batched `du -sk` across all package directories. It returns `(packages, error)`; `/api/npm` passes `error` through so the UI can tell "nothing installed" from "npm failed" (a toast). Only the first `npm` on `PATH` is scanned, so globals installed under another Node (nvm, volta) don't appear.

### Uninstall

`POST /api/npm/{name}/uninstall` runs `npm uninstall -g {name}` (60 s timeout), with the same safe-name validation, and only for packages in the scanned list (403 otherwise; a failed uninstall is a 500). The npm cache is invalidated afterward. Core toolchain packages (`npm`, `corepack`, listed in `PROTECTED_PACKAGES` in `lib/npm_scanner.py`) ship with Node.js and can't be removed: they're marked `protected`, the UI shows a "Node.js" badge and disables the button, and the server refuses the request with a 403. Uninstalling npm with npm would leave no npm to reinstall it with.

## PATH note

When Sweepr runs as a LaunchAgent, its environment is minimal. The plist and launcher set `PATH` to include `/opt/homebrew/bin` (and `/usr/local/bin`) so `brew` and `npm` are found. If these tabs are unexpectedly empty when run as a background service, a missing `PATH` is the usual cause. See [Troubleshooting](troubleshooting.md).
