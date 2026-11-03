"""App data kept outside ~/Library: hidden folders in the home folder and the
command-line links apps install in /usr/local/bin.

Many apps with a command-line side keep their data in ~/.<name> (Ollama keeps
its downloaded models in ~/.ollama, often many GB) and link a command into
/usr/local/bin. Neither is under ~/Library, so the associated-file and orphan
scans used to miss both.
"""
import os
import shutil
import subprocess
from pathlib import Path

from lib.log import log

HOME = Path.home()

# Well-known tools: which home folders are theirs, which app and command own them
KNOWN_TOOLS = [
    {"name": "Ollama", "folders": [".ollama"], "apps": ["Ollama"], "commands": ["ollama"]},
    {"name": "Docker", "folders": [".docker"], "apps": ["Docker"], "commands": ["docker"]},
    {"name": "Visual Studio Code", "folders": [".vscode", ".vscode-shared"],
     "apps": ["Visual Studio Code", "Code"], "commands": ["code"]},
    {"name": "Cursor", "folders": [".cursor"], "apps": ["Cursor"], "commands": ["cursor"]},
    {"name": "LM Studio", "folders": [".lmstudio", ".cache/lm-studio"], "apps": ["LM Studio"],
     "commands": ["lms"]},
]

# Never offered, whatever an app is called: credentials, shell and toolchain state
NEVER = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker-credentials", ".netrc", ".git",
    ".config", ".local", ".cache", ".Trash", ".npm", ".nvm", ".pyenv", ".rbenv", ".rvm",
    ".sdkman", ".cargo", ".rustup", ".m2", ".gradle", ".zsh_sessions", ".bash_sessions",
    ".claude", ".codex", ".copilot", ".github", ".vim", ".tsh", ".pip",
}

# Apps' "install command line tool" links land here; only links pointing into an
# .app bundle are ever considered
CLI_LINK_DIRS = ("/usr/local/bin", "/opt/homebrew/bin")


def _slug(app_name):
    return "".join(c for c in app_name.lower() if c.isalnum())


def home_folders_for_app(app_name):
    """Existing home-folder paths that belong to this app: from KNOWN_TOOLS, plus
    ~/.<name>, ~/.config/<name>, ~/.cache/<name> and ~/.local/share/<name> for a
    distinctive name (4+ characters, not in NEVER). Returns [(path, commands)]."""
    found = []
    lower = (app_name or "").lower()
    for tool in KNOWN_TOOLS:
        if lower in (a.lower() for a in tool["apps"]):
            found += [(str(HOME / f), tool["commands"]) for f in tool["folders"]]
    slug = _slug(app_name or "")
    if len(slug) >= 4 and "." + slug not in NEVER:
        for rel in ("." + slug, ".config/" + slug, ".cache/" + slug, ".local/share/" + slug):
            found.append((str(HOME / rel), [slug]))
    seen, result = set(), []
    for path, commands in found:
        if path not in seen and os.path.isdir(path) and not os.path.islink(path):
            seen.add(path)
            result.append((path, commands))
    return result


def external_command(commands, app_path=None):
    """A command with one of these names that resolves somewhere other than inside
    app_path (e.g. a Homebrew install), as "name (path)", or None. Its data folder
    is then shared with that install."""
    prefix = (os.path.realpath(app_path).rstrip("/") + "/") if app_path else None
    for name in commands:
        path = shutil.which(name)
        if not path:
            continue
        real = os.path.realpath(path)
        if prefix and real.startswith(prefix):
            continue  # it's this app's own link; it goes with the app
        return f"{name} command ({real})"
    return None


def _link_target(link):
    target = os.readlink(link)
    return os.path.normpath(os.path.join(os.path.dirname(link), target))


def cli_links_into(app_path):
    """Symlinks in /usr/local/bin that point into this app bundle."""
    prefix = os.path.realpath(app_path).rstrip("/") + "/"
    links = []
    for d in CLI_LINK_DIRS:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            p = os.path.join(d, name)
            try:
                if os.path.islink(p) and (_link_target(p) + "/").startswith(prefix):
                    links.append(p)
            except OSError:
                continue
    return links


def dangling_app_links():
    """Symlinks in /usr/local/bin into an .app bundle that no longer exists, as
    [(link, app_path)]: commands left behind by apps already removed."""
    result = []
    for d in CLI_LINK_DIRS:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            p = os.path.join(d, name)
            try:
                if not os.path.islink(p) or os.path.exists(p):
                    continue
                target = _link_target(p)
            except OSError:
                continue
            i = target.find(".app/")
            if i == -1:
                continue
            app_path = target[:i + 4]
            if not os.path.exists(app_path):
                result.append((p, app_path))
    return result


def app_bundle_exists_anywhere(app_names):
    """True if Spotlight knows an app bundle with one of these names outside the
    Trash (an install the app scan doesn't cover)."""
    for name in app_names:
        query = "kMDItemFSName == '%s.app'c" % name.replace("'", "")
        try:
            out = subprocess.run(["mdfind", query], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("app_bundle_exists_anywhere: mdfind failed for %s: %s", name, e)
            return True  # can't confirm it's gone
        if any(p.strip() and "/.Trash/" not in p for p in out.splitlines()):
            return True
    return False
