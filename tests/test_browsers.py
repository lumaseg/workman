"""Tests for the Firefox session-store reader.

These run without Firefox installed. The mozLz4 decoder is exercised against
hand-built all-literal LZ4 blocks (always valid) and, when the optional `lz4`
library is present, a round-trip with real back-references.

Run: python -m pytest tests/  (or: python tests/test_browsers.py)
"""

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workman import browsers, session


def _mozlz4_all_literals(payload: bytes) -> bytes:
    """Wrap raw bytes as a mozLz4 file using literals-only LZ4 sequences.

    A token's high nibble holds literal length (15 = "read more length bytes,
    summed, until one < 255"). The whole payload is one literal sequence with no
    trailing match, which is a valid LZ4 block (only the last sequence may be
    literals-only, so there must be exactly one).
    """
    n = len(payload)
    block = bytearray()
    if n < 15:
        block.append(n << 4)
    else:
        block.append(15 << 4)
        rem = n - 15
        while rem >= 255:
            block.append(255)
            rem -= 255
        block.append(rem)
    block += payload
    return browsers.MOZLZ4_MAGIC + struct.pack("<I", n) + bytes(block)


def test_lz4_literals_short():
    payload = b"hello workman"
    out = browsers._read_mozlz4_bytes(_mozlz4_all_literals(payload))
    assert out == payload


def test_lz4_literals_long():
    payload = b"A" * 1000 + b"B" * 37  # forces extended literal-length encoding
    out = browsers._read_mozlz4_bytes(_mozlz4_all_literals(payload))
    assert out == payload


def test_lz4_roundtrip_with_matches():
    try:
        import lz4.block as lz4block
    except ImportError:
        return  # optional cross-check only
    payload = (b"the quick brown fox " * 50) + b"workman" * 20
    compressed = lz4block.compress(payload, store_size=False)
    moz = browsers.MOZLZ4_MAGIC + struct.pack("<I", len(payload)) + compressed
    assert browsers._read_mozlz4_bytes(moz) == payload


def test_url_extraction_from_sessionstore():
    sessionstore = {
        "windows": [
            {"tabs": [
                {"entries": [{"url": "https://a.example"},
                             {"url": "https://b.example"}], "index": 2},
                {"entries": [{"url": "about:newtab"}], "index": 1},
                {"entries": [{"url": "https://c.example"}], "index": 1},
            ]},
            {"tabs": [
                {"entries": [{"url": "file:///tmp/x.html"}], "index": 1},
            ]},
            {"tabs": [
                {"entries": [{"url": "about:blank"}], "index": 1},
            ]},
        ]
    }
    moz = _mozlz4_all_literals(json.dumps(sessionstore).encode())
    windows = browsers._parse_sessionstore_bytes(moz)
    # Window 1: current entry of tab 1 is b (index 2), about:newtab skipped, c kept.
    # Window 2: file:// kept. Window 3: only about:blank -> dropped entirely.
    assert [w["urls"] for w in windows] == [
        ["https://b.example", "https://c.example"],
        ["file:///tmp/x.html"],
    ]


def test_get_firefox_window_urls_on_disk(tmp_path=None):
    """End-to-end: profile discovery + mozLz4 read + parse from real files."""
    import tempfile
    base = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    sessionstore = {"windows": [{"tabs": [
        {"entries": [{"url": "https://restored.example"}], "index": 1},
    ]}]}
    # Two profiles; the newer recovery file should win.
    old = base / "aaaa.default" / "sessionstore-backups"
    new = base / "bbbb.default-release" / "sessionstore-backups"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "recovery.jsonlz4").write_bytes(
        _mozlz4_all_literals(json.dumps({"windows": []}).encode()))
    new_file = new / "recovery.jsonlz4"
    new_file.write_bytes(_mozlz4_all_literals(json.dumps(sessionstore).encode()))
    import os
    os.utime(old / "recovery.jsonlz4", (1000, 1000))
    os.utime(new_file, (2000, 2000))

    saved = browsers.FIREFOX_DIR
    browsers.FIREFOX_DIR = base
    try:
        assert browsers.get_firefox_windows() == [
            {"urls": ["https://restored.example"], "title": ""}]
    finally:
        browsers.FIREFOX_DIR = saved


def test_is_firefox():
    assert browsers.is_firefox("firefox")
    assert browsers.is_firefox("org.mozilla.firefox")
    assert browsers.is_firefox("Firefox-esr")
    assert not browsers.is_firefox("google-chrome")
    assert not browsers.is_firefox(None)


# ---------------------------------------------------------------------------
# Chromium / SNSS
# ---------------------------------------------------------------------------

def _pickle(*fields):
    """Build a Chromium Pickle: 4-byte payload length, then 4-byte-aligned fields.

    `fields` are ints, ("s", text) for a UTF-8 string, or ("s16", text) for a
    UTF-16 one.
    """
    body = b""
    for field in fields:
        if isinstance(field, int):
            body += struct.pack("<i", field)
        elif field[0] == "s":
            raw = field[1].encode("utf-8")
            body += struct.pack("<i", len(raw)) + raw + b"\0" * (-len(raw) % 4)
        else:
            raw = field[1].encode("utf-16-le")
            body += struct.pack("<i", len(field[1])) + raw + b"\0" * (-len(raw) % 4)
    return struct.pack("<I", len(body)) + body


def _snss(commands):
    """Frame (command_id, payload) pairs into an SNSS file."""
    out = b"SNSS" + struct.pack("<i", 3)
    for cid, payload in commands:
        out += struct.pack("<H", len(payload) + 1) + bytes([cid]) + payload
    return out


def _pair(a, b):
    return struct.pack("<ii", a, b)


def _navigation(tab, index, url, title):
    return (6, _pickle(tab, index, ("s", url), ("s16", title)))


def _basic_session():
    """Two windows: one with two ordered tabs, one with a single tab."""
    return _snss([
        (9, _pair(10, 0)), (9, _pair(20, 0)),          # both normal windows
        (0, _pair(10, 101)), (0, _pair(10, 102)),      # tabs -> window 10
        (0, _pair(20, 201)),                           # tab  -> window 20
        (2, _pair(101, 1)), (2, _pair(102, 0)),        # 102 sits before 101
        (2, _pair(201, 0)),
        _navigation(101, 0, "https://one.example", "One"),
        _navigation(102, 0, "https://two.example", "Two"),
        _navigation(201, 0, "https://three.example", "Three"),
        (7, _pair(101, 0)), (7, _pair(102, 0)), (7, _pair(201, 0)),
        (8, _pair(10, 0)), (8, _pair(20, 0)),          # showing tab index 0
    ])


def test_snss_groups_tabs_by_window_in_tab_order():
    windows = browsers._parse_snss(_basic_session())
    assert [w["urls"] for w in windows] == [
        ["https://two.example", "https://one.example"],
        ["https://three.example"],
    ]


def test_snss_reports_the_showing_tab_as_the_window_title():
    windows = browsers._parse_snss(_basic_session())
    assert [w["title"] for w in windows] == ["Two", "Three"]


def test_snss_follows_the_selected_navigation_entry():
    """A tab that navigated back shows the entry it is on, not the newest."""
    raw = _snss([
        (9, _pair(10, 0)), (0, _pair(10, 101)), (2, _pair(101, 0)),
        _navigation(101, 0, "https://old.example", "Old"),
        _navigation(101, 1, "https://new.example", "New"),
        (7, _pair(101, 0)),                            # went back to entry 0
        (8, _pair(10, 0)),
    ])
    assert browsers._parse_snss(raw)[0]["urls"] == ["https://old.example"]


def test_snss_drops_closed_tabs_and_windows():
    raw = _snss([
        (9, _pair(10, 0)), (9, _pair(20, 0)),
        (0, _pair(10, 101)), (0, _pair(10, 102)), (0, _pair(20, 201)),
        (2, _pair(101, 0)), (2, _pair(102, 1)), (2, _pair(201, 0)),
        _navigation(101, 0, "https://kept.example", "Kept"),
        _navigation(102, 0, "https://gone.example", "Gone"),
        _navigation(201, 0, "https://window-gone.example", "Window"),
        (16, _pair(102, 0)),                           # tab closed
        (17, _pair(20, 0)),                            # whole window closed
    ])
    windows = browsers._parse_snss(raw)
    assert [w["urls"] for w in windows] == [["https://kept.example"]]


def test_snss_ignores_popups_and_devtools_windows():
    raw = _snss([
        (9, _pair(10, 0)), (9, _pair(30, 1)),          # 30 is a popup
        (0, _pair(10, 101)), (0, _pair(30, 301)),
        (2, _pair(101, 0)), (2, _pair(301, 0)),
        _navigation(101, 0, "https://real.example", "Real"),
        _navigation(301, 0, "https://popup.example", "Popup"),
    ])
    assert [w["urls"] for w in browsers._parse_snss(raw)] == [["https://real.example"]]


def test_snss_skips_internal_pages():
    raw = _snss([
        (9, _pair(10, 0)), (0, _pair(10, 101)), (0, _pair(10, 102)),
        (2, _pair(101, 0)), (2, _pair(102, 1)),
        _navigation(101, 0, "https://real.example", "Real"),
        _navigation(102, 0, "chrome://settings", "Settings"),
    ])
    assert [w["urls"] for w in browsers._parse_snss(raw)] == [["https://real.example"]]


def test_snss_survives_a_truncated_tail():
    """Chromium appends as it goes, so the last command can be half-written."""
    raw = _basic_session()[:-9]
    windows = browsers._parse_snss(raw)
    assert windows and windows[0]["urls"]


def test_a_non_snss_file_is_rejected():
    try:
        browsers._parse_snss(b"NOTSNSS" + b"\0" * 32)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_is_chromium_recognises_the_family():
    for key in ("brave-browser", "Google-chrome", "chromium", "vivaldi-stable",
                "microsoft-edge"):
        assert browsers.is_chromium(key), key
    for key in ("firefox", "org.mozilla.firefox", "code", "foot", ""):
        assert not browsers.is_chromium(key), key


# ---------------------------------------------------------------------------
# Pairing compositor windows with the browser's own list
# ---------------------------------------------------------------------------

def test_browser_windows_are_matched_by_title_not_order():
    """The two orderings disagree routinely; order alone misassigns tabs."""
    compositor = [{"title": "Mail - Brave"}, {"title": "Music - Brave"}]
    browser = [{"urls": ["https://music"], "title": "Music"},
               {"urls": ["https://mail"], "title": "Mail"}]
    pairs = session._match_browser_windows(compositor, browser)
    assert pairs[0]["urls"] == ["https://mail"]
    assert pairs[1]["urls"] == ["https://music"]


def test_unmatched_browser_windows_fall_back_to_order():
    """A tab switched since the store was flushed will not match by title."""
    compositor = [{"title": "Something else entirely"}, {"title": "Music - Brave"}]
    browser = [{"urls": ["https://a"], "title": "Stale"},
               {"urls": ["https://b"], "title": "Music"}]
    pairs = session._match_browser_windows(compositor, browser)
    assert pairs[1]["urls"] == ["https://b"]     # matched on title
    assert pairs[0]["urls"] == ["https://a"]     # the leftover


def test_a_browser_window_is_never_claimed_twice():
    compositor = [{"title": "Music - Brave"}, {"title": "Music - Brave"}]
    browser = [{"urls": ["https://only"], "title": "Music"}]
    pairs = session._match_browser_windows(compositor, browser)
    assert len(pairs) == 1


# ---------------------------------------------------------------------------
# Relaunching with tabs
# ---------------------------------------------------------------------------

def test_chromium_takes_its_urls_positionally():
    """Verified against Brave: --new-window plus URLs opens ONE window."""
    window = {"exe": "/opt/brave-bin/brave",
              "urls": ["https://a.example", "https://b.example"]}
    cmd, label = session._launch_command(window, "brave-browser")
    assert cmd == ["/opt/brave-bin/brave", "--new-window",
                   "https://a.example", "https://b.example"]
    assert "2 tab(s)" in label


def test_firefox_flags_each_extra_tab():
    window = {"exe": "/usr/bin/firefox",
              "urls": ["https://a.example", "https://b.example"]}
    cmd, _ = session._launch_command(window, "firefox")
    assert cmd == ["/usr/bin/firefox", "--new-window", "https://a.example",
                   "--new-tab", "https://b.example"]


def test_a_non_browser_gets_no_url_arguments():
    window = {"exe": "/usr/bin/foot", "urls": ["https://a.example"]}
    cmd, _ = session._launch_command(window, "foot")
    assert cmd == ["/usr/bin/foot"]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
