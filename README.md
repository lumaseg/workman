# Workman

**Save and restore your desktop sessions on Linux.**

Workman remembers which apps you had open, where they were on screen, and how big they were — so you can pick up exactly where you left off.

---

## What it does

Ever closed your laptop at the end of the day and come back the next morning to a blank desktop? Workman solves that. With a single command you can save your entire desktop layout — every open app, its position, and its size — and restore it all later with another single command.

You can save as many named sessions as you like. Switch between a "work" layout, a "dev" layout, and a "music" layout instantly.

---

## Requirements

- **Linux** on **Wayland**, with one of:
  - **GNOME Shell 45 or newer** (Ubuntu 24.04 LTS and up, Fedora 39+, Arch
    rolling) — plus the bundled GNOME Shell extension
  - **Sway** (or another i3-compatible wlroots compositor) — nothing extra to
    install; Workman talks to `swaymsg` directly
- **Python 3.8+**

Workman picks the backend by asking the compositor, not by reading
`XDG_CURRENT_DESKTOP` — so it works when Sway is started from a TTY and that
variable is never set.

---

## Installation

Workman has two parts: a Python CLI and a small GNOME Shell extension. The
distribution packages below install **both** in one step and pick the correct
extension variant for your GNOME version automatically; installing from source
installs them separately.

**On Sway the extension is irrelevant** — install the CLI and you're done.
The extension step and the log-out step below apply to GNOME only.

### Arch Linux (AUR)

```bash
yay -S workman      # or: paru -S workman
```

### Ubuntu / Debian

Download `workman_<version>_all.deb` from the
[latest release](https://github.com/lumaseg/workman/releases/latest), then:

```bash
sudo apt install ./workman_<version>_all.deb
```

### Fedora

Download `workman-<version>-1.noarch.rpm` from the
[latest release](https://github.com/lumaseg/workman/releases/latest), then:

```bash
sudo dnf install ./workman-<version>-1.noarch.rpm
```

### From source

```bash
git clone https://github.com/lumaseg/workman.git
cd workman

# 1. Install the CLI
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. GNOME only — install the Shell extension into your user extensions dir.
# Skip this entirely on Sway; there is nothing to install.
./scripts/install-extension.sh
```

### Activate the extension (GNOME only)

After installing, **log out and back in** so GNOME Shell loads the extension. (On Wayland you can't restart the shell in place.) Then verify:

```bash
gnome-extensions list --enabled | grep workman
```

If you don't see `workman@workman`, enable it manually:

```bash
gnome-extensions enable workman@workman
```

---

## Usage

### Save your current session
```bash
workman save mysession
```

Give your session any name you like — `work`, `dev`, `music`, `morning` — whatever makes sense to you.

#### Firefox tabs

When you save a session, Workman also records the tabs open in each **Firefox**
window (read from Firefox's own session store on disk). Restoring the session
reopens those tabs in a fresh Firefox window. This works out of the box — no
add-on and no extra dependency.

A couple of things worth knowing:

- Tabs are reopened only for Firefox windows that Workman **launches**. If
  Firefox is already running and the window gets reused, its current tabs are
  left as they are.
- Internal pages (`about:…`) are skipped, since they can't be reopened from the
  command line.

> **Privacy:** the URLs you have open are written, in clear text, into the
> session file under `~/.local/share/workman/sessions/`. Treat saved sessions as
> sensitive if your tabs are.

### Restore a session
```bash
workman restore mysession
```

Workman puts every app back exactly where it was. If some of the apps the
session needs are **already open**, Workman keeps them running and just moves
them into place — only the missing apps are launched. This makes restoring feel
like switching between layouts rather than rebuilding the desktop from scratch.
By default, apps that are open but aren't part of the session are left alone.

#### Clean switch — close everything else

To make the desktop match the session *exactly*, add `--close-others`:

```bash
workman restore mysession --close-others
```

Any window that isn't part of the session is closed (apps the session needs are
kept and repositioned as usual). Closing is graceful — it's the same as clicking
an app's close button, so anything with unsaved work still gets its
"save changes?" prompt. Windows that belong to the desktop or the shell itself
are never touched.

> **Note (GNOME):** `--close-others` needs the updated GNOME Shell extension. If
> you installed Workman before this feature, reinstall the extension
> (`./scripts/install-extension.sh`) and log out and back in.

#### See what a restore would do first

`--dry-run` prints every step — which apps would be launched and, on Sway, the
exact compositor commands that would rebuild the layout — without changing
anything:

```bash
workman restore mysession --dry-run
```

### List all saved sessions
```bash
workman list
```

### Delete a session
```bash
workman delete mysession
```

### Check the installed version
```bash
workman --version
```

---

## Example workflow

```bash
# Start your day — restore your work layout
workman restore work

# Switch to a music/relaxed layout
workman restore chill

# End of day — save where everything is
workman save work
```

---

## How it works

Workman speaks to the compositor natively rather than through legacy X11 tools
(`wmctrl`, `xdotool` and friends return nothing under Wayland). Which route it
takes depends on what's running:

- **GNOME** — a small Shell extension exposes the open windows over DBus, since
  GNOME offers no other Wayland-native way to move a window.
- **Sway** — `swaymsg` already exposes everything, so there's nothing to install.

When you save a session, Workman:
1. Asks the compositor for the open windows
2. Records each window's app, title, and how it is laid out (see below)
3. Reads Firefox's session store to record the tabs open in each Firefox window
4. Saves everything to a JSON file in `~/.local/share/workman/sessions/`

When you restore a session, Workman:
1. Reads the saved session file
2. Checks which of the required apps are already open
3. Launches only the apps that are missing (reusing the ones already running)
4. Optionally (with `--close-others`) closes any window that isn't part of the session
5. Waits for any newly-launched apps to open (Firefox windows it launches reopen their saved tabs)
6. Puts every window — reused and new — back where it was

### What "where it was" means

On **GNOME**, which is a floating window manager, a window is fully described
by its position and size, so that's what gets saved and replayed.

**Sway is a tiling compositor**, where geometry is a *result* of the layout
rather than an input to it — setting a tiled window's coordinates does nothing.
So on Sway, Workman saves the layout itself: which output each workspace lives
on, the workspace's split orientation, and how containers are nested inside it.
Restoring rebuilds that tree. Floating windows are the exception — those really
are described by their geometry, and are restored to their exact position and
size.

---

## Known limitations

- **Wayland only** — X11 sessions are not supported.
- **GNOME and Sway** — other desktops (KDE, XFCE) are not supported yet.
- **Sessions aren't portable between compositors** — a layout saved on GNOME
  can't be replayed on Sway, or vice versa. Workman says so rather than
  restoring something wrong.
- **One app, many windows** — several windows of the same app (three browser
  windows, say) are usually a single process, so relaunching it may open fewer
  windows than were saved. Workman warns at save time when a session contains
  apps it may not be able to tell apart on restore, and matches them by window
  title first and by order second — neither of which is guaranteed, since a
  browser's title changes with its tab.
- **App startup time** — some apps (like VS Code) take longer to load. On
  GNOME, Workman retries positioning each window. On Sway it waits five
  seconds after launching and then places whatever has appeared; an app slower
  than that is reported as `No window found for …` and left where it opened,
  and running `workman restore` a second time will place it.
- **Special windows** — dropdown terminals like Yakuake may not restore
  correctly due to how compositors handle them.
- **Session files** — sessions are stored as plain JSON in
  `~/.local/share/workman/sessions/` and can be edited manually if needed.

---

## Roadmap

- GUI for managing sessions
- Auto-save session on logout
- Hyprland and river support (both wlroots, so much of the Sway backend applies)
- KDE Plasma support

---

## Contributing

Contributions are welcome! Feel free to open an issue or submit a pull request on GitHub.

---

## License

MIT License — see LICENSE file for details.