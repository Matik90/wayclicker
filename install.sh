# Usage:  ./install.sh [--no-deps] [--no-system] [--uninstall] [-y] [-h]

set -euo pipefail

APP="wayclicker"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_HOME/$APP"
VENV_DIR="$APP_DIR/venv"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/512x512/apps"

UDEV_RULE="/etc/udev/rules.d/60-wayclicker.rules"
MODULES_CONF="/etc/modules-load.d/wayclicker.conf"

ACTION="install"
INSTALL_DEPS=1
SETUP_SYSTEM=1
ASSUME_YES=0
RELOGIN_NEEDED=0

if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

info() { printf '%s==>%s %s\n' "$GREEN$BOLD" "$RESET" "$*"; }
warn() { printf '%swarning:%s %s\n' "$YELLOW$BOLD" "$RESET" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$RED$BOLD" "$RESET" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Wayclicker installer

Usage: ./install.sh [options]

Options:
  --no-deps      do not install system packages (python, compiler, Qt libraries)
  --no-system    do not touch kernel permissions (udev rule, uinput, input group)
  --uninstall    remove Wayclicker (asks before removing the system files)
  -y, --yes      answer yes to questions
  -h, --help     show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-deps)   INSTALL_DEPS=0 ;;
        --no-system) SETUP_SYSTEM=0 ;;
        --uninstall) ACTION="uninstall" ;;
        -y|--yes)    ASSUME_YES=1 ;;
        -h|--help)   usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

[[ $EUID -ne 0 ]] || die "Run this script as your normal user, not as root. It asks for sudo when needed."

as_root() {
    if command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    elif command -v doas >/dev/null 2>&1; then
        doas "$@"
    else
        die "sudo (or doas) is required to run: $*"
    fi
}

confirm() {
    local prompt="$1" reply
    [[ $ASSUME_YES -eq 1 ]] && return 0
    [[ -t 0 ]] || return 1
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

detect_package_manager() {
    local pm
    for pm in apt-get dnf zypper pacman; do
        if command -v "$pm" >/dev/null 2>&1; then
            echo "$pm"
            return
        fi
    done
    echo "unknown"
}

install_optional() {
    local -a cmd=()
    local pkg
    while [[ $# -gt 0 && "$1" != "--" ]]; do
        cmd+=("$1")
        shift
    done
    shift
    for pkg in "$@"; do
        as_root "${cmd[@]}" "$pkg" >/dev/null 2>&1 || warn "optional package not installed: $pkg"
    done
}

install_system_dependencies() {
    local pm
    pm="$(detect_package_manager)"
    info "Installing system prerequisites (package manager: $pm)"
    case "$pm" in
        apt-get)
            as_root apt-get update -qq || warn "apt-get update failed, continuing"
            as_root apt-get install -y python3 python3-venv python3-pip python3-dev \
                build-essential linux-libc-dev \
                || warn "could not install all base packages"
            install_optional apt-get install -y -- libxkbcommon0 libegl1 libgl1 \
                libfontconfig1 libdbus-1-3 libglib2.0-0 libxkbcommon-x11-0 libxcb-cursor0
            ;;
        dnf)
            as_root dnf install -y python3 python3-pip python3-devel gcc kernel-headers \
                || warn "could not install all base packages"
            install_optional dnf install -y -- libxkbcommon mesa-libEGL mesa-libGL \
                fontconfig dbus-libs libxkbcommon-x11 xcb-util-cursor
            ;;
        zypper)
            as_root zypper --non-interactive install python3 python3-pip python3-devel gcc \
                linux-glibc-devel \
                || warn "could not install all base packages"
            install_optional zypper --non-interactive install -- libxkbcommon0 libEGL1 \
                libGL1 fontconfig libdbus-1-3
            ;;
        pacman)
            warn "Arch detected: the PKGBUILD is the recommended way (see README)."
            as_root pacman -S --needed --noconfirm python python-pip base-devel linux-api-headers \
                || warn "could not install all base packages"
            ;;
        *)
            warn "No supported package manager found. Make sure Python 3.9+ (with venv and pip),"
            warn "a C compiler, the Python and Linux kernel headers and the Qt6 runtime libraries are installed."
            ;;
    esac
}

check_python() {
    command -v python3 >/dev/null 2>&1 || die "python3 was not found."
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
        || die "Python 3.9 or newer is required (found $(python3 --version 2>&1))."
}

install_app_files() {
    info "Installing application files to $APP_DIR"
    [[ -f "$SCRIPT_DIR/src/main.py" ]] || die "Run install.sh from the Wayclicker source directory."
    mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"
    rm -rf "$APP_DIR/app"
    mkdir -p "$APP_DIR/app"
    cp -r "$SCRIPT_DIR/src" "$SCRIPT_DIR/assets" "$SCRIPT_DIR/img" "$APP_DIR/app/"
    find "$APP_DIR/app" -name '__pycache__' -prune -exec rm -rf {} +
}

create_virtualenv() {
    info "Creating virtual environment in $VENV_DIR"
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR" \
        || die "Could not create a virtual environment (Debian/Ubuntu: install python3-venv)."
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip wheel \
        || warn "could not upgrade pip, continuing with the bundled version"
    info "Installing Python dependencies (evdev, PyQt6)"
    "$VENV_DIR/bin/python" -m pip install "evdev>=1.6.0" "PyQt6>=6.4" \
        || die "pip failed. evdev is compiled locally: check that gcc and the Python/kernel headers are installed."
}

install_launcher() {
    info "Creating launcher, desktop entry and icon"
    cat > "$BIN_DIR/$APP" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$APP_DIR/app/src/main.py" "\$@"
EOF
    chmod 755 "$BIN_DIR/$APP"

    sed "s|^Exec=.*|Exec=$BIN_DIR/$APP|" "$SCRIPT_DIR/assets/wayclicker.desktop" \
        > "$DESKTOP_DIR/wayclicker.desktop"
    install -m 644 "$SCRIPT_DIR/img/icon.png" "$ICON_DIR/$APP.png"
    refresh_desktop_caches
}

refresh_desktop_caches() {
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 \
        && gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
}

setup_system_permissions() {
    info "Configuring kernel input permissions (sudo required)"
    as_root install -Dm644 "$SCRIPT_DIR/assets/60-wayclicker.rules" "$UDEV_RULE"
    as_root install -Dm644 "$SCRIPT_DIR/assets/wayclicker-modules.conf" "$MODULES_CONF"
    as_root modprobe uinput || warn "could not load the uinput module (is it built into your kernel?)"
    as_root udevadm control --reload-rules || warn "udevadm reload failed"
    as_root udevadm trigger --name-match=uinput || true
    as_root udevadm settle || true

    if ! getent group input >/dev/null 2>&1; then
        info "Creating the missing 'input' group"
        as_root groupadd --system input
    fi

    local db_groups session_groups
    db_groups=" $(id -nG "$USER") "
    session_groups=" $(id -nG) "
    if [[ "$db_groups" != *" input "* ]]; then
        info "Adding $USER to the 'input' group"
        as_root usermod -aG input "$USER"
        RELOGIN_NEEDED=1
    elif [[ "$session_groups" != *" input "* ]]; then
        RELOGIN_NEEDED=1
    else
        info "$USER is already a member of the 'input' group"
    fi
}

print_summary() {
    echo
    info "Wayclicker is installed."
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) warn "$BIN_DIR is not in your PATH; add it to your shell profile or start the app from the application menu." ;;
    esac
    if [[ $RELOGIN_NEEDED -eq 1 ]]; then
        cat <<EOF

${BOLD}One more step:${RESET} the 'input' group only applies to new sessions.
Log out and back in (or reboot), then verify with:

    $APP --check

To try it right away without logging out:

    sg input -c $APP
EOF
    else
        printf '\nStart it from your application menu or run:  %s\nDiagnose problems with:  %s --check\n' "$APP" "$APP"
    fi
}

uninstall() {
    info "Removing Wayclicker from your home directory"
    rm -rf "$APP_DIR"
    rm -f "$BIN_DIR/$APP" "$DESKTOP_DIR/wayclicker.desktop" "$ICON_DIR/$APP.png"
    refresh_desktop_caches

    if [[ -e "$UDEV_RULE" || -e "$MODULES_CONF" ]]; then
        if confirm "Also remove the udev rule and module configuration (needs sudo)?"; then
            as_root rm -f "$UDEV_RULE" "$MODULES_CONF"
            as_root udevadm control --reload-rules || true
        else
            warn "System files kept: $UDEV_RULE $MODULES_CONF"
        fi
    fi
    info "Done. Your membership of the 'input' group was not changed"
    echo "    (remove it with: sudo gpasswd -d $USER input)"
}

main() {
    if [[ "$ACTION" == "uninstall" ]]; then
        uninstall
        return
    fi
    [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]] \
        || warn "This does not look like a Wayland session (XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset}); it will still work."
    [[ $INSTALL_DEPS -eq 1 ]] && install_system_dependencies
    check_python
    install_app_files
    create_virtualenv
    install_launcher
    [[ $SETUP_SYSTEM -eq 1 ]] && setup_system_permissions
    print_summary
}

main
