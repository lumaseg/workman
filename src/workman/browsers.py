"""Read open tabs/URLs from browsers' on-disk session stores.

Workman saves window geometry via the GNOME Shell extension; the URLs open in
a browser window are not exposed there, so we read the browser's own session
store from disk. This module is read-only and best-effort: any failure means we
simply don't capture URLs for that window, never that `save` fails.

Currently only Firefox is supported (Phase 2 of the websites feature). Firefox
keeps the live session in ``sessionstore-backups/recovery.jsonlz4`` (updated
while running) under the active profile, in the "mozLz4" container: an 8-byte
magic, a little-endian uint32 of the decompressed size, then a raw LZ4 block.
We decode it in pure Python so no extra runtime dependency has to be packaged
for every distro.
"""

import json
import struct
from pathlib import Path

MOZLZ4_MAGIC = b"mozLz40\0"

FIREFOX_DIR = Path.home() / ".mozilla" / "firefox"

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
    """Decode a mozLz4 session store and return open URLs grouped by window."""
    data = json.loads(_read_mozlz4_bytes(raw))
    windows = []
    for win in data.get("windows", []):
        urls = []
        for tab in win.get("tabs", []):
            entries = tab.get("entries", [])
            if not entries:
                continue
            # `index` is 1-based and points at the tab's currently shown entry;
            # fall back to the last entry if it's missing or out of range.
            idx = tab.get("index", len(entries))
            if not (1 <= idx <= len(entries)):
                idx = len(entries)
            url = entries[idx - 1].get("url", "")
            if url and _is_restorable(url):
                urls.append(url)
        if urls:
            windows.append(urls)
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


def get_firefox_window_urls():
    """Return open URLs grouped by Firefox window.

    The result is a list (one entry per open browser window) of URL lists (the
    current page of each tab in that window). Returns an empty list if Firefox
    isn't installed, has never run, or the store can't be read/parsed.
    """
    store = _active_sessionstore_file()
    if store is None:
        return []
    try:
        return _parse_sessionstore_bytes(store.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError):
        return []
