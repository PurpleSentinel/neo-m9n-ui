#!/usr/bin/env python3
"""Install NEO-M9N Monitor for the current user, or remove it with --uninstall.

No pip, venv or administrator rights: the package goes to ~/.local/share/neo-m9n,
the launcher to ~/.local/bin/m9n, and the desktop entry, AppStream metadata and
icons to the usual places under ~/.local/share so GNOME lists the application.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "io.github.PurpleSentinel.NeoM9N"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "m9n/gui/data"


def paths():
    share = Path.home() / ".local/share"
    return {
        "package": share / "neo-m9n",
        "legacy": share / "neo-m9n-cli",  # 1.x installs
        "launcher": Path.home() / ".local/bin/m9n",
        "desktop": share / "applications" / f"{APP_ID}.desktop",
        "metainfo": share / "metainfo" / f"{APP_ID}.metainfo.xml",
        "icon": share / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg",
        "symbolic": share / "icons/hicolor/symbolic/apps" / f"{APP_ID}-symbolic.svg",
    }


def write_atomically(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def refresh_caches(p):
    """Best effort: GNOME also notices new files on its own."""
    commands = [["update-desktop-database", "-q", str(p["desktop"].parent)],
                ["gtk-update-icon-cache", "-q", "-t", "-f", str(p["icon"].parents[2])]]
    for command in commands:
        if shutil.which(command[0]) and Path(command[-1]).is_dir():
            subprocess.run(command, check=False, capture_output=True)


def remove(p):
    for key in ("package", "legacy"):
        shutil.rmtree(p[key], ignore_errors=True)
    for key in ("launcher", "desktop", "metainfo", "icon", "symbolic"):
        p[key].unlink(missing_ok=True)


def install(p):
    # Replace the package directory so files removed upstream do not linger after an upgrade.
    for key in ("package", "legacy"):
        shutil.rmtree(p[key], ignore_errors=True)
    shutil.copytree(ROOT / "m9n", p["package"] / "m9n", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    write_atomically(p["launcher"], "#!/usr/bin/env python3\nimport sys\nsys.path.insert(0, "
                     + repr(str(p["package"])) + ")\nfrom m9n.gui.application import main\nraise SystemExit(main())\n",
                     0o755)
    desktop = (DATA / f"{APP_ID}.desktop").read_text(encoding="utf-8")
    # The session PATH may not include ~/.local/bin, so the entry uses the launcher's full path.
    write_atomically(p["desktop"], desktop.replace("\nExec=m9n\n", f"\nExec={p['launcher']}\n"))
    write_atomically(p["metainfo"], (DATA / f"{APP_ID}.metainfo.xml").read_text(encoding="utf-8"))
    write_atomically(p["icon"], (DATA / f"icons/hicolor/scalable/apps/{APP_ID}.svg").read_text(encoding="utf-8"))
    write_atomically(p["symbolic"],
                     (DATA / f"icons/hicolor/symbolic/apps/{APP_ID}-symbolic.svg").read_text(encoding="utf-8"))
    refresh_caches(p)


def main(argv):
    p = paths()
    if "--uninstall" in argv:
        remove(p)
        refresh_caches(p)
        print("Removed NEO-M9N Monitor. Your settings (~/.config/neo-m9n) and evidence files were left in place.")
        return 0
    install(p)
    print(f"Installed NEO-M9N Monitor: open it from Activities, or run {p['launcher']}")
    if str(p["launcher"].parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print('To run m9n from a terminal, add this to ~/.bashrc: export PATH="$HOME/.local/bin:$PATH"')
    print("Remove it again with: python3 scripts/install-user.py --uninstall")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
