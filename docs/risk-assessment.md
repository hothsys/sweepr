# Risk assessment

Before you remove anything, Sweepr shows a colored banner in the detail view that flags whether the item is safe to remove or deserves caution. The logic lives in `static/js/utils.js` (`getAppRisk`, `getBrewRisk`, `getNpmRisk`, `renderRiskBanner`) and runs entirely on the frontend.

The intent is a nudge, not a wall. Nothing is blocked except system apps; the banner is there so you pause on the risky cases.

## Applications

Evaluated in order; the first matching rule wins:

| Condition | Level | Message |
| --- | --- | --- |
| System app | High | Cannot be removed |
| Sweepr itself (`is_self`) | High | Can't be removed from inside Sweepr; run `launchagent/uninstall.sh` |
| Web shortcut (`.webloc`) | Low | Just a bookmark, safe to remove |
| Leftover folder | Low | From an uninstalled app, safe to remove |
| Has a custom uninstaller | High | Deep system integration; use the official uninstaller instead |
| Not used in over 6 months | Low | Surfaced as safe, with the month count |
| Otherwise | None | No banner |

The "not used in 6 months" check is based on the app's last-used date from Spotlight.

## Homebrew

Checked in this order; the first match wins.

| Condition | Level | Message |
| --- | --- | --- |
| Pinned | High | Intentionally locked to this version |
| Other installed formulae need it (`used_by`) | High | Required by N installed packages; Homebrew refuses to uninstall it while they are installed |
| Install reason unknown (no receipt) | High | Could not confirm how it was installed or what needs it |
| Registered as a `brew services` service | High | Anything that connects to it will stop working |
| Installed as a dependency, nothing needs it now | Low | Leftover dependency; check you don't use it directly |
| Cask | Low | App installed by you |
| Otherwise | Low | Installed by you, and no other installed package needs it |

Whether you installed a package yourself says nothing about whether other packages need it (`openssl@3` is commonly both), so the `used_by` check comes first.

## npm

| Condition | Level | Message |
| --- | --- | --- |
| `npm` or `corepack` (`protected`) | High | Ships with Node.js; removal is blocked in the UI and refused by the server |
| Otherwise | Low | Standalone CLI tool, safe to remove |

## LaunchAgents

Agents are assessed in `static/js/agents.js`: a stale agent (its executable is missing) is low risk, while an active agent is high risk because removing it stops a service that is still wired up. See [LaunchAgents](launch-agents.md).

## Orphans

Orphaned files are low risk by definition: they belong to apps that are no longer installed. See [Orphan detection](orphans.md).
