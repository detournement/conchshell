#!/bin/sh
# Only Python's standard library is needed; no package manager or build step.
set -eu
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'conchshell requires Python 3.9 or newer (python3 not found)' >&2
    exit 1
fi
installer_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$installer_dir/install.py" "$@"
