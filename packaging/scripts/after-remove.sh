#!/bin/sh
# Remove the variant we copied into the live extension directory, but only on
# real removal — not when this is the cleanup half of an upgrade.
#   deb postrm arg: remove | purge | upgrade | ...
#   rpm %postun arg: 0 = uninstall, 1 = upgrade
set -e

case "$1" in
    upgrade|1) exit 0 ;;
esac

EXTDIR="/usr/share/gnome-shell/extensions/workman@workman"
rm -f "$EXTDIR/extension.js" "$EXTDIR/metadata.json"
rmdir "$EXTDIR" 2>/dev/null || true

exit 0
