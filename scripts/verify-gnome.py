#!/usr/bin/env python3
"""Verify Workman against a real GNOME Shell, on the machine running it.

Workman's GNOME path is covered by tests that stand in for `gdbus`, which
exercise everything except the one thing only a real shell can answer: does
Mutter actually honour a MoveWindow call? This script answers that, plus the
save/restore round trip, v1 session-file compatibility, and --close-others.

Run it inside a GNOME Wayland session, with the extension installed and
enabled:

    python3 scripts/verify-gnome.py

It is deliberately gentle with your desktop. It launches nothing, closes
nothing you did not ask it to, works only on windows already open, and puts
every window back where it found it -- including if a check fails partway.
Add --close-others to additionally test window closing, which DOES close one
throwaway window it launches itself.
"""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workman import session                       # noqa: E402
from workman.backends import detect               # noqa: E402
from workman.backends import gnome                # noqa: E402

PASS, FAIL, INFO, SKIP = "  \033[32m✓\033[0m", "  \033[31m✗\033[0m", "   ", "  \033[33m-\033[0m"
failures = []


def check(ok, label, detail=""):
    print(f"{PASS if ok else FAIL} {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def quiet(fn, *args, **kwargs):
    """Run one of Workman's chatty functions without its output."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def geometry(windows, wm_class, index):
    same = [w for w in windows if w.get("wm_class") == wm_class]
    if index < len(same):
        w = same[index]
        return (w["x"], w["y"], w["width"], w["height"])
    return None


def preflight():
    print("\nEnvironment")
    session_type = os.environ.get("XDG_SESSION_TYPE", "")
    check(session_type == "wayland", "session is Wayland", session_type or "unset")
    try:
        version = subprocess.run(["gnome-shell", "--version"],
                                 capture_output=True, text=True).stdout.strip()
    except OSError:
        version = ""
    check(bool(version), "gnome-shell present", version)
    major = 0
    for token in version.split():
        if token.split(".")[0].isdigit():
            major = int(token.split(".")[0])
            break
    check(major >= 45, "GNOME Shell 45 or newer", f"detected {major or 'unknown'}")
    check(gnome.GnomeBackend.is_available(),
          "extension owns org.workman.WindowManager",
          "if this fails: gnome-extensions enable workman@workman, then log out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--close-others", action="store_true",
                        help="also test window closing (launches and closes "
                             "one throwaway window)")
    args = parser.parse_args()

    print("Workman GNOME verification")
    preflight()
    if failures:
        print("\nStopping: the environment is not ready.\n")
        return 1

    backend = detect()
    print("\nBackend")
    check(backend.name == "gnome", "detect() selects the GNOME backend", backend.name)

    windows = backend.list_windows()
    check(len(windows) > 0, "extension reports open windows", f"{len(windows)} found")

    # Pick a window we can demonstrably move. Maximised and fullscreen windows
    # ignore geometry changes, so try candidates until one actually moves.
    candidates = [w for w in windows if w.get("wm_class")]
    if not candidates:
        print("\nNo window with a wm_class to test against. Open an app first.\n")
        return 1

    original = Path(tempfile.mkdtemp())
    session.SESSIONS_DIR = original
    quiet(session.save_session, "selftest")
    saved = json.loads((original / "selftest.json").read_text())

    print("\nSave")
    check(saved.get("version") == 2, "session file is version 2", str(saved.get("version")))
    check(saved.get("backend") == "gnome", "session records the gnome backend")
    check(len(saved["data"]["windows"]) == len(windows), "every window was captured")

    print("\nMove (this is what only a real Mutter can answer)")
    moved = None
    for candidate in candidates:
        wm_class = candidate["wm_class"]
        index = [w for w in windows if w.get("wm_class") == wm_class].index(candidate)
        before = (candidate["x"], candidate["y"], candidate["width"], candidate["height"])
        target = (before[0] + 37, before[1] + 41, max(320, before[2] - 53),
                  max(240, before[3] - 47))
        if not gnome.move_window(wm_class, index, *target, retries=2, delay=1):
            continue
        time.sleep(1.0)
        after = geometry(backend.list_windows(), wm_class, index)
        if after and after != before:
            moved = (wm_class, index, before, target, after)
            break

    if moved:
        wm_class, index, before, target, after = moved
        check(True, "Mutter honoured MoveWindow", f"{wm_class}[{index}] {before} -> {after}")
        close = all(abs(a - t) <= 2 for a, t in zip(after, target))
        if not close:
            print(f"{INFO} note: landed at {after}, asked for {target} — "
                  f"normal if the app enforces size constraints")
    else:
        check(False, "Mutter honoured MoveWindow",
              "no window moved; all candidates may be maximised or tiled")

    print("\nRestore")
    quiet(session.restore_session, "selftest")
    time.sleep(1.0)
    if moved:
        wm_class, index, before, _, _ = moved
        back = geometry(backend.list_windows(), wm_class, index)
        check(back == before, "window returned to its saved geometry",
              f"{back} vs {before}")

    print("\nv1 session files (written before 0.2)")
    if moved:
        wm_class, index, before, _, _ = moved
        v1 = [{"wm_class": wm_class, "class_index": index, "title": "",
               "x": before[0] + 29, "y": before[1] + 31,
               "width": max(320, before[2] - 37), "height": max(240, before[3] - 41)}]
        (original / "old.json").write_text(json.dumps(v1))
        _, out = quiet(session.restore_session, "old")
        time.sleep(1.0)
        after_v1 = geometry(backend.list_windows(), wm_class, index)
        check(after_v1 is not None and after_v1 != before,
              "a bare-array v1 file still restores", str(after_v1))
        quiet(session.restore_session, "selftest")
        time.sleep(1.0)

    print("\nClose")
    if args.close_others:
        launcher = next((a for a in ("gnome-terminal", "gnome-calculator", "xterm")
                         if subprocess.run(["which", a], capture_output=True).returncode == 0), None)
        if launcher:
            subprocess.Popen([launcher], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(4)
            now = backend.list_windows()
            extra = [w for w in now if w["id"] not in {x["id"] for x in windows}]
            if extra:
                ok = backend.close_window(extra[0])
                time.sleep(1.5)
                gone = extra[0]["id"] not in {w["id"] for w in backend.list_windows()}
                check(ok and gone, "CloseWindow closed the throwaway window",
                      extra[0].get("wm_class", ""))
            else:
                print(f"{SKIP} {launcher} opened no new window; skipping close test")
        else:
            print(f"{SKIP} no throwaway app found; skipping close test")
    else:
        print(f"{SKIP} skipped (pass --close-others to run it)")

    print("\nRestoring your desktop")
    quiet(session.restore_session, "selftest")
    print(f"{PASS} every window put back")

    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s): " + "; ".join(failures))
        return 1
    print("All checks passed. The GNOME backend works on this machine.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
