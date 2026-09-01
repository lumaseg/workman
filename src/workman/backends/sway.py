"""Sway (and other wlroots/i3-compatible compositors) backend.

Sway needs no helper extension: everything is available over its IPC socket,
which ``swaymsg`` speaks. Shelling out to ``swaymsg`` rather than depending on
``i3ipc`` is deliberate — ``swaymsg`` is already a hard runtime requirement,
whereas a new Python dependency would have to be threaded through four
packaging systems (PKGBUILD, the .deb and .rpm fpm invocations, and the
Flatpak manifest's vendored offline wheel list).

The important difference from GNOME: Sway is a *tiling* compositor. A tiled
window's ``rect`` is derived from the layout tree, not an input to it, so
replaying saved x/y/w/h onto tiled windows accomplishes nothing. What has to
be saved and rebuilt is the tree: which output a workspace lives on, the
workspace's layout, and the nesting of split containers inside it. Only
floating windows are described by their geometry.
"""

import json
import os
import shutil
import subprocess
import time

from workman import appmatch
from workman.backends.base import Backend
from workman.errors import WorkmanError

SOCKET_MISSING_MSG = (
    "Sway's IPC socket isn't reachable ($SWAYSOCK is unset).\n"
    "Workman has to run inside the Sway session — a terminal that didn't\n"
    "inherit Sway's environment will silently see no windows."
)

# Sway parks the scratchpad in a synthetic output/workspace pair named __i3*.
# It is not a real place windows can be restored to.
_INTERNAL_PREFIX = "__i3"

IN_FLATPAK = os.path.exists("/.flatpak-info")

# Windows are parked here while their workspace is rebuilt from empty, then
# brought back in tree order. It should always end a restore empty; anything
# left behind means a restore was interrupted.
HOLDING_WORKSPACE = "workman-restoring"

# `split h`/`split v` wraps the focused container in a new split. Tabs and
# stacks are horizontal and vertical respectively; the `layout` command turns
# the wrapper into one afterwards.
_SPLIT_COMMAND = {
    "splith": "split h",
    "splitv": "split v",
    "tabbed": "split h",
    "stacked": "split v",
}

# The `layout` command's name for a workspace-level orientation.
_SPLIT_LAYOUT = {
    "splith": "splith",
    "splitv": "splitv",
    "tabbed": "splith",
    "stacked": "splitv",
}

# Sway REPORTS a stacked container as "stacked" in get_tree but only ACCEPTS
# "stacking" as a command argument; `layout stacked` is a parse error. Nothing
# in the tree output hints at the difference, so it fails silently.
_LAYOUT_COMMAND = {"tabbed": "tabbed", "stacked": "stacking"}

# How many times to re-check for windows that hadn't mapped yet, and how long
# to pause between tries.
_PLACE_ATTEMPTS = 3
_PLACE_RETRY_DELAY = 1.0

# Failed commands are summarised rather than listed in full; a broken restore
# usually fails the same way many times over.
_MAX_REPORTED_FAILURES = 5

# Which direction moves a container *into* the split wrapper sitting beside
# it. This follows the PARENT's orientation: in a splith parent the wrapper is
# to the left, in a splitv parent it is above.
_MOVE_INTO = {
    "splith": "left",
    "splitv": "up",
    "tabbed": "left",
    "stacked": "up",
}


def _quote(value):
    """Quote a value for Sway's command parser (workspace and output names)."""
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _overlaps(rect, output_rect):
    """True if a window rectangle lands at least partly on an output."""
    return (rect["x"] < output_rect["x"] + output_rect["width"]
            and rect["x"] + rect["width"] > output_rect["x"]
            and rect["y"] < output_rect["y"] + output_rect["height"]
            and rect["y"] + rect["height"] > output_rect["y"])


def _reachable_position(rect, outputs, preferred=None):
    """Where to actually put a floating window, given today's monitor layout.

    Sway will happily place a window at coordinates no output covers, leaving
    it invisible and impossible to reach with the mouse. Saved coordinates stop
    being valid whenever the monitor layout changes — a laptop undocked, a
    screen moved to the other side — so a position that lands nowhere is
    recentred on the workspace's own output instead of being replayed blindly.
    """
    if any(_overlaps(rect, output) for output in outputs.values()):
        return rect["x"], rect["y"]
    target = outputs.get(preferred) or next(iter(outputs.values()), None)
    if target is None:
        return rect["x"], rect["y"]
    return (target["x"] + max(0, (target["width"] - rect["width"]) // 2),
            target["y"] + max(0, (target["height"] - rect["height"]) // 2))


def _refs(entries):
    """Every window reference under a list of tree entries, in depth-first order."""
    found = []
    for entry in entries:
        if entry.get("kind") == "window":
            found.append(entry["ref"])
        elif entry.get("kind") == "split":
            found.extend(_refs(entry.get("children") or []))
    return found


def _swaymsg(*args, check=True):
    """Run swaymsg and return stdout.

    With ``check=False`` a non-zero exit is not an error and stdout is returned
    anyway: swaymsg exits non-zero when a *command* is rejected, but still
    prints the JSON reply explaining why. Callers that want to read that reason
    must not have the call raise out from under them.
    """
    cmd = ["swaymsg", *args]
    if IN_FLATPAK:
        # The sandbox has no route to /run/user/<uid>/sway-ipc.*.sock, so the
        # call has to happen on the host. This mirrors how session.py already
        # launches applications from inside a Flatpak.
        cmd = ["flatpak-spawn", "--host", *cmd]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and check:
        stderr = result.stderr.strip()
        if not os.environ.get("SWAYSOCK"):
            raise WorkmanError(SOCKET_MISSING_MSG)
        raise WorkmanError(f"swaymsg failed: {stderr or 'unknown error'}")
    return result.stdout


def _swaymsg_json(*args):
    raw = _swaymsg("-t", *args)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise WorkmanError(f"Could not parse swaymsg output: {e}")


def _command(*commands):
    """Issue Sway commands. Returns (succeeded, error text or None).

    Sway answers every command with a success flag and, on failure, an error
    string — but it exits 0 either way, so a rejected command is invisible
    unless the reply is read. `layout stacked` is the cautionary example: Sway
    reports containers as "stacked" yet only accepts "stacking", so every
    stacked restore failed silently until the reply was checked.
    """
    raw = _swaymsg(*commands, check=False)
    try:
        results = json.loads(raw)
    except json.JSONDecodeError:
        return False, "could not parse swaymsg reply"
    for result in results:
        if not result.get("success"):
            return False, (result.get("error") or "unknown error")
    return True, None


def _is_internal(name):
    return (name or "").startswith(_INTERNAL_PREFIX)


def _window_app(node):
    """The app identity of a container, or None if it isn't a window.

    ``app_id`` is the Wayland-native identifier and is null for XWayland
    windows, which report an X11 class instead.
    """
    app_id = node.get("app_id")
    if app_id:
        return app_id
    return (node.get("window_properties") or {}).get("class")


class SwayBackend(Backend):
    """Tree-shaped backend: layout is structure, not coordinates."""

    name = "sway"

    @staticmethod
    def is_available():
        if not os.environ.get("SWAYSOCK") or not shutil.which("swaymsg"):
            return False
        try:
            result = subprocess.run(
                ["swaymsg", "-t", "get_version"],
                capture_output=True, text=True,
            )
        except OSError:
            return False
        return result.returncode == 0

    # ---------------------------------------------------------------- capture

    def capture(self):
        tree = _swaymsg_json("get_tree")
        windows = []
        outputs = []
        workspaces = []

        for output_node in tree.get("nodes", []):
            if output_node.get("type") != "output":
                continue
            output_name = output_node.get("name") or ""
            if _is_internal(output_name):
                continue
            outputs.append({
                "name": output_name,
                "rect": output_node.get("rect"),
            })
            for ws_node in output_node.get("nodes", []):
                if ws_node.get("type") != "workspace":
                    continue
                if _is_internal(ws_node.get("name")):
                    continue
                workspaces.append(
                    self._capture_workspace(ws_node, output_name, windows)
                )

        return {"windows": windows, "outputs": outputs, "workspaces": workspaces}

    def _capture_workspace(self, ws_node, output_name, windows):
        """Record one workspace, appending its windows to the shared list.

        The tree stores integer references into ``windows`` rather than nested
        copies, so each window is described exactly once and session.py's
        annotations (exe, flatpak, urls) can't drift out of sync with the
        structural copy.
        """
        record = {
            "name": ws_node.get("name"),
            "num": ws_node.get("num"),
            "output": output_name,
            "layout": ws_node.get("layout"),
            "tree": self._capture_nodes(
                ws_node.get("nodes", []), ws_node.get("name"),
                output_name, windows, floating=False,
            ),
            "floating": self._capture_nodes(
                ws_node.get("floating_nodes", []), ws_node.get("name"),
                output_name, windows, floating=True,
            ),
        }
        return record

    def _capture_nodes(self, nodes, ws_name, output_name, windows, floating):
        """Convert a list of containers into serialisable tree entries."""
        entries = []
        for node in nodes:
            app = _window_app(node)
            children = node.get("nodes", [])
            if app is None and children:
                # A split container: no app of its own, only structure.
                entries.append({
                    "kind": "split",
                    "layout": node.get("layout"),
                    "children": self._capture_nodes(
                        children, ws_name, output_name, windows, floating
                    ),
                })
                continue
            if app is None and node.get("pid") is None:
                # Neither a window nor a populated split — nothing to restore.
                continue
            windows.append({
                "app_id": node.get("app_id"),
                "class": (node.get("window_properties") or {}).get("class"),
                "title": node.get("name"),
                "pid": node.get("pid"),
                "workspace": ws_name,
                "output": output_name,
                "floating": floating,
                # fullscreen_mode is also set on workspace nodes, so it is only
                # meaningful here, on a leaf.
                "fullscreen": bool(node.get("fullscreen_mode")),
                "rect": node.get("rect"),
                # `rect` is the content box, but `move`/`resize` address the
                # decorated container, so the title bar's height has to be
                # added back when replaying the geometry. Without it a
                # floating window lands one title bar lower every restore.
                "deco": (node.get("deco_rect") or {}).get("height") or 0,
            })
            entries.append({"kind": "window", "ref": len(windows) - 1})
        return entries

    def list_windows(self):
        """Flat list of what's on screen now, each carrying its live con_id."""
        tree = _swaymsg_json("get_tree")
        found = []
        self._collect(tree, found)
        return found

    def _collect(self, node, found, workspace=None, output=None):
        node_type = node.get("type")
        if node_type == "output":
            output = node.get("name")
        elif node_type == "workspace":
            workspace = node.get("name")
        if _is_internal(output) or _is_internal(workspace):
            return
        app = _window_app(node)
        if app is not None and node.get("pid") is not None:
            found.append({
                "con_id": node.get("id"),
                "app_id": node.get("app_id"),
                "class": (node.get("window_properties") or {}).get("class"),
                "title": node.get("name"),
                "pid": node.get("pid"),
                "workspace": workspace,
                "output": output,
            })
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            self._collect(child, found, workspace, output)

    def app_key(self, window):
        return window.get("app_id") or window.get("class") or ""

    def close_window(self, window):
        con_id = window.get("con_id")
        if con_id is None:
            return False
        succeeded, _ = _command(f"[con_id={con_id}] kill")
        return succeeded

    # ---------------------------------------------------------------- restore

    def place(self, payload, dry_run=False):
        """Rebuild the saved layout out of the windows currently on screen.

        The whole plan is computed up front as a list of Sway commands, so
        `--dry-run` prints exactly what a real run would execute.
        """
        workspaces = payload.get("workspaces") or []
        windows = payload.get("windows") or []
        if not workspaces:
            print("  Nothing to place.")
            return

        live = self.list_windows()
        matches = self._match_windows(windows, live)
        # An app that maps its window late (VS Code is the usual offender) is
        # absent from the first snapshot. Look again a few times rather than
        # reporting it missing on a restore that would have worked a second
        # later. Costs nothing when everything is already present.
        if not dry_run:
            for _ in range(_PLACE_ATTEMPTS - 1):
                if len(matches) >= len(windows):
                    break
                time.sleep(_PLACE_RETRY_DELAY)
                live = self.list_windows()
                matches = self._match_windows(windows, live)

        outputs = {o["name"]: o["rect"] for o in _swaymsg_json("get_outputs")
                   if o.get("name") and o.get("rect")}
        focused = self._focused_workspace()

        commands = []
        for ws in workspaces:
            commands.extend(
                self._workspace_commands(ws, windows, matches, live, outputs)
            )
        if focused:
            # Restoring a session shouldn't leave the user staring at whichever
            # workspace happened to be rebuilt last.
            commands.append(f"workspace {_quote(focused)}")

        if dry_run:
            for command in commands:
                print(f"  swaymsg {command}")
        else:
            failures = []
            for command in commands:
                succeeded, error = _command(command)
                if not succeeded:
                    failures.append((command, error))
            if failures:
                print(f"  {len(failures)} of {len(commands)} layout "
                      f"command(s) failed:")
                for command, error in failures[:_MAX_REPORTED_FAILURES]:
                    print(f"    {command}  ->  {error}")
                extra = len(failures) - _MAX_REPORTED_FAILURES
                if extra > 0:
                    print(f"    ...and {extra} more")
            else:
                print(f"  Applied {len(commands)} layout command(s).")

        for index, window in enumerate(windows):
            if index not in matches:
                print(f"  No window found for {self.app_key(window)} "
                      f"({window.get('title') or 'untitled'}) — skipped")

    def _match_windows(self, saved, live):
        """Map saved-window index -> live con_id.

        Windows sharing an app id are the normal case, not an edge case (three
        browser windows on one desktop is unremarkable), so matching happens in
        two passes: exact title first, because relaunched apps rarely map in
        the order they were saved, then remaining order, which is what
        `app_index` records. Both are best-effort — a browser title changes the
        moment its tab does.

        Which live windows are even considered comes from `appmatch`, so a
        session saved against Flatpak Firefox (`org.mozilla.firefox`) still
        restores onto a native `firefox`, matching the leniency the GNOME
        extension has had since v0.1.4.
        """
        matches = {}
        used = set()

        def candidates(window):
            """Unclaimed live windows for this app, tightest match tier first."""
            free = [w for w in live if w.get("con_id") not in used]
            return appmatch.best_matches(
                self.app_key(window), free, self.app_key
            )

        for index, window in enumerate(saved):
            title = window.get("title")
            if not title:
                continue
            for candidate in candidates(window):
                if candidate.get("title") == title:
                    matches[index] = candidate["con_id"]
                    used.add(candidate["con_id"])
                    break
        for index, window in enumerate(saved):
            if index in matches:
                continue
            pool = candidates(window)
            if pool:
                matches[index] = pool[0]["con_id"]
                used.add(pool[0]["con_id"])
        return matches

    def _focused_workspace(self):
        for ws in _swaymsg_json("get_workspaces"):
            if ws.get("focused"):
                return ws.get("name")
        return None

    def _workspace_commands(self, ws, windows, matches, live, outputs):
        name = ws.get("name")
        if name is None:
            return []
        tiled_refs = _refs(ws.get("tree") or [])
        floating_refs = _refs(ws.get("floating") or [])
        tiled = [matches[ref] for ref in tiled_refs if ref in matches]
        floating = [matches[ref] for ref in floating_refs if ref in matches]

        commands = []

        # Rebuilding a tree in place is unreliable, so empty the workspace of
        # the windows we're about to arrange and bring them back in tree order.
        for con_id in tiled + floating:
            commands.append(
                f"[con_id={con_id}] move container to workspace {_quote(HOLDING_WORKSPACE)}"
            )

        commands.append(f"workspace {_quote(name)}")
        output = ws.get("output")
        if output and output in outputs:
            commands.append(f"move workspace to output {_quote(output)}")

        # Setting the layout only lands on the workspace itself while it has no
        # children; with someone else's window still here it would restructure
        # whatever container that window sits in instead.
        ours = set(tiled) | set(floating)
        strangers = [
            w for w in live
            if w.get("workspace") == name and w.get("con_id") not in ours
        ]
        layout = ws.get("layout") or "splith"
        if strangers:
            print(f"  Workspace {name} has {len(strangers)} window(s) not in "
                  f"this session; leaving its layout alone.")
        else:
            commands.append(f"layout {_SPLIT_LAYOUT.get(layout, 'splith')}")

        # Flatten first: each window is inserted next to the previously focused
        # one, so moving them in tree order lays them out in the right sequence.
        for con_id in tiled:
            commands.append(
                f"[con_id={con_id}] move container to workspace {_quote(name)}"
            )
            commands.append(f"[con_id={con_id}] focus")

        commands.extend(self._nest_commands(ws.get("tree") or [], layout, matches))

        if layout in _LAYOUT_COMMAND and tiled and not strangers:
            commands.append(f"[con_id={tiled[0]}] focus")
            commands.append(f"layout {_LAYOUT_COMMAND[layout]}")

        for ref in floating_refs:
            con_id = matches.get(ref)
            if con_id is None:
                continue
            commands.append(
                f"[con_id={con_id}] move container to workspace {_quote(name)}"
            )
            commands.append(f"[con_id={con_id}] floating enable")
            rect = windows[ref].get("rect") or {}
            if rect and rect.get("width") is not None:
                # Undo the content-box/decorated-container difference described
                # in _capture_nodes, so a saved rect round-trips exactly.
                deco = windows[ref].get("deco") or 0
                commands.append(
                    f"[con_id={con_id}] resize set "
                    f"{rect['width']} {rect['height'] + deco}"
                )
                x, y = _reachable_position(rect, outputs, output)
                if (x, y) != (rect["x"], rect["y"]):
                    print(f"  {windows[ref].get('title') or 'A window'} was "
                          f"saved off the current monitor layout; recentred it")
                # Saved coordinates span all outputs, so they are absolute.
                commands.append(
                    f"[con_id={con_id}] move absolute position {x} {y - deco}"
                )

        for ref in tiled_refs + floating_refs:
            if windows[ref].get("fullscreen") and ref in matches:
                commands.append(f"[con_id={matches[ref]}] fullscreen enable")

        return commands

    def _nest_commands(self, entries, parent_layout, matches):
        """Rebuild split containers inside an already-flattened workspace.

        Verified against Sway 1.12: focusing a container and issuing `split v`
        wraps just that container in a new splitv, and `move left` (in a
        splith parent) then moves a sibling *into* that wrapper rather than
        past it. `move up` in a splith parent does something else entirely —
        it restructures the whole workspace — so the direction has to follow
        the parent's orientation.

        A split containing a single window is rebuilt too, even though it
        looks identical on screen. It is a *pending* split: the user pressed
        `split h` meaning "the next window opens beside this one", and
        dropping it silently changes where their next window lands.
        """
        commands = []
        for entry in entries:
            if entry.get("kind") != "split":
                continue
            saved = _refs([entry])
            leaves = [matches[ref] for ref in saved if ref in matches]
            if not leaves:
                continue
            if len(leaves) < 2 and len(saved) > 1:
                # The split held siblings when it was saved but only one of
                # them came back, so there is no structure left to rebuild.
                continue
            child_layout = entry.get("layout") or "splith"
            direction = _MOVE_INTO.get(parent_layout, "left")
            commands.append(f"[con_id={leaves[0]}] focus")
            commands.append(_SPLIT_COMMAND.get(child_layout, "split h"))
            for con_id in leaves[1:]:
                commands.append(f"[con_id={con_id}] focus")
                commands.append(f"move {direction}")
            if child_layout in _LAYOUT_COMMAND:
                commands.append(f"[con_id={leaves[0]}] focus")
                commands.append(f"layout {_LAYOUT_COMMAND[child_layout]}")
            commands.extend(
                self._nest_commands(
                    entry.get("children") or [], child_layout, matches
                )
            )
        return commands
