# Maintainer: lumaseg
pkgname=workman
pkgver=0.1.4
pkgrel=1
pkgdesc="Wayland session manager — save and restore open windows (GNOME, Sway)"
arch=('any')
url="https://github.com/lumaseg/workman"
license=('MIT')
depends=('python')
optdepends=('gnome-shell>=45: for the GNOME backend (also needs the bundled shell extension)'
            'sway: for the Sway backend (uses swaymsg; no extension needed)')
makedepends=('python-hatchling' 'python-build' 'python-installer')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('adf2bd4fbe9c03b60ce1a548a92649b3d3166b92a823153fb8a50f709c795506')

build() {
    cd "$pkgname-$pkgver"
    python -m build --wheel --no-isolation
}

package() {
    cd "$pkgname-$pkgver"
    python -m installer --destdir="$pkgdir" dist/*.whl

    install -dm755 "$pkgdir/usr/share/gnome-shell/extensions/workman@workman"
    install -m644 extension/extension.js \
        "$pkgdir/usr/share/gnome-shell/extensions/workman@workman/extension.js"
    install -m644 extension/metadata.json \
        "$pkgdir/usr/share/gnome-shell/extensions/workman@workman/metadata.json"
}
