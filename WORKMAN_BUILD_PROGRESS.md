# Workman Build Progress

## Overview
Workman is a GNOME desktop session manager for Linux that saves and restores open application windows including their positions and sizes.

---

## Current Status: Ready for Flathub Submission

---

## Completed Milestones

### ✅ Core Functionality
- CLI tool with `save`, `restore`, `list` and `delete` commands
- Users can name their own sessions
- Sessions stored as JSON in `~/.local/share/workman/sessions/`
- Multiple sessions supported simultaneously

### ✅ GNOME Shell Extension
- Custom extension (`workman@workman`) built to expose window data via DBus
- Required because GNOME 49+ disabled the Shell Eval method for security
- Extension exposes two DBus methods:
  - `GetWindows` — returns all open windows with position and size data
  - `MoveWindow` — moves and resizes a window by wm_class and index
- Extension is Wayland-native, no X11 dependency

### ✅ Window Save
- Captures all open windows via GNOME Shell DBus
- Records title, wm_class, pid, x, y, width, height for each window
- Resolves executable path from PID via `/proc/{pid}/exe`
- Tracks class index to handle multiple windows of the same app (e.g. two Firefox windows)

### ✅ Window Restore
- Launches all apps from saved executables
- Waits for apps to fully open before moving windows
- Retry logic for slow-loading apps (e.g. VS Code)
- Moves and resizes each window to saved position using DBus extension
- Handles multiple windows of the same class using index tracking

### ✅ Known Issues Resolved
- Fixed: wmctrl and xdotool don't work on Wayland — replaced with GNOME Shell DBus extension
- Fixed: libwnck X11-only limitation — replaced with native Wayland approach
- Fixed: Multiple windows of same class only moving one — added class index tracking
- Fixed: VS Code not restoring correctly — added retry logic with configurable delay

### ✅ Documentation
- README.md written for Linux users
- Covers installation on Arch, Fedora and Ubuntu
- Includes usage examples and workflow
- Documents known limitations and roadmap

### ✅ Project Structure
```
workman/
├── src/
│   └── workman/
│       ├── __init__.py
│       ├── cli.py
│       └── session.py
├── com.github.lumaseg.workman.json   ← Flatpak manifest (in progress)
├── pyproject.toml
└── README.md
```

### ✅ GitHub Repository
- Project is on GitHub
- pyproject.toml configured with hatchling build system

---

## In Progress: Flatpak Packaging

### Goal
Package Workman as a Flatpak so it can be installed on any Linux distro without manual dependency management.

### Approach
Building all Python dependencies from source inside the Flatpak sandbox since the build environment has no internet access.

### Dependencies Bundled
| Package | Version | Status |
|---------|---------|--------|
| flit_core | 3.10.1 | ✅ Built |
| setuptools | 77.0.3 | ✅ Built |
| setuptools_scm | 8.1.0 | ✅ Built |
| pathspec | 1.1.0 | ✅ Built |
| pluggy | 1.6.0 | ✅ Built |
| packaging | 24.2 | ✅ Built |
| calver | 2025.10.20 | ✅ Built |
| trove-classifiers | 2026.1.14.14 | ✅ Built |
| hatchling | 1.29.0 | ✅ Built |
| pyxdg | 0.28 | ✅ Built |
| workman | 0.1.0 | ✅ Built |

### Build Notes
- Upgraded setuptools from 75.8.0 → 77.0.3 (calver requires >=77.0.1 for PEP 639 license support)
- Added calver (build dep for trove-classifiers)
- Added setuptools_scm (build dep for pluggy)
- Workman install uses `--no-build-isolation` so hatchling is found from the prior install step
- `flatpak run com.github.lumaseg.workman --help` confirms the app runs correctly

---

## Upcoming Tasks

- [x] Complete Flatpak build locally
- [x] Test Flatpak install and run
- [x] Create desktop entry file (`data/com.github.lumaseg.workman.desktop`)
- [x] Create AppStream metainfo file (`data/com.github.lumaseg.workman.metainfo.xml`)
- [ ] Tag release v0.1.0 on GitHub
- [ ] Submit to Flathub

### Flatpak Notes
- `finish-args` trimmed to the minimum needed: `--talk-name=org.workman.WindowManager`, `--talk-name=org.freedesktop.Flatpak`, `--filesystem=xdg-data`
- `restore` uses `flatpak-spawn --host <exe>` when running inside the sandbox (detected via `/.flatpak-info`) so host executables like `/usr/bin/firefox` can be launched correctly
- `appstreamcli compose` passes during build — `flatpak info` shows version and license resolved from metainfo

---

## Technical Notes

### Why a GNOME Shell Extension?
Wayland restricts direct window manipulation for security reasons. The proper way to interact with windows on GNOME Wayland is through the GNOME Shell itself via DBus. The Workman extension acts as a bridge between the CLI tool and the window manager.

### Why Not wmctrl/xdotool?
Both tools are X11-only and return no data on Wayland sessions. Early development used these tools but they were replaced entirely with the DBus extension approach.

### Session Storage Format
Sessions are stored as plain JSON files making them human-readable and editable:
```json
[
  {
    "title": "Visual Studio Code",
    "wm_class": "code",
    "pid": 1234,
    "x": 0,
    "y": 29,
    "width": 1920,
    "height": 1080,
    "class_index": 0,
    "exe": "/opt/visual-studio-code/code"
  }
]
```
