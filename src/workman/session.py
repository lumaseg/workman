"""Session orchestration.

Everything here is compositor-independent. Resolving an executable from a pid,
relaunching Flatpaks, reopening a browser's tabs, and deciding which apps to
reuse rather than launch work identically everywhere; only enumerating,
placing and closing windows differ, and those live behind the `Backend`
interface in `workman.backends`.
"""

import json
import os
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from workman import appmatch, browsers
from workman.backends import detect
from workman.errors import UnsupportedDesktopError, WorkmanError  # noqa: F401

IN_FLATPAK = os.path.exists("/.flatpak-info")

SESSIONS_DIR = Path.home() / ".local" / "share" / "workman" / "sessions"

# v1 was a bare JSON array of GNOME windows. v2 wraps the payload so the
# backend that captured it is known before anything is replayed.
SESSION_VERSION = 2

# How long to wait for a launched app to map its window, and how often to look.
# Generous because slow starters (VS Code, JetBrains IDEs) routinely exceed the
# five-second sleep this replaced; the wait exits as soon as it is satisfied.
WAIT_TIMEOUT = 20.0
WAIT_INTERVAL = 0.4


def ensure_sessions_dir():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def get_exe_from_pid(pid):
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except:
        return None


def get_flatpak_id(pid):
    """Return the Flatpak application id for a process, or None.

    A Flatpak app's /proc/<pid>/exe resolves to its in-sandbox path
    (e.g. /app/extra/.../spotify), which does not exist on the host and so
    cannot be relaunched directly. The sandbox exposes its app id in
    /proc/<pid>/root/.flatpak-info, which we use to relaunch via
    `flatpak run <id>` instead.
    """
    try:
        with open(f"/proc/{pid}/root/.flatpak-info") as f:
            for line in f:
                if line.startswith("name="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


def _match_browser_windows(compositor_windows, browser_windows):
    """Pair the compositor's browser windows with the browser's own window list.

    Order alone is not enough: the compositor enumerates windows in stacking or
    tree order while the browser lists them in its own internal order, and on a
    three-window browser those disagree routinely. A window's title is the tab
    currently showing, so the browser's title is a substring of the compositor's
    ("Spotify - Web Player" inside "Spotify - Web Player - Brave"). Match on
    that first, then fall back to order for whatever is left — a tab switched
    since the browser last flushed its session store will not match by title.
    """
    pairs = {}
    claimed = set()
    for index, window in enumerate(compositor_windows):
        title = (window.get('title') or '').strip()
        if not title:
            continue
        for position, candidate in enumerate(browser_windows):
            if position in claimed:
                continue
            candidate_title = (candidate.get('title') or '').strip()
            if candidate_title and candidate_title in title:
                pairs[index] = candidate
                claimed.add(position)
                break
    spare = [w for position, w in enumerate(browser_windows)
             if position not in claimed]
    for index in range(len(compositor_windows)):
        if index not in pairs and spare:
            pairs[index] = spare.pop(0)
    return pairs


def _attach_browser_urls(windows, app_key):
    """Record each browser window's open tabs so restore can reopen them.

    Best-effort and read-only: any failure leaves windows without a `urls` key
    and restore simply launches them without tabs. Sessions saved before this
    feature have no `urls` key and stay valid.
    """
    by_browser = defaultdict(list)
    for window in windows:
        key = app_key(window)
        if browsers.is_browser(key):
            by_browser[key].append(window)

    for key, compositor_windows in by_browser.items():
        try:
            browser_windows = browsers.get_browser_windows(key)
        except Exception:
            browser_windows = []
        if not browser_windows:
            continue
        pairs = _match_browser_windows(compositor_windows, browser_windows)
        captured = 0
        for index, browser_window in pairs.items():
            urls = browser_window.get('urls') or []
            if urls:
                compositor_windows[index]['urls'] = urls
                captured += len(urls)
        if captured:
            print(f"Captured {captured} {key} tab(s) across {len(pairs)} window(s).")


def save_session(name):
    backend = detect()
    ensure_sessions_dir()
    payload = backend.capture()
    windows = payload.get('windows', [])

    # The Nth window of a given app, in enumeration order. This is what lets
    # restore tell two windows of the same app apart when nothing else can.
    app_counts = defaultdict(int)
    for window in windows:
        key = backend.app_key(window)
        window['app_index'] = app_counts[key]
        app_counts[key] += 1
        pid = window.get('pid')
        if pid:
            window['exe'] = get_exe_from_pid(pid)
            flatpak_id = get_flatpak_id(pid)
            if flatpak_id:
                window['flatpak'] = flatpak_id

    _attach_browser_urls(windows, backend.app_key)
    _warn_about_ambiguity(app_counts)

    session_file = SESSIONS_DIR / f"{name}.json"
    with open(session_file, 'w') as f:
        json.dump({
            'version': SESSION_VERSION,
            'backend': backend.name,
            'created': datetime.now().astimezone().isoformat(timespec='seconds'),
            'data': payload,
        }, f, indent=2)
    print(f"Session '{name}' saved with {len(windows)} windows "
          f"({backend.name} backend).")


def _warn_about_ambiguity(app_counts):
    """Flag apps with several windows, which restore can only place approximately.

    Two windows of the same app are told apart by title first and enumeration
    order second. Titles change (switching a browser tab rewrites one), and a
    single process often owns many windows, so a relaunch may produce fewer
    windows than were saved. Saying so at save time beats a silently wrong
    restore later.
    """
    ambiguous = sorted(key for key, count in app_counts.items() if count > 1 and key)
    if ambiguous:
        print("Note: multiple windows share an app id (" +
              ", ".join(ambiguous) + ").")
        print("      Restore matches them by title, then by order — which can "
              "differ if titles changed.")


def _load_session(path, backend):
    """Read a session file, normalising v1 files and rejecting foreign ones."""
    with open(path, 'r') as f:
        raw = json.load(f)

    if isinstance(raw, list):
        # v1: a bare array of GNOME windows, written before session files
        # recorded which backend produced them.
        version, saved_backend, payload = 1, 'gnome', {'windows': raw}
    else:
        version = raw.get('version', 1)
        saved_backend = raw.get('backend', 'gnome')
        payload = raw.get('data') or {}

    if version > SESSION_VERSION:
        raise WorkmanError(
            f"This session file is version {version}, but this Workman "
            f"understands up to version {SESSION_VERSION}. Upgrade Workman."
        )
    if saved_backend != backend.name:
        raise WorkmanError(
            f"Session '{path.stem}' was captured on the '{saved_backend}' "
            f"backend, but this machine is running '{backend.name}'.\n"
            "Layouts aren't portable between compositors — save a new session "
            "here instead."
        )
    return payload


def _close_others(backend, current_windows, target_by_key, dry_run):
    """Close anything open that this session doesn't need.

    Keep the first N windows of each app the session wants (those get reused
    and repositioned) and close the rest, plus every window of an app the
    session doesn't mention. Windows without an app key belong to the desktop
    or the shell itself and are never touched.
    """
    keep_remaining = {key: len(wins) for key, wins in target_by_key.items()}
    to_close = []
    for window in current_windows:
        key = backend.app_key(window)
        if not key:
            continue
        # Which app in the session does this window satisfy? Tightest match
        # wins, so an exactly-named window is never consumed on behalf of a
        # loosely-matching one. Counting strictly here would close a window the
        # session is about to reuse under a different packaging id.
        claimed, best_tier = None, None
        for target_key, remaining in keep_remaining.items():
            if remaining <= 0:
                continue
            tier = appmatch.match_tier(key, target_key)
            if tier is not None and (best_tier is None or tier < best_tier):
                claimed, best_tier = target_key, tier
        if claimed is not None:
            keep_remaining[claimed] -= 1
        else:
            to_close.append(window)
    if not to_close:
        return
    print(f"Closing {len(to_close)} window(s) not in this session...")
    for window in to_close:
        label = backend.app_key(window) or window.get('title') or 'window'
        if dry_run:
            print(f"  Would close {label}")
            continue
        if backend.close_window(window):
            print(f"  Closed {label}")
        else:
            print(f"  Could not close {label}")


def _launch_command(window, app_key):
    """Build the command that reopens one window, or None if it can't be."""
    flatpak_id = window.get('flatpak')
    exe = window.get('exe')
    # Flatpak apps must be relaunched via `flatpak run <id>`; their saved exe
    # is an in-sandbox path that doesn't exist on the host.
    if flatpak_id:
        cmd, label = ['flatpak', 'run', flatpak_id], f"flatpak run {flatpak_id}"
    elif exe:
        cmd, label = [exe], exe
    else:
        return None, None
    # Reopen a browser window's saved tabs. Only windows we launch get their
    # tabs back; reused already-open windows keep what they have.
    urls = window.get('urls')
    if urls and browsers.is_firefox(app_key):
        # Firefox wants each extra tab flagged individually.
        cmd = cmd + ['--new-window', urls[0]]
        for url in urls[1:]:
            cmd += ['--new-tab', url]
        label += f" (+{len(urls)} tab(s))"
    elif urls and browsers.is_chromium(app_key):
        # Chromium takes them positionally: one new window, the rest as tabs
        # in it. Verified against Brave, which opens a single window.
        cmd = cmd + ['--new-window'] + list(urls)
        label += f" (+{len(urls)} tab(s))"
    if IN_FLATPAK:
        cmd = ["flatpak-spawn", "--host", *cmd]
    return cmd, label


def _launch_missing(backend, target_by_key, open_counts, dry_run):
    """Launch only the apps that aren't already running.

    Returns ``{app_key: [Popen, ...]}`` for what was actually started, which
    lets the caller tell "still starting up" from "will never open another
    window" while waiting.
    """
    print("Launching missing apps...")
    launched = defaultdict(list)
    reused_any = False
    for key, target_windows in target_by_key.items():
        already_open = open_counts.get(key, 0)
        if already_open:
            reused = min(already_open, len(target_windows))
            print(f"  Reusing {reused} already-open {key or 'window'}")
            reused_any = True
        for window in target_windows[already_open:]:
            cmd, label = _launch_command(window, key)
            if cmd is None:
                continue
            if dry_run:
                print(f"  Would launch {label}")
                continue
            try:
                launched[key].append(subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                print(f"  Launched {label}")
            except Exception as e:
                print(f"  Could not launch {label}: {e}")

    if not launched and reused_any:
        print("All required apps already open; repositioning...")
    return launched


def _wait_for_windows(backend, target_by_key, launched,
                      timeout=WAIT_TIMEOUT, interval=WAIT_INTERVAL):
    """Wait for the apps just launched to put their windows on screen.

    Polling beats the fixed five-second sleep this replaces in both directions:
    a session of quick apps stops waiting the moment they appear, and a slow
    starter like VS Code gets far longer than five seconds instead of being
    reported missing on a restore that would have worked a moment later.

    An app stops being waited on once every process launched for it has already
    exited without adding a window. That is the normal outcome when one process
    owns several windows — relaunching a browser hands off to the running
    instance and quits — and without it every such session would stall for the
    full timeout.
    """
    print("Waiting for apps to open...")
    deadline = time.monotonic() + timeout
    pending = set(launched)
    while pending:
        counts = defaultdict(int)
        for window in backend.list_windows():
            counts[backend.app_key(window)] += 1
        for key in list(pending):
            if counts.get(key, 0) >= len(target_by_key.get(key, ())):
                pending.discard(key)
            elif all(process.poll() is not None for process in launched[key]):
                pending.discard(key)
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(interval)


def restore_session(name, close_others=False, dry_run=False):
    backend = detect()
    session_file = SESSIONS_DIR / f"{name}.json"
    if not session_file.exists():
        print(f"Session '{name}' not found.")
        return
    payload = _load_session(session_file, backend)
    windows = payload.get('windows', [])

    # Look at what's already on screen so we can reuse running apps instead of
    # relaunching them. This also fails fast with a clear message if the
    # compositor is unreachable (we'd be unable to move windows either way).
    current_windows = backend.list_windows()

    # Group the target windows by app. The first N instances of each app are
    # assumed to be covered by windows already open; only the remainder need
    # launching.
    target_by_key = defaultdict(list)
    for window in windows:
        target_by_key[backend.app_key(window)].append(window)

    # Count what's already open per app, tolerating packaging-variant
    # differences exactly as placement does. Counting strictly here would undo
    # that tolerance: a session recording `org.mozilla.firefox` would see zero
    # native `firefox` windows open, launch a duplicate, and only then place
    # one of them.
    open_counts = {
        key: len(appmatch.best_matches(key, current_windows, backend.app_key))
        for key in target_by_key
    }

    if close_others:
        _close_others(backend, current_windows, target_by_key, dry_run)

    launched = _launch_missing(backend, target_by_key, open_counts, dry_run)
    if launched:
        _wait_for_windows(backend, target_by_key, launched)

    print("Restoring window positions...")
    backend.place(payload, dry_run=dry_run)

    if dry_run:
        print("Dry run complete — nothing was changed.")
    else:
        print(f"Session '{name}' restored.")


def list_sessions():
    ensure_sessions_dir()
    sessions = list(SESSIONS_DIR.glob("*.json"))
    if not sessions:
        print("No sessions saved yet.")
        return
    print("Saved sessions:")
    for session in sessions:
        print(f"  - {session.stem}")


def delete_session(name):
    session_file = SESSIONS_DIR / f"{name}.json"
    if not session_file.exists():
        print(f"Session '{name}' not found.")
        return
    session_file.unlink()
    print(f"Session '{name}' deleted.")
