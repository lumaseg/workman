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

def get_exe_from_pid(pid):
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except:
        return None

def save_session(name):
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

    session_file = SESSIONS_DIR / f"{name}.json"
    with open(session_file, 'w') as f:
        json.dump(windows, f, indent=2)
    print(f"Session '{name}' saved with {len(windows)} windows.")

def restore_session(name):
    session_file = SESSIONS_DIR / f"{name}.json"
    if not session_file.exists():
        print(f"Session '{name}' not found.")
        return
    with open(session_file, 'r') as f:
        windows = json.load(f)

    print("Launching apps...")
    launched_exes = set()
    for window in windows:
        exe = window.get('exe')
        if not exe:
            continue
        # Only launch each exe once per unique instance
        exe_key = f"{exe}_{window.get('class_index', 0)}"
        if exe_key in launched_exes:
            continue
        try:
            cmd = ["flatpak-spawn", "--host", exe] if IN_FLATPAK else [exe]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            launched_exes.add(exe_key)
            print(f"  Launched {exe}")
        except Exception as e:
            print(f"  Could not launch {exe}: {e}")

    print("Waiting for apps to open...")
    time.sleep(5)

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
