# Workman

**Save and restore your desktop sessions on Linux.**

Workman remembers which apps you had open, where they were on screen, and how big they were — so you can pick up exactly where you left off.

---

## What it does

Ever closed your laptop at the end of the day and come back the next morning to a blank desktop? Workman solves that. With a single command you can save your entire desktop layout — every open app, its position, and its size — and restore it all later with another single command.

You can save as many named sessions as you like. Switch between a "work" layout, a "dev" layout, and a "music" layout instantly.

---

## Requirements

- **Linux** with GNOME on **Wayland**
- **GNOME Shell 42 or newer** (Ubuntu 22.04 LTS and up, Fedora 36+, Arch rolling)
- **Python 3.8+**

---

## Installation

Workman has two parts: a Python CLI and a small GNOME Shell extension. The
distribution packages below install **both** in one step and pick the correct
extension variant for your GNOME version automatically; installing from source
installs them separately.

After any method, you must **log out and back in** once (see
[Activate the extension](#activate-the-extension)).

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

# 2. Install the GNOME Shell extension into your user extensions dir.
# The script picks the modern (GNOME 45+) or legacy (GNOME 42-44) variant
# based on the running GNOME Shell.
./scripts/install-extension.sh
```

### Activate the extension

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
"save changes?" prompt. Windows that belong to the desktop or GNOME Shell itself
are never touched.

> **Note:** `--close-others` needs the updated GNOME Shell extension. If you
> installed Workman before this feature, reinstall the extension
> (`./scripts/install-extension.sh`) and log out and back in.

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

Workman uses a GNOME Shell extension to communicate with the desktop environment directly via DBus — the standard Linux inter-process communication system. This is the proper Wayland-native approach, meaning it works correctly on modern GNOME systems without relying on legacy X11 tools.

When you save a session, Workman:
1. Asks the GNOME extension for a list of all open windows
2. Records each window's app, position, size and title
3. Saves everything to a JSON file in `~/.local/share/workman/sessions/`

When you restore a session, Workman:
1. Reads the saved session file
2. Checks which of the required apps are already open
3. Launches only the apps that are missing (reusing the ones already running)
4. Optionally (with `--close-others`) closes any window that isn't part of the session
5. Waits for any newly-launched apps to open
6. Moves and resizes every window — reused and new — to its saved position

---

## Known limitations

- **Wayland only** — Workman is designed for GNOME on Wayland. X11 sessions are not supported.
- **GNOME only** — other desktop environments (KDE, XFCE etc.) are not currently supported.
- **App startup time** — some apps (like VS Code) take longer to load. Workman retries window positioning automatically to account for this.
- **Special windows** — dropdown terminals like Yakuake may not restore correctly due to how GNOME handles them.
- **Session files** — sessions are stored as plain JSON in `~/.local/share/workman/sessions/` and can be edited manually if needed.

---

## Roadmap

- Save and restore open browser tabs/websites as part of a session
- GUI for managing sessions
- Auto-save session on logout
- Support for multiple monitors
- KDE Plasma support

---

## Contributing

Contributions are welcome! Feel free to open an issue or submit a pull request on GitHub.

---

## License

MIT License — see LICENSE file for details.