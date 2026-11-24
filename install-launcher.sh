#!/bin/sh
set -eu

project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p -- "$applications"

python3 - "$project_dir" "$applications/super-productivity-bridge.desktop" <<'PY'
from pathlib import Path
from sys import argv

project, destination = argv[1:]
path = project.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
Path(destination).write_text(
    "[Desktop Entry]\n"
    "Type=Application\n"
    "Name=Super Productivity Bridge\n"
    "Comment=Review and import tasks from a JSON file\n"
    f"Path={path}\n"
    "Exec=python3 -m bridge\n"
    "Terminal=false\n"
    "Categories=Office;\n",
    encoding="utf-8",
)
PY
