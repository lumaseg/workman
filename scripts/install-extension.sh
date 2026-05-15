#!/usr/bin/env bash
# Install the Workman GNOME Shell extension into the current user's
# extension directory, picking the variant that matches the running
# GNOME Shell.
set -euo pipefail

if ! command -v gnome-shell >/dev/null 2>&1; then
    echo "gnome-shell not found — is GNOME installed?" >&2
    exit 1
fi

major=$(gnome-shell --version | awk '{print $3}' | cut -d. -f1)
if [[ "$major" =~ ^[0-9]+$ ]] && (( major < 45 )); then
    variant="legacy"
else
    variant="modern"
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
src="$repo_root/extension/$variant"
dest="$HOME/.local/share/gnome-shell/extensions/workman@workman"

mkdir -p "$dest"
install -m644 "$src/extension.js" "$dest/extension.js"
install -m644 "$src/metadata.json" "$dest/metadata.json"

echo "Installed $variant extension to $dest"

if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable workman@workman || true
    echo "Enabled workman@workman."
fi

echo "Log out and back in to activate the extension."
