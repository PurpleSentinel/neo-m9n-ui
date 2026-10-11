#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v dnf >/dev/null; then
  echo "This helper targets conventional Fedora Workstation/Server. See docs/FEDORA.md for Atomic desktops." >&2
  exit 1
fi
sudo dnf install -y python3 python3-gobject gobject-introspection gtk4 libadwaita usbutils util-linux psmisc
python3 "$repo_dir/scripts/install-user.py"
echo "USB permissions are handled separately; see docs/FEDORA.md."
