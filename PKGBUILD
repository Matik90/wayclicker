# Maintainer: Matik90 <https://github.com/Matik90>
#
# Build this file in an EMPTY directory, not inside the source repository:
# the repository has its own src/ folder, which collides with makepkg's
# $srcdir (makepkg -c would delete it). The guard below enforces that.
#   mkdir /tmp/wayclicker-build && cp PKGBUILD /tmp/wayclicker-build && cd /tmp/wayclicker-build
#   makepkg -si

if [[ -z "${BUILDDIR:-}" && -f "$PWD/src/clicker.py" && -f "$PWD/src/ui.py" ]]; then
    echo "==> ERROR: this PKGBUILD sits inside the Wayclicker source tree." >&2
    echo "==> Copy it to an empty directory and run makepkg there." >&2
    return 1 2>/dev/null || exit 1
fi

pkgname=wayclicker
pkgver=1.0.0
pkgrel=1
pkgdesc='Wayland-native autoclicker using evdev and uinput, with a PyQt6 interface'
arch=('any')
url='https://github.com/matik90/wayclicker'
license=('MIT')
depends=('python' 'python-evdev' 'python-pyqt6')
makedepends=('git')
source=("$pkgname::git+$url.git#tag=v$pkgver")
sha256sums=('SKIP')

check() {
    cd "$srcdir/$pkgname"
    python -m py_compile src/clicker.py src/main.py src/ui.py
}

package() {
    cd "$srcdir/$pkgname"

    # Application: same layout as the repository (src/, assets/, img/), so the
    # code finds its resources relative to itself in every install method.
    local share="$pkgdir/usr/share/$pkgname"
    install -Dm644 -t "$share/src" src/clicker.py src/main.py src/ui.py
    install -Dm644 -t "$share/assets" assets/style.qss assets/chevron-down.png
    install -Dm644 img/icon.png "$share/img/icon.png"

    install -d "$pkgdir/usr/bin"
    cat > "$pkgdir/usr/bin/$pkgname" <<EOF
#!/bin/sh
exec /usr/bin/python /usr/share/$pkgname/src/main.py "\$@"
EOF
    chmod 755 "$pkgdir/usr/bin/$pkgname"

    # Desktop integration
    install -Dm644 assets/wayclicker.desktop "$pkgdir/usr/share/applications/$pkgname.desktop"
    install -Dm644 img/icon.png "$pkgdir/usr/share/icons/hicolor/512x512/apps/$pkgname.png"

    # Kernel interfaces: /dev/uinput access for the "input" group, and loading
    # the uinput module at boot. The user still has to join the "input" group.
    install -Dm644 assets/60-wayclicker.rules "$pkgdir/usr/lib/udev/rules.d/60-$pkgname.rules"
    install -Dm644 assets/wayclicker-modules.conf "$pkgdir/usr/lib/modules-load.d/$pkgname.conf"

    install -Dm644 README.md -t "$pkgdir/usr/share/doc/$pkgname"
    install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname"

    python -m compileall -q -d "/usr/share/$pkgname/src" "$share/src"
}
