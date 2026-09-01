#!/usr/bin/env bash
# Build a Debian/Ubuntu .deb and a Fedora .rpm for Workman from one staging
# tree, using fpm. Produces architecture-independent packages (pure Python +
# JS) carrying the GNOME Shell extension, which after-install.sh copies into
# the live extension directory (see packaging/scripts/after-install.sh).
#
# Requirements:
#   - python3 with the `build` and `installer` modules
#   - fpm            (gem install --user-install fpm)
#   - rpmbuild       (only for the .rpm target; Fedora: dnf install rpm-build,
#                     Arch: pacman -S rpm-tools, Debian: apt install rpm)
#
# Usage:  packaging/build-packages.sh            # build both
#         packaging/build-packages.sh deb        # just the .deb
#         packaging/build-packages.sh rpm        # just the .rpm
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

TARGETS="${*:-deb rpm}"

VERSION="$(grep -E '^version = ' pyproject.toml | head -1 | sed -E 's/.*"(.*)".*/\1/')"
[ -n "$VERSION" ] || { echo "could not read version from pyproject.toml" >&2; exit 1; }
echo ">> building workman $VERSION packages: $TARGETS"

WORK="$(mktemp -d)"
STAGE="$WORK/stage"
trap 'rm -rf "$WORK"' EXIT

# 1. Build the wheel.
python3 -m build --wheel --outdir "$WORK/dist" >/dev/null

# 2. Install it into a temp tree, then relocate the package + its .dist-info to
#    a python-version-independent private dir. The launcher puts this on
#    PYTHONPATH, so `import workman` and `importlib.metadata` (for --version)
#    both work regardless of the target distro's python minor version.
python3 -m installer --destdir "$WORK/install" "$WORK"/dist/workman-*.whl
PKG_PARENT="$(dirname "$(find "$WORK/install" -type d -name workman -path '*site-packages*')")"
mkdir -p "$STAGE/usr/lib/workman"
cp -r "$PKG_PARENT/workman" "$PKG_PARENT"/workman-*.dist-info "$STAGE/usr/lib/workman/"
# Drop build-machine bytecode — it's compiled for this host's python version
# and useless (silently ignored) on the target's python.
find "$STAGE/usr/lib/workman" -name __pycache__ -type d -prune -exec rm -rf {} +

# 3. Distro-agnostic launcher.
mkdir -p "$STAGE/usr/bin"
cat > "$STAGE/usr/bin/workman" <<'EOF'
#!/bin/sh
export PYTHONPATH="/usr/lib/workman${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m workman.cli "$@"
EOF
chmod 755 "$STAGE/usr/bin/workman"

# 4. Stage the extension; after-install.sh copies it into place on the target.
mkdir -p "$STAGE/usr/share/workman/extension"
cp extension/extension.js extension/metadata.json \
   "$STAGE/usr/share/workman/extension/"

SCRIPTS="packaging/scripts"
COMMON=(
    -s dir
    -n workman
    -v "$VERSION"
    --license MIT
    --maintainer "lumaseg <lumaseg@proton.me>"
    --vendor "lumaseg"
    --url "https://github.com/lumaseg/workman"
    --description "Wayland session manager — save and restore open windows (GNOME, Sway)"
    --after-install "$SCRIPTS/after-install.sh"
    --after-remove  "$SCRIPTS/after-remove.sh"
)

OUTDIR="$REPO_ROOT/dist-packages"
# Start clean so stale artifacts from an earlier version can't linger — release.sh
# attaches the whole directory to the GitHub Release with `dist-packages/*`.
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

for t in $TARGETS; do
    case "$t" in
        deb)
            fpm "${COMMON[@]}" -t deb -a all -f \
                --depends python3 \
                --deb-recommends gnome-shell \
                -p "$OUTDIR/workman_${VERSION}_all.deb" \
                -C "$STAGE" usr
            ;;
        rpm)
            fpm "${COMMON[@]}" -t rpm -a noarch -f \
                --depends python3 \
                --rpm-tag "Recommends: gnome-shell" \
                -p "$OUTDIR/workman-${VERSION}-1.noarch.rpm" \
                -C "$STAGE" usr
            ;;
        *) echo "unknown target: $t" >&2; exit 1 ;;
    esac
done

echo ">> done:"
ls -1 "$OUTDIR"
