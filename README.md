# Wayclicker

A small, modern autoclicker for **Wayland** (KDE Plasma, Niri, Hyprland, Sway, GNOME and friends).

Wayland deliberately stops applications from listening to global keys or faking input for other
windows, so classic X11 autoclickers (`xdotool` and similar) do not work. Wayclicker goes below the
display server and talks to the Linux kernel directly:

| Job                              | Kernel interface | Python         |
|----------------------------------|------------------|----------------|
| Detect the global hotkey         | `evdev`          | `python-evdev` |
| Inject mouse clicks / key presses| `uinput`         | `python-evdev` |

That is why it works in every compositor, in full-screen games and with the window minimised.
The interface is written with PyQt6, which runs **natively on Wayland**. This matters for
compositors such as Niri that ship without built-in XWayland.

## Features

- Global start/stop hotkey (default `F4`), customisable by typing or with the **Capture** button
- Click a **mouse button** (LMB or RMB) or spam any **keyboard key**
- Single or double click
- Delay in hours, minutes, seconds and milliseconds (down to 1 ms)
- Repeat until stopped, or a fixed number of repetitions
- Live `IDLE` / `RUNNING` status badge
- Clicker runs in its own worker thread, so the window never freezes
- Clean popups with fix instructions when kernel permissions are missing
- `wayclicker --check` diagnoses permission problems from a terminal

## Contents

1. [Installation](#installation)
2. [Wayland prerequisites: permissions](#wayland-prerequisites-permissions)
3. [Usage](#usage)
4. [Troubleshooting](#troubleshooting)
5. [Developer guide](#developer-guide): run locally, test, Git, GitHub, `makepkg`, AUR
6. [How it works](#how-it-works)
7. [License](#license)

---

## Installation

### Arch Linux and derivatives (AUR)

```bash
yay -S wayclicker
```

The package installs the app, a desktop entry, the udev rule and the `uinput` module-load file.
You still have to join the `input` group once, see [permissions](#wayland-prerequisites-permissions).

Without an AUR helper:

```bash
git clone https://aur.archlinux.org/wayclicker.git
cd wayclicker
makepkg -si
```

### Other distributions (Debian, Ubuntu, Fedora, openSUSE, ...)

```bash
git clone https://github.com/matik90/wayclicker.git
cd wayclicker
./install.sh
```

The script runs as your normal user and uses `sudo` only where needed. It will:

1. install prerequisites with `apt`, `dnf` or `zypper` (Python, compiler, Qt runtime libraries)
2. copy the app to `~/.local/share/wayclicker/app`
3. create a virtual environment and install `evdev` and `PyQt6` into it
4. add the launcher `~/.local/bin/wayclicker`, a desktop entry and the icon
5. install the udev rule, enable the `uinput` module and add you to the `input` group

Options: `--no-deps` (skip system packages), `--no-system` (skip kernel permission setup),
`--uninstall`, `-y` (assume yes). When it finishes, **log out and back in** once.

### From source (any distribution)

```bash
git clone https://github.com/matik90/wayclicker.git
cd wayclicker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # evdev is compiled: needs gcc + Python and kernel headers
python src/main.py
```

Then follow the permissions section below.

---

## Wayland prerequisites: permissions

Wayclicker needs two things from the kernel. Both are protected by default, so a one-time setup
is required. **Packaged installs and `install.sh` do steps 2 and 3 for you; step 1 (joining the
group) always needs a log out and back in.**

### 1. Join the `input` group (read the hotkey)

Keyboards appear as `/dev/input/event*`, owned by `root:input`.

```bash
sudo usermod -aG input $USER
```

Group changes only apply to **new login sessions**: log out and back in (or reboot).
Check with `groups`, the list must contain `input`.

### 2. Let the `input` group use `/dev/uinput` (inject input)

Install the udev rule shipped in `assets/`:

```bash
sudo install -Dm644 assets/60-wayclicker.rules /etc/udev/rules.d/60-wayclicker.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput
```

The rule is a single line:

```
KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess", GROUP="input", MODE="0660"
```

### 3. Make sure the `uinput` module is loaded

```bash
sudo modprobe uinput                                                    # now
sudo install -Dm644 assets/wayclicker-modules.conf /etc/modules-load.d/wayclicker.conf   # every boot
```

### Verify

```bash
ls -l /dev/uinput     # expected: crw-rw----+ 1 root input ...
wayclicker --check    # or: python src/main.py --check
```

Want to try it before logging out again? Start it with the new group applied to just that process:

```bash
sg input -c wayclicker
```

### A note on security

Membership of the `input` group lets a process read **every keystroke on the machine**, including
passwords typed into other applications. This is inherent to the evdev approach, and the same
trade-off tools like `ydotool`, `kmonad` and `keyd` make. Wayclicker only reacts to its configured
hotkey and never records or stores key presses, but you should only run software you trust under a
user that is in this group. Wayclicker does **not** grab your keyboards: every key still reaches
the focused application as usual.

---

## Usage

Start **Wayclicker** from your application menu or run `wayclicker`.

| Section                  | What it does |
|--------------------------|--------------|
| **Global hotkey**        | The key that starts and stops the clicker from any window. Type a name (`F4`, `f9`, `pause`) and press Enter, or click **Capture** and press the key. |
| **Click type**           | `Single click` or `Double click` per repetition. |
| **What to press**        | **Mouse button** enables the dropdown (`LMB` / `RMB`). **Keyboard key** enables the key field (type `f`, `space`, `e` ..., get suggestions while typing, or use **Capture**). Choosing one disables the other. |
| **Delay between actions**| Hours, minutes, seconds and milliseconds are added up. The line below shows the total and the approximate rate. The minimum is 1 ms. |
| **Repeat**               | `Repeat until stopped` runs until you press the hotkey again. `Repeat X times` stops by itself after X repetitions (a double click counts as one). |
| **Status badge**         | `IDLE` or `RUNNING`. |

Good to know:

- Mouse clicks happen **where the pointer currently is**. Move the cursor to the target first.
- Pressing the hotkey starts the first click immediately, then the delay applies.
- While running, the settings are locked. Stop, change, start again.
- The hotkey key is not swallowed. Pick a key your game or application does not use. If you choose
  `F4`, note that `Alt+F4` will also toggle the clicker.
- Key names are Linux key codes and refer to the **physical key position on a US-QWERTY keyboard**.
  On AZERTY or Dvorak layouts the printed letter may differ. **Capture** avoids any confusion.
- Accepted names: letters, digits, `F1`-`F24`, `space`, `enter`, `esc`, `tab`, `backspace`, `shift`,
  `ctrl`, `alt`, `super`, arrows (`up`, `down`, `left`, `right`), `home`, `end`, `pgup`, `pgdn`,
  `insert`, `delete`, `kp0` ... and raw evdev names such as `KEY_KP0`.

Command line:

```
wayclicker [--check] [--debug] [--version]
```

---

## Troubleshooting

**Popup: "Cannot create virtual input devices"**
`/dev/uinput` is missing or not writable. Run `wayclicker --check`, then follow steps 2 and 3 above
and log out and in again. Press **Retry** in the popup after fixing it, no restart needed (except for
group changes).

**Popup: "Cannot read keyboard devices" or the hotkey does nothing**
Your user cannot read `/dev/input/event*`. Join the `input` group (step 1) and log in again. The
Start button inside the window works even without this.

**"No readable keyboard found" in the footer**
Same cause as above, or no keyboard was detected. Plugged-in keyboards are picked up automatically
within a few seconds.

**The window does not open**
Force the native Wayland backend: `QT_QPA_PLATFORM=wayland wayclicker`. On very minimal systems
install the Qt6 runtime libraries (`libxkbcommon`, `libegl`, `fontconfig`, `dbus`).

**Clicks do nothing in a game**
Wayclicker creates a virtual mouse and keyboard, which the game sees as real hardware. Check that the
game window has focus and the pointer is over the target. Games with kernel-level anti-cheat may block
or punish automation; check the game's terms before using it online.

**Tiling compositors: make the window float**
Niri (`~/.config/niri/config.kdl`):
```kdl
window-rule {
    match app-id="^wayclicker$"
    open-floating true
}
```
Hyprland (older syntax; newer releases changed `windowrule`, see the Hyprland wiki):
```
windowrulev2 = float, class:^(wayclicker)$
```
KDE Plasma: System Settings, Window Management, Window Rules.

---

## Developer guide

### Project layout

```
wayclicker/
├── assets/
│   ├── style.qss                 dark theme (QSS), loaded by ui.py
│   ├── chevron-down.png          dropdown arrow used by the stylesheet
│   ├── wayclicker.desktop        desktop entry
│   ├── 60-wayclicker.rules       udev rule: /dev/uinput for the input group
│   └── wayclicker-modules.conf   loads the uinput module at boot
├── img/
│   └── icon.png                  application icon (512x512)
├── src/
│   ├── main.py                   entry point, wiring, --check
│   ├── clicker.py                evdev hotkeys, uinput injection, worker thread
│   └── ui.py                     PyQt6 window, validation, state binding
├── install.sh                    installer for non-Arch distributions
├── PKGBUILD                      Arch Linux / AUR package
├── requirements.txt
├── README.md
└── LICENSE
```

### 1. Run and test locally

```bash
cd wayclicker
python -m venv .venv && source .venv/bin/activate
# Arch: use the distro packages instead of pip
#   sudo pacman -S python-evdev python-pyqt6 && python -m venv --system-site-packages .venv
pip install -r requirements.txt

python src/main.py --check      # permissions report, no GUI needed
python src/main.py --debug      # run with verbose logging
```

If you have not logged out since joining the `input` group, run it under the group directly:
`sg input -c ".venv/bin/python src/main.py --debug"`.

Manual test checklist:

1. **Window**: it opens, shows the icon, dark theme, `IDLE` badge and `Start clicking (F4)`.
2. **Keyboard test**: open any text editor, then in Wayclicker choose *Keyboard key*, type `a`,
   set the delay to 100 ms, click the editor, and press `F4`. Letters appear and the badge shows
   `RUNNING`. Press `F4` again: it stops. This proves hotkey (evdev) and injection (uinput).
3. **Mouse test**: in a terminal run `sudo libinput debug-events` (or `sudo evtest` and pick
   *Wayclicker Virtual Mouse*). Start the clicker with *Mouse button*: you will see
   `POINTER_BUTTON` events for `BTN_LEFT`.
4. **Repeat X times**: choose 5, start: it clicks five times, then the badge returns to `IDLE` by itself.
5. **Capture**: click **Capture** next to the hotkey, press `F9`: the field and the button label update.
6. **Validation**: type `zzz` into the key field: red border and an error message, Start is blocked.
7. **Permission popup**: run `wayclicker` as a user that is not in the `input` group: a popup with
   fix instructions appears instead of a crash.

### 2. Create the Git repository and push it to GitHub

```bash
cd wayclicker
git init -b main
git add .
git commit -m "Initial commit: Wayclicker 1.0.0"
```

Create an **empty** repository named `wayclicker` on GitHub (no README or license, they already
exist) at <https://github.com/new>, then:

```bash
git remote add origin git@github.com:matik90/wayclicker.git      # or the https:// URL
git push -u origin main
```

With the GitHub CLI this is a single command:

```bash
gh repo create matik90/wayclicker --public --source=. --remote=origin --push
```

Publish the release that the PKGBUILD points at:

```bash
git tag -a v1.0.0 -m "Wayclicker 1.0.0"
git push origin v1.0.0
```

### 3. Build and install the Arch package

> **Never run `makepkg` inside the repository.** makepkg uses a directory called `src/` for its own
> work (and `makepkg -c` deletes it), which is where the Python sources live. The PKGBUILD
> refuses to run there. Copy it to an empty directory:

```bash
BUILD="$(mktemp -d)" && cp PKGBUILD "$BUILD" && cd "$BUILD"
makepkg -si                       # clones the v1.0.0 tag, builds, installs with pacman
namcap PKGBUILD                   # lint (package: sudo pacman -S namcap)
namcap wayclicker-*.pkg.tar.zst
```

Run it after installing, then check the files landed correctly:
`pacman -Ql wayclicker` and `wayclicker --check`.

### 4. Publish on the AUR (so `yay -S wayclicker` works)

One-time setup: create an account on <https://aur.archlinux.org> and add your SSH public key in
*My Account*. First check that the name is free: `yay -Ss wayclicker`.

```bash
git clone ssh://aur@aur.archlinux.org/wayclicker.git aur-wayclicker    # "empty repository" warning is normal
cp PKGBUILD aur-wayclicker/ && cd aur-wayclicker
makepkg --printsrcinfo > .SRCINFO
git add PKGBUILD .SRCINFO
git commit -m "wayclicker 1.0.0"
git branch -M master              # the AUR only accepts the master branch
git push origin master
```

Users can now run `yay -S wayclicker`.

Releasing an update: change the code, bump `__version__` in `src/clicker.py` and `pkgver` in the
PKGBUILD (reset `pkgrel=1`), commit, tag `vX.Y.Z` and push the tag to GitHub, then in the
`aur-wayclicker` checkout copy the PKGBUILD, regenerate `.SRCINFO`, commit and push as above.

---

## How it works

```
 keyboards (/dev/input/event*)                      virtual devices (/dev/uinput)
        │  evdev, read only, never grabbed                    ▲  uinput
        ▼                                                     │
┌──────────────────┐   toggle()   ┌───────────────────────────┴──┐
│  HotkeyListener  │ ───────────▶ │ AutoClicker (worker thread)  │
│  (thread)        │              │ start / stop / shutdown      │
└────────┬─────────┘              │ threading.Event flags        │
         │ Qt signals             └───────────────┬──────────────┘
         ▼                                        │ Qt signals
┌─────────────────────────────────────────────────▼──────────────┐
│ MainWindow (GUI thread): validates input, publishes the config │
└─────────────────────────────────────────────────────────────────┘
```

- **Hotkey thread** polls all keyboards with `select()`, rescans for hot-plugged devices, ignores
  Wayclicker's own virtual devices and debounces double reports.
- **Worker thread** lives for the whole session and waits on `threading.Event` objects. The delay
  between clicks is an interruptible wait on the stop event, so stopping is immediate even with a
  long interval. Timing is schedule-based, so it does not drift, and it never fires a burst to catch up.
- **GUI thread** is the only one that touches widgets. Backend threads report back through Qt signals,
  which Qt delivers safely across threads. The UI publishes a validated, immutable `ClickConfig` to
  the engine on every change, so the global hotkey works even if the window is hidden or busy.
- **Errors**: a missing or unwritable `/dev/uinput`, an unreadable `/dev/input`, or a device that vanishes
  mid-run all end in a clear message, never in a crash or a stuck key. Anything held down is released when a run ends.

## License

[MIT](LICENSE). Copyright (c) 2026 Matik90.
