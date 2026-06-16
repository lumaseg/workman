#!/bin/sh
# Runs on install, upgrade, and (on Debian) the gnome-shell trigger.
# Picks the extension variant matching the GNOME Shell actually present on
# THIS machine — the equivalent of the build-time check in the AUR PKGBUILD,
# moved to install time because a prebuilt .deb/.rpm doesn't know the target's
# GNOME version. Idempotent: safe to run repeatedly.
set -e

EXTDIR="/usr/share/gnome-shell/extensions/workman@workman"
STAGE="/usr/share/workman/extension"

variant=modern
if command -v gnome-shell >/dev/null 2>&1; then
    major=$(gnome-shell --version 2>/dev/null | awk '{print $3}' | cut -d. -f1)
    case "$major" in
        ''|*[!0-9]*) major=99 ;;   # unparseable -> assume a modern shell
    esac
    [ "$major" -lt 45 ] && variant=legacy
fi

install -dm755 "$EXTDIR"
install -m644 "$STAGE/$variant/extension.js"  "$EXTDIR/extension.js"
install -m644 "$STAGE/$variant/metadata.json" "$EXTDIR/metadata.json"

exit 0
