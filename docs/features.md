# Feature overview

- **Applications**: scan `/Applications`, `/System/Applications`, and `~/Applications` with icons, version info, and size
- **Associated files**: finds related preferences, caches, containers, and support files across `~/Library` when removing an app
- **Storage analyzer**: a separate full-screen mode (opened from the "Analyze Storage" button, since it is read-only) that drills into top-level folders (Home, Documents, Downloads, Library, Applications, and more) with live-computed sizes, largest first
- **Orphan detection**: scans `~/Library` (and `/Library/PrivilegedHelperTools`) for files belonging to apps that are no longer installed
- **Homebrew**: lists all installed formulae and casks; uninstall directly from the UI
- **npm**: lists all globally installed npm packages; uninstall directly from the UI
- **LaunchAgents**: lists user and system LaunchAgents and LaunchDaemons, flags stale or failing ones, and removes them (unloading the job too)
- **Custom uninstaller detection**: finds official uninstallers bundled with apps and surfaces them before you trash anything
- **Risk assessment**: warns before removing dependencies, system-integrated apps, or toolchain packages
- **Show in Finder**: reveal an app, or any folder or file in the storage analyzer, in Finder
- **Dark mode**: follows your system setting with a manual toggle
- **Progressive loading**: app list appears in under 1 second; sizes load in the background
