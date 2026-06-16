#!/usr/bin/env bash
#
# release.sh — cut a Workman release end to end, under the lumaseg pseudonym.
#
#   ./release.sh X.Y.Z ["one-line release notes"]
#
# Phases (each outward-facing step asks before acting; pass --yes to skip
# the prompts for an unattended run):
#   0. Anonymity + environment pre-flight  (HARD gate — aborts on any failure)
#   1. Bump version: pyproject.toml, PKGBUILD, metainfo.xml release entry
#   2. Commit, tag vX.Y.Z, push commit + tag to GitHub (lumaseg alias)
#   3. Fetch the tarball, pin its sha256 in PKGBUILD, commit + push
#   4. Build .deb/.rpm with fpm and attach to the GitHub Release
#   5. Update the AUR package (PKGBUILD + .SRCINFO) and push
#
# Flags:  --yes            assume "yes" to every confirmation
#         --skip-packages  skip phase 4 (.deb/.rpm build + GitHub Release)
#         --skip-aur       skip phase 5 (AUR push)
set -euo pipefail

# ---- pseudonymous-release constants (see the anonymity notes) --------------
ORIGIN_HOST="github-lumaseg"          # SSH alias that authenticates as lumaseg
ORIGIN_URL="git@github-lumaseg:lumaseg/workman.git"
REPO_SLUG="lumaseg/workman"
AUR_URL="ssh://aur@aur.archlinux.org/workman.git"
PSEUDO_NAME="lumaseg"
PSEUDO_EMAIL="lumaseg@proton.me"
META="data/com.github.lumaseg.workman.metainfo.xml"

# ---- arg parsing -----------------------------------------------------------
VERSION="" ; NOTES="" ; ASSUME_YES=0 ; SKIP_PACKAGES=0 ; SKIP_AUR=0
for a in "$@"; do
    case "$a" in
        --yes)           ASSUME_YES=1 ;;
        --skip-packages) SKIP_PACKAGES=1 ;;
        --skip-aur)      SKIP_AUR=1 ;;
        -*) echo "unknown flag: $a" >&2; exit 2 ;;
        *)  if [ -z "$VERSION" ]; then VERSION="$a"; else NOTES="$a"; fi ;;
    esac
done

# ---- helpers ---------------------------------------------------------------
bold=$(tput bold 2>/dev/null || true); reset=$(tput sgr0 2>/dev/null || true)
step() { echo; echo "${bold}== $* ==${reset}"; }
ok()   { echo "  ✓ $*"; }
die()  { echo "  ✗ $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"; }
confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    printf "  → %s [y/N] " "$1"; read -r r; [ "$r" = y ] || [ "$r" = Y ]
}

cd "$(git rev-parse --show-toplevel)"

[ -n "$VERSION" ] || die "usage: ./release.sh X.Y.Z [\"release notes\"]"
echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' || die "version must be X.Y.Z"
TAG="v$VERSION"
[ -n "$NOTES" ] || { printf "One-line release notes: "; read -r NOTES; }
[ -n "$NOTES" ] || die "release notes are required (used in metainfo + GitHub Release)"
DATE=$(date +%F)

# ============================================================================
step "Phase 0 — pre-flight (anonymity is a hard gate)"
need git; need curl; need awk; need sed
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || die "not on main"
[ -z "$(git status --porcelain)" ] || die "working tree is dirty — commit/stash first"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG already exists"
[ "$(git config --local user.name)"  = "$PSEUDO_NAME"  ] || die "local user.name is not $PSEUDO_NAME"
[ "$(git config --local user.email)" = "$PSEUDO_EMAIL" ] || die "local user.email is not $PSEUDO_EMAIL"
[ "$(git remote get-url origin)" = "$ORIGIN_URL" ] || die "origin is not the $ORIGIN_HOST alias (bare github.com authenticates as the personal LNof account!)"
if git config --global --get-regexp '^url\.' >/dev/null 2>&1; then
    die "a url.*.insteadOf rewrite exists in global git config — may inject a PAT; remove it"
fi
who=$(ssh -o BatchMode=yes -T "git@$ORIGIN_HOST" 2>&1 | head -1 || true)
echo "$who" | grep -q "Hi $PSEUDO_NAME" || die "ssh git@$ORIGIN_HOST does not greet $PSEUDO_NAME (got: $who)"
ok "on main, clean, identity + remote + SSH all resolve to $PSEUDO_NAME"

# ============================================================================
step "Phase 1 — bump version to $VERSION"
sed -i -E 's/^version = ".*"/version = "'"$VERSION"'"/' pyproject.toml
sed -i -E 's/^pkgver=.*/pkgver='"$VERSION"'/; s/^pkgrel=.*/pkgrel=1/' PKGBUILD
awk -v ver="$VERSION" -v date="$DATE" -v notes="$NOTES" '
    /<releases>/ && !done {
        print
        print "    <release version=\"" ver "\" date=\"" date "\">"
        print "      <description>"
        print "        <p>" notes "</p>"
        print "      </description>"
        print "    </release>"
        done=1; next
    }
    { print }
' "$META" > "$META.tmp" && mv "$META.tmp" "$META"
grep -q "version = \"$VERSION\"" pyproject.toml && grep -q "pkgver=$VERSION" PKGBUILD || die "bump failed"
ok "bumped pyproject.toml, PKGBUILD, $META"
git --no-pager diff --stat

# ============================================================================
step "Phase 2 — commit, tag, push"
confirm "commit, tag $TAG, and push to GitHub?" || die "aborted before push"
git add pyproject.toml PKGBUILD "$META"
git commit -m "Release $TAG: $NOTES"
git tag -a "$TAG" -m "Release $TAG"
git push origin main
git push origin "$TAG"
ok "pushed main + $TAG"

# ============================================================================
step "Phase 3 — pin tarball sha256 in PKGBUILD"
TARBALL="$(mktemp --suffix=.tar.gz)"
URL="https://github.com/$REPO_SLUG/archive/$TAG.tar.gz"
for i in 1 2 3 4 5; do
    curl -fsSL "$URL" -o "$TARBALL" && break
    echo "  …waiting for GitHub to generate the tarball ($i/5)"; sleep 4
done
[ -s "$TARBALL" ] || die "could not download $URL"
SHA=$(sha256sum "$TARBALL" | awk '{print $1}'); rm -f "$TARBALL"
ok "sha256 = $SHA"
sed -i -E "s/^sha256sums=\('.*'\)/sha256sums=('$SHA')/" PKGBUILD
git add PKGBUILD
git commit -m "Pin PKGBUILD to $TAG tarball"
git push origin main
ok "pushed sha256 pin"

# ============================================================================
if [ "$SKIP_PACKAGES" = 0 ]; then
    step "Phase 4 — build .deb/.rpm and attach to the GitHub Release"
    need fpm; need gh
    # gh uses API auth (NOT ssh) — make sure it is the lumaseg account, or we
    # would create the Release under the wrong identity.
    ghuser=$(gh api user --jq .login 2>/dev/null || true)
    [ "$ghuser" = "$PSEUDO_NAME" ] || die "gh is authenticated as '${ghuser:-none}', not $PSEUDO_NAME — run 'gh auth switch' (skip with --skip-packages)"
    if confirm "build packages and create GitHub Release $TAG?"; then
        packaging/build-packages.sh
        gh release create "$TAG" dist-packages/* --title "$TAG" --notes "$NOTES"
        ok "Release $TAG created with .deb + .rpm attached"
    else
        echo "  • skipped (build later with packaging/build-packages.sh)"
    fi
else
    step "Phase 4 — skipped (--skip-packages)"
fi

# ============================================================================
if [ "$SKIP_AUR" = 0 ]; then
    step "Phase 5 — update AUR"
    need makepkg
    if confirm "clone, update, and push the AUR package?"; then
        AURDIR="$(mktemp -d)/aur-workman"
        git clone "$AUR_URL" "$AURDIR"
        ( cd "$AURDIR"
          git config --local user.name  "$PSEUDO_NAME"
          git config --local user.email "$PSEUDO_EMAIL"
          cp "$OLDPWD/PKGBUILD" PKGBUILD
          makepkg --printsrcinfo > .SRCINFO
          git add PKGBUILD .SRCINFO
          git commit -m "Update to $VERSION"
          [ "$(git log -1 --format='%ae')" = "$PSEUDO_EMAIL" ] || { echo "AUR commit author is not $PSEUDO_EMAIL" >&2; exit 1; }
          git push origin master )
        rm -rf "$(dirname "$AURDIR")"
        ok "AUR pushed"
    else
        echo "  • skipped"
    fi
else
    step "Phase 5 — skipped (--skip-aur)"
fi

step "Release $TAG complete"
echo "  GitHub : https://github.com/$REPO_SLUG/releases/tag/$TAG"
echo "  AUR    : https://aur.archlinux.org/packages/workman"
