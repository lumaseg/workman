"""End-to-end tests of the GNOME backend against a stand-in `gdbus`.

The machine this backend was refactored on has no GNOME Shell, so the GNOME
path could not be executed at all. These tests close most of that gap: a fake
`gdbus` on PATH answers exactly as the Workman Shell extension does, and the
real `save_session` / `restore_session` run against it unmodified.

That exercises everything the refactor actually changed — backend detection,
the v1/v2 session shim, reuse counting, --close-others, launch decisions, and
the precise gdbus argv the extension receives. What it cannot cover is whether
Mutter honours a MoveWindow call, but the three functions that issue those
calls were moved byte-identically from the pre-refactor session.py, so they
carry the risk they always did and no more.

Run: python -m pytest tests/  (or: python tests/test_gnome_integration.py)
"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workman import session

# Answers GetWindows/MoveWindow/CloseWindow the way the extension does, logs
# every invocation, and drops closed windows from its state so a later
# GetWindows reflects them.
FAKE_GDBUS = '''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
method = ""
for i, a in enumerate(args):
    if a == "--method" and i + 1 < len(args):
        method = args[i + 1]

with open(os.environ["WORKMAN_FAKE_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")

state = os.environ["WORKMAN_FAKE_WINDOWS"]
if method.endswith("NameHasOwner"):
    print("(true,)")
elif method.endswith("GetWindows"):
    with open(state) as f:
        print("('" + json.dumps(json.load(f)) + "',)")
elif method.endswith("CloseWindow"):
    target = int(args[-1])
    with open(state) as f:
        windows = json.load(f)
    with open(state, "w") as f:
        json.dump([w for w in windows if w.get("id") != target], f)
    print("(true,)")
elif method.endswith("MoveWindow"):
    print("(true,)")
else:
    print("()")
'''

FAKE_APP = '''#!/bin/sh
echo "$0 $@" >> "$WORKMAN_FAKE_LAUNCHES"
'''


class Harness:
    """A temp dir holding the fake gdbus, a fake app, and session storage."""

    def __init__(self, windows):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.state = root / "windows.json"
        self.state.write_text(json.dumps(windows))
        self.log = root / "gdbus.log"
        self.log.touch()
        self.launches = root / "launches.log"
        self.launches.touch()
        self.app = root / "fakeapp"
        self.app.write_text(FAKE_APP)
        self.app.chmod(self.app.stat().st_mode | stat.S_IEXEC)
        gdbus = root / "gdbus"
        gdbus.write_text(FAKE_GDBUS)
        gdbus.chmod(gdbus.stat().st_mode | stat.S_IEXEC)
        self.sessions = root / "sessions"
        self.sessions.mkdir()
        self._saved = {}

    def __enter__(self):
        self._saved = {
            "PATH": os.environ.get("PATH", ""),
            "SWAYSOCK": os.environ.get("SWAYSOCK"),
            "SESSIONS_DIR": session.SESSIONS_DIR,
        }
        os.environ["PATH"] = f"{self.dir.name}:{self._saved['PATH']}"
        # detect() tries Sway first; on the dev machine it would win.
        os.environ.pop("SWAYSOCK", None)
        os.environ["WORKMAN_FAKE_LOG"] = str(self.log)
        os.environ["WORKMAN_FAKE_WINDOWS"] = str(self.state)
        os.environ["WORKMAN_FAKE_LAUNCHES"] = str(self.launches)
        session.SESSIONS_DIR = self.sessions
        return self

    def __exit__(self, *exc):
        os.environ["PATH"] = self._saved["PATH"]
        if self._saved["SWAYSOCK"] is not None:
            os.environ["SWAYSOCK"] = self._saved["SWAYSOCK"]
        session.SESSIONS_DIR = self._saved["SESSIONS_DIR"]
        self.dir.cleanup()

    def calls(self, method):
        """Every fake-gdbus invocation of `method`, as argument lists."""
        found = []
        for line in self.log.read_text().splitlines():
            args = json.loads(line)
            if any(a.endswith("." + method) for a in args):
                found.append(args)
        return found

    def launched(self):
        return self.launches.read_text().splitlines()

    def written(self, name):
        return json.loads((self.sessions / f"{name}.json").read_text())


def window(wid, wm_class, title, x, y, w=800, h=600, pid=None):
    return {"id": wid, "wm_class": wm_class, "title": title,
            "x": x, "y": y, "width": w, "height": h, "pid": pid}


def test_gnome_backend_is_selected_when_the_extension_answers():
    with Harness([window(1, "code", "Editor", 0, 0)]) as h:
        from workman.backends import detect
        assert detect().name == "gnome"
        assert h.calls("NameHasOwner")


def test_save_writes_a_v2_file_tagged_gnome():
    with Harness([window(1, "code", "Editor", 10, 20),
                  window(2, "code", "Other", 30, 40)]) as h:
        session.save_session("s")
        doc = h.written("s")
        assert doc["version"] == 2 and doc["backend"] == "gnome"
        windows = doc["data"]["windows"]
        # class_index's replacement: the Nth window of an app, in order.
        assert [w["app_index"] for w in windows] == [0, 1]
        assert [w["wm_class"] for w in windows] == ["code", "code"]


def test_restore_moves_every_window_with_the_saved_geometry():
    with Harness([window(1, "code", "Editor", 0, 0)]) as h:
        session.save_session("s")
        session.restore_session("s")
        moves = h.calls("MoveWindow")
        assert len(moves) == 1
        # gdbus argv tail: wm_class index x y width height
        assert moves[0][-6:] == ["code", "0", "0", "0", "800", "600"]


def test_a_v1_session_file_still_restores():
    """Pre-0.2 files are a bare array using `class_index`, not `app_index`."""
    with Harness([window(1, "code", "First", 0, 0),
                  window(2, "code", "Second", 0, 0)]) as h:
        # A non-zero index, so that reading `app_index` with a default of 0
        # cannot accidentally produce the right answer.
        (h.sessions / "old.json").write_text(json.dumps([
            {"wm_class": "code", "class_index": 1, "title": "Second",
             "x": 111, "y": 222, "width": 333, "height": 444},
        ]))
        session.restore_session("old")
        moves = h.calls("MoveWindow")
        assert moves[0][-6:] == ["code", "1", "111", "222", "333", "444"]


def test_already_open_apps_are_reused_not_relaunched():
    with Harness([window(1, "code", "Editor", 0, 0, pid=1)]) as h:
        session.save_session("s")
        doc = h.written("s")
        doc["data"]["windows"][0]["exe"] = str(h.app)
        (h.sessions / "s.json").write_text(json.dumps(doc))
        session.restore_session("s")
        assert h.launched() == []
        assert len(h.calls("MoveWindow")) == 1


def test_a_missing_app_is_launched():
    with Harness([window(1, "code", "Editor", 0, 0, pid=1)]) as h:
        session.save_session("s")
        doc = h.written("s")
        doc["data"]["windows"][0]["exe"] = str(h.app)
        (h.sessions / "s.json").write_text(json.dumps(doc))
        # The app is gone by the time we restore.
        h.state.write_text(json.dumps([]))
        session.restore_session("s")
        assert len(h.launched()) == 1


def test_close_others_closes_only_windows_outside_the_session():
    with Harness([window(1, "code", "Editor", 0, 0),
                  window(2, "code", "Second", 0, 0),
                  window(3, "chatty", "Not in session", 0, 0)]) as h:
        session.save_session("s")
        doc = h.written("s")
        doc["data"]["windows"] = [w for w in doc["data"]["windows"]
                                  if w["wm_class"] == "code"]
        (h.sessions / "s.json").write_text(json.dumps(doc))
        session.restore_session("s", close_others=True)
        closed = h.calls("CloseWindow")
        assert len(closed) == 1 and closed[0][-1] == "3"


def test_a_window_with_no_wm_class_is_never_closed():
    """Desktop and shell surfaces report no class and must be left alone."""
    with Harness([window(1, "code", "Editor", 0, 0),
                  window(2, "", "gnome-shell surface", 0, 0)]) as h:
        session.save_session("s")
        doc = h.written("s")
        doc["data"]["windows"] = [w for w in doc["data"]["windows"]
                                  if w["wm_class"] == "code"]
        (h.sessions / "s.json").write_text(json.dumps(doc))
        session.restore_session("s", close_others=True)
        assert h.calls("CloseWindow") == []


def test_a_flatpak_session_reuses_a_natively_packaged_window():
    """The one intentional GNOME behaviour change, exercised end to end.

    Before, reuse counting was exact: a session recording
    org.mozilla.firefox saw no native `firefox` open and launched a second
    browser. The extension's own matching then had two windows to choose from.
    """
    with Harness([window(1, "firefox", "Docs", 0, 0, pid=1)]) as h:
        (h.sessions / "ff.json").write_text(json.dumps({
            "version": 2, "backend": "gnome", "data": {"windows": [
                {"wm_class": "org.mozilla.firefox", "app_index": 0,
                 "title": "Docs", "x": 5, "y": 6, "width": 7, "height": 8,
                 "exe": str(h.app)},
            ]}}))
        session.restore_session("ff")
        assert h.launched() == []
        assert len(h.calls("MoveWindow")) == 1


def test_dry_run_touches_nothing():
    with Harness([window(1, "code", "Editor", 0, 0)]) as h:
        session.save_session("s")
        session.restore_session("s", close_others=True, dry_run=True)
        assert h.calls("MoveWindow") == []
        assert h.calls("CloseWindow") == []
        assert h.launched() == []


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failures else 0)
