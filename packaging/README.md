# Packaging

Workman is distributed on the AUR via the top-level `PKGBUILD`. This directory
builds the **Debian/Ubuntu `.deb`** and **Fedora `.rpm`** packages.

## How the GNOME extension variant is selected

The extension ships in two JS-incompatible variants: `modern` (ESM, GNOME 45+)
and `legacy` (`imports.gi`, GNOME 42–44), both with UUID `workman@workman`. A
prebuilt binary package can't pick at build time the way the PKGBUILD does,
because the build host doesn't know the target's GNOME version. So the package
ships **both** variants under `/usr/share/workman/extension/{modern,legacy}/`
and selects on the target machine at install time:

- `scripts/after-install.sh` reads `gnome-shell --version` and copies the right
  variant into `/usr/share/gnome-shell/extensions/workman@workman/`. No
  gnome-shell present → defaults to `modern`.
- On Debian/Ubuntu a dpkg trigger on `/usr/bin/gnome-shell`
  (`--deb-interest-noawait`) re-runs the selection when GNOME itself is
  upgraded, so an in-place distro upgrade (e.g. 22.04 → 24.04, GNOME 42 → 46)
  flips `legacy` → `modern` automatically. Fedora only ever ships GNOME 45+, so
  the variant is always `modern` there and re-selection is moot.
- `scripts/after-remove.sh` removes the copied files on real uninstall.

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
version. The `pyxdg` dependency is satisfied by the distro package
(`python3-xdg` on Debian/Ubuntu, `python3-pyxdg` on Fedora).

## Release process

These are **Phase 1** (GitHub Releases) instructions. See the distribution
roadmap for the planned Phase 2 move to the openSUSE Build Service (OBS) for
auto-updating apt/dnf repositories.

1. Cut the release as usual (bump version, tag `vX.Y.Z`, push — see the
   top-level release flow). Build packages from the **tagged** tree:
   ```bash
   git checkout vX.Y.Z
   packaging/build-packages.sh
   ```
2. Attach both files in `dist-packages/` to the GitHub Release for that tag
   (e.g. `gh release upload vX.Y.Z dist-packages/*`).
3. Users then install the downloaded file:
   - **Ubuntu/Debian:** `sudo apt install ./workman_X.Y.Z_all.deb`
     (`apt install ./file.deb` resolves `python3-xdg` / recommends `gnome-shell`
     from the archive).
   - **Fedora:** `sudo dnf install ./workman-X.Y.Z-1.noarch.rpm`
     (pulls `python3-pyxdg`; recommends `gnome-shell`).

There is no auto-update at Phase 1 — users re-download newer releases. Phase 2
(OBS) is what adds `apt upgrade` / `dnf upgrade` support.
