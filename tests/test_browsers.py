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

from workman import browsers


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
    assert windows == [
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
        assert browsers.get_firefox_window_urls() == [["https://restored.example"]]
    finally:
        browsers.FIREFOX_DIR = saved


def test_is_firefox():
    assert browsers.is_firefox("firefox")
    assert browsers.is_firefox("org.mozilla.firefox")
    assert browsers.is_firefox("Firefox-esr")
    assert not browsers.is_firefox("google-chrome")
    assert not browsers.is_firefox(None)


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
