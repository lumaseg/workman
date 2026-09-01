#!/usr/bin/env bash
# Install the Workman GNOME Shell extension into the current user's
# extension directory.
set -euo pipefail

if ! command -v gnome-shell >/dev/null 2>&1; then
    echo "gnome-shell not found — is GNOME installed?" >&2
    exit 1
fi

major=$(gnome-shell --version | awk '{print $3}' | cut -d. -f1)
if [[ "$major" =~ ^[0-9]+$ ]] && (( major < 45 )); then
    echo "GNOME Shell $major is not supported — Workman needs 45 or newer." >&2
    echo "(Workman 0.1.x supported GNOME 42-44 via a second extension variant.)" >&2
    exit 1
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
dest="$HOME/.local/share/gnome-shell/extensions/workman@workman"

mkdir -p "$dest"
install -m644 "$repo_root/extension/extension.js" "$dest/extension.js"
install -m644 "$repo_root/extension/metadata.json" "$dest/metadata.json"

echo "Installed extension to $dest"
echo "Now log out and back in, then: gnome-extensions enable workman@workman"
