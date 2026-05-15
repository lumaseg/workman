# Maintainer: lumaseg
pkgname=workman
pkgver=0.1.0
pkgrel=1
pkgdesc="GNOME session manager — saves and restores open windows"
arch=('any')
url="https://github.com/lumaseg/workman"
license=('MIT')  
depends=('python' 'python-pyxdg')
makedepends=('python-hatchling' 'python-build' 'python-installer')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('SKIP')  # replace with real checksum

build() {
    cd "$pkgname-$pkgver"
    python -m build --wheel --no-isolation
}

package() {
    cd "$pkgname-$pkgver"
    python -m installer --destdir="$pkgdir" dist/*.whl

    # Pick the extension variant matching the user's GNOME Shell.
    # GNOME 45+ requires the ESM (modern) API; earlier versions use the legacy API.
    local variant="modern"
    if command -v gnome-shell >/dev/null 2>&1; then
        local major
        major=$(gnome-shell --version | awk '{print $3}' | cut -d. -f1)
        if [[ "$major" =~ ^[0-9]+$ ]] && (( major < 45 )); then
            variant="legacy"
        fi
    fi

    install -dm755 "$pkgdir/usr/share/gnome-shell/extensions/workman@workman"
    install -m644 "extension/$variant/extension.js" \
        "$pkgdir/usr/share/gnome-shell/extensions/workman@workman/extension.js"
    install -m644 "extension/$variant/metadata.json" \
        "$pkgdir/usr/share/gnome-shell/extensions/workman@workman/metadata.json"
}
