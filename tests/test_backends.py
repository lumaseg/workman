"""Tests for backend detection, the session-file shim, and the Sway backend.

These run anywhere: the compositor is replaced by a fixture tree, so nothing
here needs Sway or GNOME to be running. That matters because the machine this
backend was developed on has no GNOME Shell, and CI has neither.

Run: python -m pytest tests/  (or: python tests/test_backends.py)
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workman import appmatch, session
from workman.backends import gnome, sway
from workman.backends.sway import (
    SwayBackend, _quote, _reachable_position, _refs)
from workman.errors import WorkmanError


# A tree with one window per shape that has ever caused a bug:
#   - the scratchpad's synthetic __i3 output
#   - a workspace carrying fullscreen_mode=1 while holding no fullscreen window
#   - a tiled window whose `floating` field reads "auto_off" (truthy!)
#   - a genuinely floating window, reachable only via floating_nodes
#   - an XWayland window with app_id null and a window_properties class
#   - nested splits two levels deep
FIXTURE_TREE = {
    "type": "root", "name": "root",
    "nodes": [
        {
            "type": "output", "name": "__i3",
            "nodes": [{"type": "workspace", "name": "__i3_scratch",
                       "nodes": [{"type": "con", "name": "hidden",
                                  "app_id": "secret", "pid": 1,
                                  "rect": {}, "nodes": []}]}],
        },
        {
            "type": "output", "name": "DP-1",
            "rect": {"x": 0, "y": 0, "width": 3440, "height": 1440},
            "nodes": [{
                "type": "workspace", "name": "2", "num": 2,
                "layout": "splith",
                # Workspaces report fullscreen_mode=1; no window here is
                # actually fullscreen.
                "fullscreen_mode": 1,
                "nodes": [
                    {"type": "con", "name": "Editor", "app_id": "code",
                     "pid": 10, "floating": "auto_off", "fullscreen_mode": 0,
                     "rect": {"x": 0, "y": 0, "width": 2000, "height": 1400},
                     "deco_rect": {"height": 22}, "nodes": []},
                    {"type": "con", "name": None, "app_id": None,
                     "layout": "splitv", "rect": {}, "nodes": [
                        {"type": "con", "name": "Terminal", "app_id": "foot",
                         "pid": 11, "floating": "auto_off",
                         "fullscreen_mode": 0, "rect": {},
                         "deco_rect": {"height": 22}, "nodes": []},
                        # XWayland: no app_id, class only.
                        {"type": "con", "name": "Legacy",
                         "app_id": None, "pid": 12,
                         "window_properties": {"class": "XTerm"},
                         "fullscreen_mode": 2, "rect": {},
                         "deco_rect": {"height": 22}, "nodes": []},
                     ]},
                ],
                "floating_nodes": [
                    {"type": "floating_con", "name": "Popup",
                     "app_id": "blueman", "pid": 13, "fullscreen_mode": 0,
                     "rect": {"x": 100, "y": 222, "width": 300, "height": 178},
                     "deco_rect": {"height": 22}, "nodes": []},
                ],
            }],
        },
    ],
}


def _backend_with_fixture(monkeypatched=None):
    backend = SwayBackend()
    sway._swaymsg_json = lambda *args: FIXTURE_TREE
    return backend


def test_capture_skips_scratchpad():
    backend = _backend_with_fixture()
    payload = backend.capture()
    assert [o["name"] for o in payload["outputs"]] == ["DP-1"]
    assert [w["name"] for w in payload["workspaces"]] == ["2"]
    assert all(w["title"] != "hidden" for w in payload["windows"])


def test_capture_records_nested_structure():
    payload = _backend_with_fixture().capture()
    tree = payload["workspaces"][0]["tree"]
    assert tree[0]["kind"] == "window"
    assert tree[1]["kind"] == "split"
    assert tree[1]["layout"] == "splitv"
    assert [c["kind"] for c in tree[1]["children"]] == ["window", "window"]


def test_tiled_windows_are_not_marked_floating():
    """`floating: "auto_off"` is truthy — the field must not be trusted."""
    payload = _backend_with_fixture().capture()
    tiled = [w for w in payload["windows"] if w["title"] in ("Editor", "Terminal")]
    assert len(tiled) == 2
    assert all(w["floating"] is False for w in tiled)


def test_floating_window_comes_from_floating_nodes():
    payload = _backend_with_fixture().capture()
    popup = [w for w in payload["windows"] if w["title"] == "Popup"][0]
    assert popup["floating"] is True
    assert payload["workspaces"][0]["floating"] == [{"kind": "window", "ref": 3}]


def test_workspace_fullscreen_flag_does_not_leak_into_windows():
    """The workspace has fullscreen_mode=1; only the real leaf is fullscreen."""
    payload = _backend_with_fixture().capture()
    fullscreen = [w["title"] for w in payload["windows"] if w["fullscreen"]]
    assert fullscreen == ["Legacy"]


def test_xwayland_window_falls_back_to_class():
    backend = _backend_with_fixture()
    payload = backend.capture()
    legacy = [w for w in payload["windows"] if w["title"] == "Legacy"][0]
    assert legacy["app_id"] is None
    assert legacy["class"] == "XTerm"
    assert backend.app_key(legacy) == "XTerm"


def test_nest_commands_use_parent_orientation():
    """`move up` in a splith parent restructures the workspace; `move left` nests."""
    backend = SwayBackend()
    entries = [
        {"kind": "window", "ref": 0},
        {"kind": "split", "layout": "splitv", "children": [
            {"kind": "window", "ref": 1},
            {"kind": "window", "ref": 2},
        ]},
    ]
    commands = backend._nest_commands(entries, "splith", {0: 100, 1: 101, 2: 102})
    assert commands == [
        "[con_id=101] focus",
        "split v",
        "[con_id=102] focus",
        "move left",
    ]
    # A splitv workspace pulls siblings in from below instead.
    commands = backend._nest_commands(entries, "splitv", {0: 100, 1: 101, 2: 102})
    assert "move up" in commands


def test_pending_split_is_rebuilt():
    """A split holding one window looks identical but is not inert.

    It is a *pending* split: the next window opens inside it rather than
    beside its parent, so dropping it silently changes where the user's next
    window lands.
    """
    backend = SwayBackend()
    entries = [{"kind": "split", "layout": "splith",
                "children": [{"kind": "window", "ref": 0}]}]
    assert backend._nest_commands(entries, "splitv", {0: 100}) == [
        "[con_id=100] focus",
        "split h",
    ]


def test_split_is_skipped_when_its_siblings_are_missing():
    """Two windows were saved but only one came back: no structure to rebuild."""
    backend = SwayBackend()
    entries = [{"kind": "split", "layout": "splitv", "children": [
        {"kind": "window", "ref": 0}, {"kind": "window", "ref": 1}]}]
    assert backend._nest_commands(entries, "splith", {0: 100}) == []


def test_stacked_layout_uses_the_stacking_command():
    """Sway reports "stacked" but only accepts "stacking"; the mismatch is silent."""
    backend = SwayBackend()
    entries = [{"kind": "split", "layout": "stacked", "children": [
        {"kind": "window", "ref": 0}, {"kind": "window", "ref": 1}]}]
    commands = backend._nest_commands(entries, "splith", {0: 100, 1: 101})
    assert "layout stacking" in commands
    assert "layout stacked" not in commands


def test_tabbed_layout_keeps_its_command_name():
    backend = SwayBackend()
    entries = [{"kind": "split", "layout": "tabbed", "children": [
        {"kind": "window", "ref": 0}, {"kind": "window", "ref": 1}]}]
    assert "layout tabbed" in backend._nest_commands(entries, "splith", {0: 100, 1: 101})


def test_app_matching_tiers():
    """Exact wins; looser tiers exist for packaging variants of the same app."""
    assert appmatch.match_tier("firefox", "firefox") == appmatch.EXACT
    assert appmatch.match_tier("Firefox", "firefox") == appmatch.CASE_INSENSITIVE
    assert appmatch.match_tier("org.mozilla.firefox", "firefox") == appmatch.LAST_SEGMENT
    assert appmatch.match_tier("firefox-esr", "firefox") == appmatch.SUBSTRING
    assert appmatch.match_tier("code", "firefox") is None
    assert appmatch.match_tier("", "firefox") is None


def test_exact_match_beats_a_looser_one():
    """A window carrying the real id must never be displaced by a loose match."""
    pool = [{"a": "org.mozilla.firefox"}, {"a": "firefox"}]
    assert appmatch.best_matches("firefox", pool, lambda c: c["a"]) == [{"a": "firefox"}]


def test_flatpak_session_restores_onto_a_native_app():
    """Parity with the GNOME extension's tolerant wm_class matching."""
    backend = SwayBackend()
    saved = [{"app_id": "org.mozilla.firefox", "title": "Docs"}]
    live = [{"app_id": "firefox", "title": "Something else", "con_id": 5}]
    assert backend._match_windows(saved, live) == {0: 5}


def test_unrelated_apps_never_match():
    backend = SwayBackend()
    saved = [{"app_id": "code", "title": "main.py"}]
    live = [{"app_id": "firefox", "title": "main.py", "con_id": 5}]
    assert backend._match_windows(saved, live) == {}


def test_match_prefers_title_over_order():
    """Relaunched apps rarely map in the order they were saved."""
    backend = SwayBackend()
    saved = [{"app_id": "brave", "title": "Mail"},
             {"app_id": "brave", "title": "Docs"}]
    live = [{"app_id": "brave", "title": "Docs", "con_id": 7},
            {"app_id": "brave", "title": "Mail", "con_id": 8}]
    assert backend._match_windows(saved, live) == {0: 8, 1: 7}


def test_match_falls_back_to_order_when_titles_changed():
    backend = SwayBackend()
    saved = [{"app_id": "brave", "title": "Mail"},
             {"app_id": "brave", "title": "Docs"}]
    live = [{"app_id": "brave", "title": "Something else", "con_id": 7},
            {"app_id": "brave", "title": "Another", "con_id": 8}]
    assert backend._match_windows(saved, live) == {0: 7, 1: 8}


def test_refs_are_depth_first():
    entries = [{"kind": "window", "ref": 0},
               {"kind": "split", "layout": "splitv", "children": [
                   {"kind": "window", "ref": 1},
                   {"kind": "split", "layout": "splith", "children": [
                       {"kind": "window", "ref": 2}]}]}]
    assert _refs(entries) == [0, 1, 2]


OUTPUTS = {
    "DP-2": {"x": 0, "y": 0, "width": 1080, "height": 1920},
    "DP-1": {"x": 1080, "y": 0, "width": 3440, "height": 1440},
}


def test_valid_floating_position_is_left_alone():
    rect = {"x": 1500, "y": 300, "width": 500, "height": 350}
    assert _reachable_position(rect, OUTPUTS, "DP-1") == (1500, 300)


def test_floating_position_off_every_output_is_recentred():
    """A monitor unplugged between save and restore must not lose the window.

    Sway accepts coordinates no output covers and the window becomes invisible
    and unreachable, so a position that lands nowhere is recentred instead.
    """
    rect = {"x": 99999, "y": 99999, "width": 400, "height": 300}
    x, y = _reachable_position(rect, OUTPUTS, "DP-1")
    assert (x, y) == (1080 + (3440 - 400) // 2, (1440 - 300) // 2)


def test_recentring_falls_back_when_the_saved_output_is_gone():
    rect = {"x": 99999, "y": 99999, "width": 400, "height": 300}
    x, y = _reachable_position(rect, OUTPUTS, "DP-99-UNPLUGGED")
    assert any(
        o["x"] <= x < o["x"] + o["width"] and o["y"] <= y < o["y"] + o["height"]
        for o in OUTPUTS.values())


def test_partly_offscreen_window_is_still_considered_reachable():
    """Half off the edge is normal and deliberate; don't move it."""
    rect = {"x": -100, "y": 50, "width": 500, "height": 350}
    assert _reachable_position(rect, OUTPUTS, "DP-2") == (-100, 50)


def test_quote_escapes_names():
    assert _quote("2") == '"2"'
    assert _quote("my ws") == '"my ws"'
    assert _quote('a"b') == '"a\\"b"'


def test_command_reports_the_error_text():
    """A rejected Sway command must not be silently swallowed."""
    # swaymsg exits non-zero on a rejected command but still prints the reply,
    # so _command must ask for the output rather than let the call raise.
    sway._swaymsg = lambda *a, **kw: json.dumps(
        [{"success": False, "parse_error": True, "error": "Expected 'layout ...'"}])
    succeeded, error = sway._command("layout stacked")
    assert succeeded is False and "Expected" in error
    sway._swaymsg = lambda *a, **kw: json.dumps([{"success": True}])
    assert sway._command("layout stacking") == (True, None)


class _Process:
    """Stand-in for Popen: `alive` decides what poll() reports."""

    def __init__(self, alive):
        self.alive = alive

    def poll(self):
        return None if self.alive else 0


class _WaitBackend:
    """Reports a fixed window list, recording how often it was asked."""

    def __init__(self, windows):
        self.windows = windows
        self.polls = 0

    def list_windows(self):
        self.polls += 1
        return self.windows

    def app_key(self, window):
        return window["app_id"]


def test_wait_returns_immediately_once_windows_are_present():
    backend = _WaitBackend([{"app_id": "code"}])
    session._wait_for_windows(
        backend, {"code": [{}]}, {"code": [_Process(alive=True)]},
        timeout=5.0, interval=0.01)
    assert backend.polls == 1


def test_wait_gives_up_when_nothing_is_still_starting():
    """One process owning several windows must not stall the whole restore.

    Relaunching a browser hands off to the running instance and exits, so the
    extra windows are never coming; waiting the full timeout for them would
    make every such restore feel broken.
    """
    backend = _WaitBackend([{"app_id": "brave"}])
    started = time.monotonic()
    session._wait_for_windows(
        backend, {"brave": [{}, {}, {}]}, {"brave": [_Process(alive=False)]},
        timeout=30.0, interval=0.01)
    assert time.monotonic() - started < 1.0


def test_wait_keeps_waiting_while_a_process_is_alive():
    """A slow starter gets the full timeout, not five seconds."""
    backend = _WaitBackend([])
    started = time.monotonic()
    session._wait_for_windows(
        backend, {"code": [{}]}, {"code": [_Process(alive=True)]},
        timeout=0.3, interval=0.01)
    assert time.monotonic() - started >= 0.3


class _ReuseBackend:
    name = "sway"

    def __init__(self, live):
        self.live = live

    def list_windows(self):
        return self.live

    def app_key(self, window):
        return window.get("app_id") or ""


def test_reuse_counting_tolerates_packaging_variants():
    """A native window must satisfy a session that recorded the Flatpak id.

    Counting strictly here would undo placement's tolerance: the session would
    launch a duplicate and only then place one of the two.
    """
    from workman import appmatch as am
    live = [{"app_id": "firefox", "con_id": 1}]
    target_by_key = {"org.mozilla.firefox": [{}]}
    counts = {k: len(am.best_matches(k, live, lambda w: w["app_id"]))
              for k in target_by_key}
    assert counts == {"org.mozilla.firefox": 1}


class _FakeBackend:
    name = "sway"


def _write(tmp, payload):
    path = Path(tmp) / "s.json"
    path.write_text(json.dumps(payload))
    return path


def test_v1_bare_list_is_read_as_gnome():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [{"wm_class": "code", "x": 0, "y": 0,
                             "width": 1, "height": 1}])

        class Gnome:
            name = "gnome"

        payload = session._load_session(path, Gnome())
        assert payload["windows"][0]["wm_class"] == "code"


def test_v1_file_is_refused_on_a_different_backend():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, [{"wm_class": "code"}])
        try:
            session._load_session(path, _FakeBackend())
        except WorkmanError as e:
            assert "gnome" in str(e) and "sway" in str(e)
        else:
            raise AssertionError("expected a WorkmanError")


def test_v2_file_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, {"version": 2, "backend": "sway",
                            "data": {"windows": [{"app_id": "foot"}]}})
        payload = session._load_session(path, _FakeBackend())
        assert payload["windows"][0]["app_id"] == "foot"


def test_future_version_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, {"version": 99, "backend": "sway", "data": {}})
        try:
            session._load_session(path, _FakeBackend())
        except WorkmanError as e:
            assert "99" in str(e)
        else:
            raise AssertionError("expected a WorkmanError")


def test_gnome_place_reads_v1_class_index():
    """v1 files call the index `class_index`; v2 calls it `app_index`."""
    moved = []
    gnome.move_window = lambda *args: moved.append(args) or True
    backend = gnome.GnomeBackend()
    backend.place({"windows": [
        {"wm_class": "code", "class_index": 3,
         "x": 1, "y": 2, "width": 3, "height": 4},
    ]})
    assert moved == [("code", 3, 1, 2, 3, 4)]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
