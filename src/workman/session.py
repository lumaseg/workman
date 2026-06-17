import json
import os
import subprocess
import time
from pathlib import Path
from collections import defaultdict

IN_FLATPAK = os.path.exists("/.flatpak-info")

SESSIONS_DIR = Path.home() / ".local" / "share" / "workman" / "sessions"

EXTENSION_MISSING_MSG = (
    "The Workman GNOME Shell extension isn't running.\n"
    "Install it (see README), then run:\n"
    "    gnome-extensions enable workman@workman\n"
    "and log out and back in."
)


class WorkmanError(Exception):
    pass


def _check_supported_session():
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '')
    session_type = os.environ.get('XDG_SESSION_TYPE', '')
    is_gnome = 'GNOME' in desktop.upper()
    is_wayland = session_type.lower() == 'wayland' if session_type else True
    if is_gnome and is_wayland:
        return
    raise WorkmanError(
        "This version of Workman supports GNOME on Wayland only.\n"
        f"  Detected desktop: {desktop or 'unknown'}\n"
        f"  Detected session: {session_type or 'unknown'}\n"
        "Support for KDE, XFCE, and wlroots-based compositors is "
        "planned for future versions."
    )


def ensure_sessions_dir():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

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

def save_session(name):
    _check_supported_session()
    ensure_sessions_dir()
    windows = get_open_windows()
    
    class_counts = defaultdict(int)
    for window in windows:
        wm_class = window.get('wm_class', '')
        window['class_index'] = class_counts[wm_class]
        class_counts[wm_class] += 1
        pid = window.get('pid')
        if pid:
            window['exe'] = get_exe_from_pid(pid)
            flatpak_id = get_flatpak_id(pid)
            if flatpak_id:
                window['flatpak'] = flatpak_id

    session_file = SESSIONS_DIR / f"{name}.json"
    with open(session_file, 'w') as f:
        json.dump(windows, f, indent=2)
    print(f"Session '{name}' saved with {len(windows)} windows.")

def restore_session(name, close_others=False):
    _check_supported_session()
    session_file = SESSIONS_DIR / f"{name}.json"
    if not session_file.exists():
        print(f"Session '{name}' not found.")
        return
    with open(session_file, 'r') as f:
        windows = json.load(f)

    # Look at what's already on screen so we can reuse running apps instead of
    # relaunching them. This also fails fast with a clear message if the
    # extension isn't available (we'd be unable to move windows either way).
    current_windows = get_open_windows()
    open_counts = defaultdict(int)
    for window in current_windows:
        open_counts[window.get('wm_class', '')] += 1

    # Group the target windows by app. The first N instances of each app are
    # assumed to be covered by windows already open; only the remainder need
    # launching.
    target_by_class = defaultdict(list)
    for window in windows:
        target_by_class[window.get('wm_class', '')].append(window)

    # With --close-others, anything open that the session doesn't need is shut.
    # Keep the first N windows of each app (those get reused/repositioned) and
    # close the rest, plus every window of an app not in the session at all.
    # Windows without a wm_class (desktop/shell components) are never touched.
    if close_others:
        keep_remaining = {cls: len(wins) for cls, wins in target_by_class.items()}
        to_close = []
        for window in current_windows:
            wm_class = window.get('wm_class', '')
            if not wm_class:
                continue
            if keep_remaining.get(wm_class, 0) > 0:
                keep_remaining[wm_class] -= 1
            else:
                to_close.append(window)
        if to_close:
            print(f"Closing {len(to_close)} window(s) not in this session...")
            for window in to_close:
                window_id = window.get('id')
                label = window.get('wm_class') or window.get('title') or 'window'
                if window_id is None:
                    print(f"  Skipped {label} (update the extension to enable closing)")
                    continue
                if close_window(window_id):
                    print(f"  Closed {label}")
                else:
                    print(f"  Could not close {label}")

    print("Launching missing apps...")
    launched_any = False
    reused_any = False
    for wm_class, target_windows in target_by_class.items():
        already_open = open_counts.get(wm_class, 0)
        if already_open:
            reused = min(already_open, len(target_windows))
            print(f"  Reusing {reused} already-open {wm_class or 'window'}")
            reused_any = True
        for window in target_windows[already_open:]:
            flatpak_id = window.get('flatpak')
            exe = window.get('exe')
            # Flatpak apps must be relaunched via `flatpak run <id>`; their
            # saved exe is an in-sandbox path that doesn't exist on the host.
            if flatpak_id:
                cmd, label = ['flatpak', 'run', flatpak_id], f"flatpak run {flatpak_id}"
            elif exe:
                cmd, label = [exe], exe
            else:
                continue
            if IN_FLATPAK:
                cmd = ["flatpak-spawn", "--host", *cmd]
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                launched_any = True
                print(f"  Launched {label}")
            except Exception as e:
                print(f"  Could not launch {label}: {e}")

    if launched_any:
        print("Waiting for apps to open...")
        time.sleep(5)
    elif reused_any:
        print("All required apps already open; repositioning...")

    print("Restoring window positions...")
    for window in windows:
        wm_class = window.get('wm_class')
        index = window.get('class_index', 0)
        if not wm_class:
            continue
        success = move_window(
            wm_class,
            index,
            window['x'],
            window['y'],
            window['width'],
            window['height']
        )
        if success:
            print(f"  Moved {wm_class}[{index}] to {window['x']},{window['y']} {window['width']}x{window['height']}")
        else:
            print(f"  Could not move {wm_class}[{index}] after retries")

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
