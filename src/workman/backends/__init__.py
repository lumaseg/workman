"""Backend selection.

Detection probes *capability*, never identity. ``XDG_CURRENT_DESKTOP`` is
empty whenever a compositor is launched from a TTY instead of a display
manager, so it cannot be relied on to name what is running — asking the
compositor directly always can.
"""

import os

from workman.backends.base import Backend
from workman.backends.gnome import GnomeBackend
from workman.backends.sway import SwayBackend
from workman.errors import UnsupportedDesktopError

BACKENDS = (SwayBackend, GnomeBackend)

__all__ = ["Backend", "GnomeBackend", "SwayBackend", "BACKENDS", "detect"]


def detect():
    """Return a backend that can talk to the running compositor."""
    for backend_cls in BACKENDS:
        if backend_cls.is_available():
            return backend_cls()
    raise UnsupportedDesktopError(_unavailable_message())


def _unavailable_message():
    # GNOME running without the extension is a distinct and fixable problem;
    # telling that user their desktop is unsupported would be misleading.
    if GnomeBackend.looks_installed():
        from workman.backends.gnome import EXTENSION_MISSING_MSG
        return EXTENSION_MISSING_MSG
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"
    session_type = os.environ.get("XDG_SESSION_TYPE") or "unknown"
    return (
        "Workman couldn't find a supported compositor.\n"
        f"  Detected desktop: {desktop}\n"
        f"  Detected session: {session_type}\n"
        "Available backends:\n"
        "  - sway   needs $SWAYSOCK set and swaymsg on PATH\n"
        "  - gnome  needs the Workman GNOME Shell extension (see README)\n"
        "Support for KDE and XFCE is planned for future versions."
    )
