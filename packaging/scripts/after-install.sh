#!/bin/sh
# Copy the extension into the live GNOME Shell extension directory.
# Idempotent: safe to run on install and upgrade alike.
set -e

EXTDIR="/usr/share/gnome-shell/extensions/workman@workman"
STAGE="/usr/share/workman/extension"

install -dm755 "$EXTDIR"
install -m644 "$STAGE/extension.js"  "$EXTDIR/extension.js"
install -m644 "$STAGE/metadata.json" "$EXTDIR/metadata.json"

exit 0
