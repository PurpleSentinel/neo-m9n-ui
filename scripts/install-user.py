#!/usr/bin/env python3
"""Install the desktop application for the current user without pip, venv or administrative access."""
import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = Path.home() / ".local/share/neo-m9n-cli"
bin_dir = Path.home() / ".local/bin"
data.mkdir(parents=True, exist_ok=True)
bin_dir.mkdir(parents=True, exist_ok=True)
shutil.copytree(root / "m9n", data / "m9n", dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
launcher = bin_dir / "m9n"
temp = bin_dir / ".m9n-install.tmp"
temp.write_text("#!/usr/bin/env python3\nimport sys\nsys.path.insert(0, " + repr(str(data)) + ")\nfrom m9n.gui.application import main\nraise SystemExit(main())\n")
temp.chmod(0o755)
os.replace(temp, launcher)
print(f"Installed {launcher}\nRun: {launcher}")
if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
    print('Add this to ~/.bashrc: export PATH="$HOME/.local/bin:$PATH"')
