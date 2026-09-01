"""GNOME Shell backend.

GNOME 49+ removed ``org.gnome.Shell.Eval`` and the X11 tools (wmctrl, xdotool,
libwnck) return nothing under Wayland, so window access goes through Workman's
own GNOME Shell extension, which exports ``org.workman.WindowManager`` on the
session bus. See ``extension/`` and ``scripts/install-extension.sh``.

The three module-level helpers below were moved verbatim out of ``session.py``
when the backend seam was introduced; ``GnomeBackend`` is a thin adapter over
them. There is no GNOME Shell on the machine this split was developed on, so
keeping the moved code byte-identical is the only available evidence that the
GNOME path did not regress.
"""

import json
import os
import shutil
import subprocess
import time

from workman.backends.base import Backend
from workman.errors import WorkmanError

BUS_NAME = "org.workman.WindowManager"
OBJECT_PATH = "/org/workman/WindowManager"

EXTENSION_MISSING_MSG = (
    "The Workman GNOME Shell extension isn't running.\n"
    "Install it (see README), then run:\n"
    "    gnome-extensions enable workman@workman\n"
    "and log out and back in."
)

def get_open_windows():
    result = subprocess.run([
        'gdbus', 'call',
        '--session',
        '--dest', 'org.workman.WindowManager',
        '--object-path', '/org/workman/WindowManager',
        '--method', 'org.workman.WindowManager.GetWindows'
    ], capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if 'ServiceUnknown' in stderr:
            raise WorkmanError(EXTENSION_MISSING_MSG)
        raise WorkmanError(f"Failed to query GNOME Shell: {stderr}")

    output = result.stdout.strip()
    try:
        json_str = output[2:output.rfind("',)")]
        return json.loads(json_str)
    except (ValueError, json.JSONDecodeError) as e:
        raise WorkmanError(
            f"Could not parse GNOME Shell response: {e}\nRaw output: {output}"
        )

def move_window(wm_class, index, x, y, width, height, retries=5, delay=1):
    """Move a window using the GNOME extension with retry logic."""
    for attempt in range(retries):
        cmd = [
            'gdbus', 'call',
            '--session',
            '--dest', 'org.workman.WindowManager',
            '--object-path', '/org/workman/WindowManager',
            '--method', 'org.workman.WindowManager.MoveWindow',
            wm_class,
            str(index),
            str(x),
            str(y),
            str(width),
            str(height)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if '(true,)' in result.stdout:
            return True
        if 'ServiceUnknown' in result.stderr:
            raise WorkmanError(EXTENSION_MISSING_MSG)
        print(f"  Retry {attempt + 1}/{retries} for {wm_class}[{index}]...")
        time.sleep(delay)
    return False

def close_window(window_id):
    """Gracefully close a window by its stable id via the GNOME extension."""
    cmd = [
        'gdbus', 'call',
        '--session',
        '--dest', 'org.workman.WindowManager',
        '--object-path', '/org/workman/WindowManager',
        '--method', 'org.workman.WindowManager.CloseWindow',
        str(window_id)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if '(true,)' in result.stdout:
        return True
    if 'ServiceUnknown' in result.stderr:
        raise WorkmanError(EXTENSION_MISSING_MSG)
    if 'UnknownMethod' in result.stderr:
        raise WorkmanError(
            "The installed Workman extension is too old to close windows.\n"
            "Reinstall it (see README) and log out and back in:\n"
            "    ./scripts/install-extension.sh"
        )
    return False


class GnomeBackend(Backend):
    """Flat-geometry backend: every window is fully described by x/y/w/h."""

    name = "gnome"

    @staticmethod
    def is_available():
        """True when the extension is actually on the bus.

        GNOME running *without* the extension is as unusable as no GNOME at
        all, so the extension's bus name — not the desktop's name — is the
        real capability test. `detect()` turns a negative here back into the
        install-the-extension message when GNOME itself is present.
        """
        if not shutil.which("gdbus"):
            return False
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.DBus",
                "--object-path", "/org/freedesktop/DBus",
                "--method", "org.freedesktop.DBus.NameHasOwner",
                BUS_NAME,
            ],
            capture_output=True, text=True,
        )
        return "(true,)" in result.stdout

    @staticmethod
    def looks_installed():
        """True if GNOME Shell is present, whether or not the extension is."""
        if "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
            return True
        return shutil.which("gnome-shell") is not None

    def capture(self):
        return {"windows": get_open_windows()}

    def list_windows(self):
        return get_open_windows()

    def app_key(self, window):
        return window.get("wm_class", "")

    def close_window(self, window):
        window_id = window.get("id")
        if window_id is None:
            # Pre-0.1.2 extensions didn't report a stable id, so there is
            # nothing to address the close to.
            return False
        return close_window(window_id)

    def place(self, payload, dry_run=False):
        for window in payload.get("windows", []):
            wm_class = window.get("wm_class")
            if not wm_class:
                continue
            # v2 sessions carry `app_index`; v1 files call the same number
            # `class_index`.
            index = window.get("app_index", window.get("class_index", 0))
            geometry = (
                window["x"], window["y"], window["width"], window["height"],
            )
            if dry_run:
                print(f"  Would move {wm_class}[{index}] to "
                      f"{geometry[0]},{geometry[1]} {geometry[2]}x{geometry[3]}")
                continue
            if move_window(wm_class, index, *geometry):
                print(f"  Moved {wm_class}[{index}] to "
                      f"{geometry[0]},{geometry[1]} {geometry[2]}x{geometry[3]}")
            else:
                print(f"  Could not move {wm_class}[{index}] after retries")
