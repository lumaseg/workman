"""Read open tabs/URLs from browsers' on-disk session stores.

Workman saves window geometry via the GNOME Shell extension; the URLs open in
a browser window are not exposed there, so we read the browser's own session
store from disk. This module is read-only and best-effort: any failure means we
simply don't capture URLs for that window, never that `save` fails.

Two browser families are supported, each with its own on-disk format, and both
are decoded in pure Python so no extra runtime dependency has to be packaged
for every distro.

**Firefox** keeps the live session in ``sessionstore-backups/recovery.jsonlz4``
(updated while running) under the active profile, in the "mozLz4" container: an
8-byte magic, a little-endian uint32 of the decompressed size, then a raw LZ4
block.

**Chromium-family browsers** (Brave, Chrome, Chromium, Vivaldi, Edge) keep
theirs in ``Sessions/Session_<timestamp>`` in Chromium's SNSS format: a
``SNSS`` magic and version, then a stream of length-prefixed commands that
replay the session as a log — tab created here, navigated there, closed later.
Reconstructing the open windows means replaying that log rather than reading a
snapshot.
"""

import json
import struct
from pathlib import Path

MOZLZ4_MAGIC = b"mozLz40\0"

FIREFOX_DIR = Path.home() / ".mozilla" / "firefox"

# Chromium-family browsers, keyed by a token that appears in the window's app
# id / wm_class. Order matters only in that a more specific token should not be
# shadowed by a looser one.
CHROMIUM_ROOTS = (
    ("brave", Path.home() / ".config" / "BraveSoftware" / "Brave-Browser"),
    ("vivaldi", Path.home() / ".config" / "vivaldi"),
    ("edge", Path.home() / ".config" / "microsoft-edge"),
    ("chromium", Path.home() / ".config" / "chromium"),
    ("chrome", Path.home() / ".config" / "google-chrome"),
)

SNSS_MAGIC = b"SNSS"

# SNSS command ids (Chromium's session_service_commands.cc). Two payload
# shapes exist: small fixed-size commands are raw structs, while variable-size
# ones are Chromium Pickles, which carry a 4-byte payload length of their own.
_CMD_SET_TAB_WINDOW = 0            # raw: window_id, tab_id
_CMD_SET_TAB_INDEX_IN_WINDOW = 2   # raw: tab_id, index
_CMD_UPDATE_TAB_NAVIGATION = 6     # pickle: tab_id, index, url, title, ...
_CMD_SET_SELECTED_NAVIGATION = 7   # raw: tab_id, index
_CMD_SET_SELECTED_TAB_IN_INDEX = 8 # raw: window_id, index of the showing tab
_CMD_SET_WINDOW_TYPE = 9           # raw: window_id, type (0 = normal window)
_CMD_TAB_CLOSED = 16               # raw: tab_id, ...
_CMD_WINDOW_CLOSED = 17            # raw: window_id, ...

_WINDOW_TYPE_NORMAL = 0

# Session-store files for the active profile, most-authoritative first.
# recovery.jsonlz4 is rewritten continuously while Firefox runs; recovery.baklz4
# is the previous copy; sessionstore.jsonlz4 only exists after a clean shutdown.
_SESSIONSTORE_RELPATHS = (
    "sessionstore-backups/recovery.jsonlz4",
    "sessionstore-backups/recovery.baklz4",
    "sessionstore.jsonlz4",
)


def is_firefox(wm_class):
    """True if a window's wm_class looks like a Firefox-family browser."""
    return "firefox" in (wm_class or "").lower()


def _lz4_decompress_block(src, expected_size=None):
    """Decompress a raw LZ4 block (not the LZ4 frame format).

    A block is a series of sequences: a token byte whose high nibble is the
    literal length and low nibble is the match length minus 4; lengths of 15
    are extended by summing following 0xFF-terminated bytes. Literals are
    copied verbatim; a match copies ``match_len`` bytes from ``offset`` bytes
    back in the output (the copy may overlap, so it is done byte by byte). The
    final sequence carries literals only and no match.
    """
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        token = src[i]
        i += 1

        lit_len = token >> 4
        if lit_len == 15:
            while True:
                b = src[i]
                i += 1
                lit_len += b
                if b != 0xFF:
                    break
        out += src[i:i + lit_len]
        i += lit_len

        # Last sequence ends on literals: no match follows.
        if i >= n:
            break

        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            raise ValueError("invalid LZ4 match offset 0")

        match_len = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while True:
                b = src[i]
                i += 1
                match_len += b
                if b != 0xFF:
                    break

        start = len(out) - offset
        if start < 0:
            raise ValueError("LZ4 match offset before start of output")
        for j in range(match_len):
            out.append(out[start + j])

    if expected_size is not None and len(out) != expected_size:
        raise ValueError(
            f"LZ4 size mismatch: got {len(out)}, expected {expected_size}"
        )
    return bytes(out)


def _read_mozlz4_bytes(raw):
    """Decompress the contents of a mozLz4 container given as bytes."""
    if raw[:8] != MOZLZ4_MAGIC:
        raise ValueError("not a mozLz4 buffer")
    expected_size = struct.unpack("<I", raw[8:12])[0]
    return _lz4_decompress_block(raw[12:], expected_size)


def _read_mozlz4(path):
    """Return the decompressed bytes of a Firefox mozLz4 file."""
    return _read_mozlz4_bytes(path.read_bytes())


def _parse_sessionstore_bytes(raw):
    """Decode a mozLz4 session store into the open windows it describes.

    Each entry is ``{"urls": [...], "title": "..."}``, matching the shape
    _parse_snss returns; the title is the tab on screen, which restore uses to
    tell one browser window from another.
    """
    data = json.loads(_read_mozlz4_bytes(raw))
    windows = []
    for win in data.get("windows", []):
        urls, titles = [], []
        for tab in win.get("tabs", []):
            entries = tab.get("entries", [])
            if not entries:
                continue
            # `index` is 1-based and points at the tab's currently shown entry;
            # fall back to the last entry if it's missing or out of range.
            idx = tab.get("index", len(entries))
            if not (1 <= idx <= len(entries)):
                idx = len(entries)
            entry = entries[idx - 1]
            url = entry.get("url", "")
            if url and _is_restorable(url):
                urls.append(url)
                titles.append(entry.get("title", "") or "")
        if urls:
            # `selected` is a 1-based index over the window's tabs.
            selected = win.get("selected", 1)
            title = titles[selected - 1] if 1 <= selected <= len(titles) else ""
            windows.append({"urls": urls, "title": title})
    return windows


def _active_sessionstore_file():
    """Pick the most recently written session-store file across all profiles.

    Newest mtime reliably points at the profile Firefox is actually using
    (recovery.jsonlz4 is rewritten constantly while running), which sidesteps
    parsing profiles.ini and its relative/absolute path quirks.
    """
    if not FIREFOX_DIR.is_dir():
        return None
    candidates = []
    for profile in FIREFOX_DIR.iterdir():
        if not profile.is_dir():
            continue
        for relpath in _SESSIONSTORE_RELPATHS:
            f = profile / relpath
            if f.exists():
                candidates.append(f)
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


def _is_restorable(url):
    """Skip internal pages (about:, chrome:) and blanks that can't be reopened."""
    return url.startswith(("http://", "https://", "file://", "ftp://"))


def get_firefox_windows():
    """Return the open Firefox windows.

    Each entry is ``{"urls": [...], "title": "..."}``. Returns an empty list if
    Firefox isn't installed, has never run, or the store can't be read.
    """
    store = _active_sessionstore_file()
    if store is None:
        return []
    try:
        return _parse_sessionstore_bytes(store.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def is_chromium(app_key):
    """True if a window's app id looks like a Chromium-family browser."""
    key = (app_key or "").lower()
    return any(token in key for token, _ in CHROMIUM_ROOTS)


def _chromium_root(app_key):
    """The config directory for whichever Chromium-family browser this is."""
    key = (app_key or "").lower()
    for token, root in CHROMIUM_ROOTS:
        if token in key and root.is_dir():
            return root
    return None


class _Pickle:
    """Reader for Chromium's Pickle: a 4-byte payload length, then fields.

    Ints are little-endian 32-bit. Strings are a length followed by that many
    bytes, padded out to the next 4-byte boundary.
    """

    def __init__(self, buf):
        if len(buf) < 4:
            raise ValueError("truncated pickle")
        (size,) = struct.unpack("<I", buf[:4])
        self._data = buf[4:4 + size]
        self._at = 0

    def read_int(self):
        if self._at + 4 > len(self._data):
            raise ValueError("truncated int")
        (value,) = struct.unpack("<i", self._data[self._at:self._at + 4])
        self._at += 4
        return value

    def read_string(self):
        length = self.read_int()
        if length < 0 or self._at + length > len(self._data):
            raise ValueError("truncated string")
        raw = self._data[self._at:self._at + length]
        self._at += length + (-length % 4)   # fields are 4-byte aligned
        return raw.decode("utf-8", "replace")

    def read_string16(self):
        """A UTF-16 string, whose length counts code units rather than bytes."""
        units = self.read_int()
        if units < 0 or self._at + 2 * units > len(self._data):
            raise ValueError("truncated string16")
        raw = self._data[self._at:self._at + 2 * units]
        self._at += 2 * units + (-(2 * units) % 4)
        return raw.decode("utf-16-le", "replace")


def _snss_commands(raw):
    """Yield (command_id, payload) for each command in an SNSS file."""
    if raw[:4] != SNSS_MAGIC:
        raise ValueError("not an SNSS file")
    at = 8                                    # magic + version
    while at + 2 <= len(raw):
        (length,) = struct.unpack("<H", raw[at:at + 2])
        at += 2
        if length == 0 or at + length > len(raw):
            break                             # truncated tail: stop, keep what we have
        yield raw[at], raw[at + 1:at + length]
        at += length


def _raw_pair(payload):
    """The two int32s carried by the small fixed-size commands."""
    if len(payload) < 8:
        raise ValueError("short payload")
    return struct.unpack("<ii", payload[:8])


def _parse_snss(raw):
    """Replay an SNSS command log into open URLs grouped by window.

    The file is a log, not a snapshot: a tab appears, is assigned to a window,
    navigates repeatedly, is reordered, and may later be closed. Later commands
    supersede earlier ones for the same tab, so the state is built by replaying
    in order and letting the last value win.
    """
    tab_window = {}          # tab id -> window id
    tab_order = {}           # tab id -> position within its window
    tab_selected = {}        # tab id -> which navigation entry is showing
    tab_pages = {}           # tab id -> {navigation index: (url, title)}
    window_type = {}         # window id -> Chromium window type
    window_active = {}       # window id -> index of the tab on screen
    closed_tabs = set()
    closed_windows = set()

    for command, payload in _snss_commands(raw):
        try:
            if command == _CMD_SET_TAB_WINDOW:
                window, tab = _raw_pair(payload)
                tab_window[tab] = window
            elif command == _CMD_SET_TAB_INDEX_IN_WINDOW:
                tab, index = _raw_pair(payload)
                tab_order[tab] = index
            elif command == _CMD_SET_SELECTED_NAVIGATION:
                tab, index = _raw_pair(payload)
                tab_selected[tab] = index
            elif command == _CMD_SET_SELECTED_TAB_IN_INDEX:
                window, index = _raw_pair(payload)
                window_active[window] = index
            elif command == _CMD_SET_WINDOW_TYPE:
                window, kind = _raw_pair(payload)
                window_type[window] = kind
            elif command == _CMD_TAB_CLOSED:
                closed_tabs.add(_raw_pair(payload)[0])
            elif command == _CMD_WINDOW_CLOSED:
                closed_windows.add(_raw_pair(payload)[0])
            elif command == _CMD_UPDATE_TAB_NAVIGATION:
                pickle = _Pickle(payload)
                tab = pickle.read_int()
                index = pickle.read_int()
                url = pickle.read_string()
                # The title follows the URL and lets restore tell one browser
                # window from another; a failure here must not cost us the URL.
                try:
                    title = pickle.read_string16()
                except (ValueError, struct.error):
                    title = ""
                tab_pages.setdefault(tab, {})[index] = (url, title)
        except (ValueError, struct.error):
            # One malformed command shouldn't cost us the whole session; the
            # format varies a little across Chromium versions.
            continue

    windows = {}
    for tab, window in tab_window.items():
        if tab in closed_tabs or window in closed_windows:
            continue
        # Popups, devtools and app windows aren't restorable browser windows.
        if window_type.get(window, _WINDOW_TYPE_NORMAL) != _WINDOW_TYPE_NORMAL:
            continue
        navigations = tab_pages.get(tab)
        if not navigations:
            continue
        # The selected entry is the page actually showing; without one, the
        # furthest-forward navigation is the best guess.
        wanted = tab_selected.get(tab)
        if wanted not in navigations:
            wanted = max(navigations)
        url, title = navigations[wanted]
        if _is_restorable(url):
            windows.setdefault(window, []).append(
                (tab_order.get(tab, 0), url, title))

    result = []
    for window, tabs in sorted(windows.items()):
        tabs.sort()
        active = window_active.get(window, 0)
        title = next((t for position, _, t in tabs if position == active), "")
        result.append({"urls": [u for _, u, _ in tabs], "title": title})
    return result


def _active_chromium_session_file(root):
    """The most recently written session log across this browser's profiles."""
    candidates = []
    for profile in root.iterdir():
        sessions = profile / "Sessions"
        if not sessions.is_dir():
            continue
        candidates.extend(f for f in sessions.glob("Session_*") if f.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


def get_chromium_windows(app_key):
    """Return the open windows of a Chromium-family browser.

    Each entry is ``{"urls": [...], "title": "..."}`` — the title being the tab
    on screen, which is what the compositor shows in the window title and so
    what restore can match against. Best-effort: any failure yields an empty
    list rather than an error.
    """
    root = _chromium_root(app_key)
    if root is None:
        return []
    store = _active_chromium_session_file(root)
    if store is None:
        return []
    try:
        return _parse_snss(store.read_bytes())
    except (OSError, ValueError, struct.error):
        return []


def get_browser_windows(app_key):
    """Open windows of whichever browser this app key names, or []."""
    if is_firefox(app_key):
        return get_firefox_windows()
    if is_chromium(app_key):
        return get_chromium_windows(app_key)
    return []


def is_browser(app_key):
    return is_firefox(app_key) or is_chromium(app_key)
