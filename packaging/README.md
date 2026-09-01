# Packaging

Workman is distributed on the AUR via the top-level `PKGBUILD`. This directory
builds the **Debian/Ubuntu `.deb`** and **Fedora `.rpm`** packages.

## The GNOME extension

The extension ships as a single ESM variant (GNOME Shell 45+) with UUID
`workman@workman`. The packages stage it under
`/usr/share/workman/extension/` and `scripts/after-install.sh` copies it into
`/usr/share/gnome-shell/extensions/workman@workman/` on install and upgrade;
`scripts/after-remove.sh` removes it on real uninstall.

Workman 0.1.x also shipped a `legacy` (`imports.gi`) variant for GNOME 42–44,
selected at install time, with a dpkg trigger to re-select it when GNOME itself
was upgraded. GNOME 42–44 support was dropped after Ubuntu 22.04 ceased to be a
target, so the variant, the selection logic and the trigger are all gone.

Sway needs no extension at all.

## Building

Requirements: `python3` with the `build` and `installer` modules, plus
[`fpm`](https://fpm.readthedocs.io) (`gem install --user-install fpm`). The
`.rpm` target additionally needs `rpmbuild` (Arch: `pacman -S rpm-tools`,
Debian/Ubuntu: `apt install rpm`, Fedora: `dnf install rpm-build`).

```bash
packaging/build-packages.sh          # both .deb and .rpm
packaging/build-packages.sh deb      # just the .deb
packaging/build-packages.sh rpm      # just the .rpm
```

Output lands in `dist-packages/` (git-ignored):
`workman_<ver>_all.deb` and `workman-<ver>-1.noarch.rpm`.

The packages are architecture-independent (pure Python + JS). The Python module
and its `.dist-info` install to a private `/usr/lib/workman/`; a
`/usr/bin/workman` launcher puts that on `PYTHONPATH`, so `import workman` and
`workman --version` work regardless of the target distro's Python minor
version. Workman has no third-party Python dependencies; each backend
talks to its compositor through a command already present on the system
(`gdbus` for GNOME, `swaymsg` for Sway).

## Release process

These are **Phase 1** (GitHub Releases) instructions. See the distribution
roadmap for the planned Phase 2 move to the openSUSE Build Service (OBS) for
auto-updating apt/dnf repositories.

The whole sequence — version bump, commit/tag/push, tarball-sha pin, package
build + GitHub Release, and the AUR update — is automated by the top-level
[`release.sh`](../release.sh), which also runs the lumaseg anonymity pre-flight
as a hard gate:

```bash
./release.sh X.Y.Z "one-line release notes"
```

It pauses for confirmation before each outward-facing step. To build packages
by hand instead (e.g. to inspect them first), run from the **tagged** tree:

```bash
git checkout vX.Y.Z
packaging/build-packages.sh
gh release upload vX.Y.Z dist-packages/*
```

Either way, users then install the downloaded file:
   - **Ubuntu/Debian:** `sudo apt install ./workman_X.Y.Z_all.deb`
     (`apt install ./file.deb` resolves the recommended `gnome-shell`
     from the archive).
   - **Fedora:** `sudo dnf install ./workman-X.Y.Z-1.noarch.rpm`
     (recommends `gnome-shell`).

There is no auto-update at Phase 1 — users re-download newer releases. Phase 2
(OBS) is what adds `apt upgrade` / `dnf upgrade` support.
