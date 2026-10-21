# Orphan detection

The Orphans tab finds files in `~/Library` (and the privileged helper folder) that belong to apps you no longer have installed. The scan lives in `lib/orphan_scanner.py` and runs via `GET /api/orphans`.

Anything offered here can be trashed with a couple of clicks, so the scanner is deliberately conservative: it would rather miss a leftover than offer something that belongs to macOS or to software that is still installed.

## Where it looks

| Category | Location |
| --- | --- |
| Preferences | `~/Library/Preferences` |
| Preferences (ByHost) | `~/Library/Preferences/ByHost` |
| Application Support | `~/Library/Application Support` |
| Caches | `~/Library/Caches` |
| Containers | `~/Library/Containers` |
| Saved Application State | `~/Library/Saved Application State` |
| HTTP Storages | `~/Library/HTTPStorages` |
| Privileged Helpers | `/Library/PrivilegedHelperTools` |

Deliberately **not** scanned:

- `~/Library/Accounts`: it holds the macOS Internet Accounts database (`Accounts4.sqlite`), not app data.
- `~/Library/Group Containers`: shared between apps and their extensions, and usually named `<TeamID>.<name>` (`UBF8T346G9.Office`), which can't be tied to an installed app by name.
- Launch agent and daemon plists: the [Agents tab](launch-agents.md) judges those by whether their executable still exists, which is much more reliable than name matching.

## What counts as an orphan

An entry must pass every check below to be offered:

1. **It has a reverse-DNS name** (`com.vendor.app`, `com.vendor.app.plist`, at least three dot-separated parts). Plain names like `MobileSync`, `Knowledge`, `pip` or `Accounts4.sqlite` belong to macOS, iOS devices or command-line tools as often as to apps, so they are never offered. Hidden files are skipped too.
2. **It isn't Apple's or shared.** A prefix list covers `com.apple*` plus Apple-owned identifiers that don't start with it (`org.cups.`, `is.workflow.` for Shortcuts, `org.swift.`, `edu.mit.Kerberos`, ...), shared frameworks like Sparkle, and the default cache or preference names of libraries embedded in many apps (Kingfisher, SDWebImage, Crashlytics, Sentry, LaunchDarkly). Those are named after the library, so they can't be blamed on any one app.
3. **It doesn't match anything installed** (`_matches_known`). Known ids are the installed apps' bundle ids **plus the labels of every launchd plist** in `/Library/LaunchDaemons`, `/Library/LaunchAgents` and `~/Library/LaunchAgents`, so security and management agents that have no `.app` still count as installed. Matching is deliberately generous:
   - exact bundle id, or a parent/child relationship (`com.vendor.app` versus `com.vendor.app.helper`);
   - any meaningful segment of the entry's id matching a known app's name parts. Generic words (`GENERIC_PARTS`: installer, helper, agent, update, client, ...) don't count: they appear in unrelated ids and used to hide real leftovers (Wondershare's installer matched Adobe's `com.adobe.acc.installer.v2`);
   - the cleaned name matching, or being contained in, a known name (and vice versa) for names longer than three characters.
4. **Apps kept outside the Applications folders count as installed** through `TOOL_OWNED`, which maps a bundle id to the tool's install folder. Playwright, for example, keeps its private WebKit browser (`org.webkit.Playwright`) in `~/Library/Caches/ms-playwright`; while that folder exists, its cache and preferences aren't orphans. Add an entry when a tool-managed app shows up as a false positive.
5. **Spotlight doesn't know an installed copy** (`_installed_elsewhere`), a last check for apps the app scan never sees: swiftDialog in `/Library/Application Support/Dialog`, Synology Drive in `~/Library/Application Support`, apps you build yourself. It looks for an app with the entry's id or a parent of it (down to three components), then for the vendor's apps (`com.vendor.*`) and the extensions, helpers and XPC services bundled inside them, which Spotlight doesn't index and whose ids can be unrelated to the app's. Results are cached per scan. If Spotlight can't be asked, the entry isn't offered.
6. **Privileged helpers that a launchd plist still references are kept**, whatever they're called (`_helpers_in_use`). An unparseable plist still protects the helper with its own name.
7. **It has a size and could be read completely.** Sizes come from `lib/sizes.py` (`disk_usage`), which reports on-disk usage like `du` and whether everything was readable. Empty entries and anything only partly readable (TCC-protected folders report 0 B or a partial size) are dropped, because we can't see what they contain.

## Grouping

The scanner groups results by vendor (derived from bundle id segments, with a lookup table for common vendors like Adobe, Microsoft, and JetBrains). The frontend (`static/js/orphans.js`) then regroups everything by **category** for display, ordered by a canonical list (Caches, Application Support, Containers, Preferences, and so on). Within a category the detail view breaks files down by the app they came from, so you can see and uncheck individual items before cleaning up. The banner reminds you that matching is by name.

## Removal

Selected paths are sent to `POST /api/orphans/trash`, which only accepts paths from the latest orphan scan and moves them to `~/.Trash` using the same safe mechanism as everything else. See [Removal and uninstallers](removal.md).

## Protected locations

Parts of `~/Library` are protected by macOS privacy controls (TCC). Without Full Disk Access the scan cannot list or fully read them. Unlistable categories are skipped with a warning; unreadable entries are left out of the results (see rule 7). This is expected. See [Troubleshooting](troubleshooting.md).
