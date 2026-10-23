# Risk assessment

Before you remove anything, Sweepr shows a colored banner in the detail view that flags whether the item is safe to remove or deserves caution. The logic lives in `static/js/utils.js` (`getAppRisk`, `getBrewRisk`, `getNpmRisk`, `renderRiskBanner`) and runs entirely on the frontend.

The intent is a nudge, not a wall. Nothing is blocked except system apps, Sweepr's own launcher and agent, and the Node.js core npm packages; the banner is there so you pause on the risky cases.

## Applications

Evaluated in order; the first matching rule wins:

| Condition | Level | Message |
| --- | --- | --- |
| System app | High | Cannot be removed |
| Sweepr itself (`is_self`) | High | Can't be removed from inside Sweepr; run `launchagent/uninstall.sh` |
| Web shortcut (`.webloc`) | Low | Web shortcut, safe to remove |
| Leftover folder | Low | From an uninstalled app, safe to remove |
| Has a custom uninstaller | High | Deep system integration; use the official uninstaller instead |
| Last used more than 6 months ago | Low | Not used in N months, safe to remove |
| Otherwise | None | No banner |

The "not used in 6 months" check is based on the app's last-used date from Spotlight (months are counted as 30 days). An app with no last-used date gets no banner. A leftover folder that contains an uninstaller still gets the low-risk leftover banner, because that rule comes first.

## Homebrew

Checked in this order; the first match wins.

| Condition | Level | Message |
| --- | --- | --- |
| Pinned | High | Intentionally locked to this version |
| Other installed formulae need it (`used_by`) | High | Required by N installed packages (up to three named); Homebrew will refuse to uninstall it while they are installed |
| Install reason unknown (`installed_on_request` is `null`, e.g. no receipt) | High | Could not confirm how it was installed or what needs it |
| Registered as a `brew services` service | High | Runs as a background service; anything that connects to it will stop working |
| Installed as a dependency, nothing needs it now | Low | Leftover dependency; check you don't use it directly |
| Cask | Low | App installed by you |
| Otherwise | Low | Installed by you, and no other installed package needs it |

Whether you installed a package yourself says nothing about whether other packages need it (`openssl@3` is commonly both), so the `used_by` check comes first.

## npm

| Condition | Level | Message |
| --- | --- | --- |
| `npm` or `corepack` (`protected`) | High | Ships with Node.js; removing it would break npm and node. Removal is blocked in the UI and refused by the server |
| Otherwise | Low | Standalone CLI tool, safe to remove |

## LaunchAgents

Agents are assessed in `renderAgentDetail` in `static/js/agents.js`, in this order:

| Condition | Level | Message |
| --- | --- | --- |
| Sweepr's own agent (`is_self`) | High | Run `launchagent/uninstall.sh` to uninstall Sweepr (removal is disabled) |
| Stale | Low | Can't run because of the `stale_reason` (program missing, or nothing to run), so safe to remove |
| Failing | High | Keeps failing (with the last exit code); may be a duplicate or broken setup, so find out what it belongs to first |
| Plist couldn't be parsed (`parse_error`) | High | Sweepr can't tell what it runs |
| Otherwise (active) | High | Removing it may affect running services |

See [LaunchAgents](launch-agents.md).

## Orphans

`static/js/orphans.js` always shows a low-risk banner: the files are named for apps that are no longer installed. It also reminds you that matching is by name, so review the list, and that everything goes to the Trash and can be restored. See [Orphan detection](orphans.md).
