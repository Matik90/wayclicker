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
5. [License](#license)

---

## Installation

### Arch / CachyOS / any Arch-based distro (AUR)

```bash
yay -S wayclicker
```

The package installs the app, a desktop entry, the udev rule and the `uinput` module-load file.
You still have to join the `input` group once, see [permissions](#wayland-prerequisites-permissions).

### Other distributions (Debian, Ubuntu, Fedora, openSUSE, ...)

```bash
git clone https://github.com/matik90/wayclicker.git
cd wayclicker
./install.sh
```

Options: `--no-deps` (skip system packages), `--no-system` (skip kernel permission setup),
`--uninstall`, `-y` (assume yes). When it finishes, **log out and back in** once.

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

## License

[MIT](LICENSE). Copyright (c) 2026 Matik90.
