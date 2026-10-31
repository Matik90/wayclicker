#!/usr/bin/env python3
"""Wayclicker entry point.

Wires the PyQt6 window to the evdev/uinput backend:

    HotkeyListener (thread) --toggle--> AutoClicker (worker thread)
          |                                  |
          +---- Bridge signals ---- MainWindow (GUI thread)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    import clicker
except ImportError as exc:  # python-evdev is missing
    sys.stderr.write(
        f"Wayclicker could not import its backend: {exc}\n"
        "Install the 'evdev' Python package (Arch: python-evdev, "
        "pip: pip install evdev).\n")
    raise SystemExit(1)

log = logging.getLogger(clicker.APP_ID)


def parse_args(argv: List[str]) -> tuple[argparse.Namespace, List[str]]:
    parser = argparse.ArgumentParser(
        prog=clicker.APP_ID,
        description="Wayland-native autoclicker using evdev and uinput.",
    )
    parser.add_argument("--version", action="version",
                        version=f"{clicker.APP_NAME} {clicker.__version__}")
    parser.add_argument("--check", action="store_true",
                        help="check permissions and kernel interfaces, then exit")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    return parser.parse_known_args(argv)


def run_check() -> int:
    """Print a permissions report. Does not need a display or PyQt6."""
    print(f"{clicker.APP_NAME} {clicker.__version__} - environment check\n")
    failed = False
    for item in clicker.run_diagnostics():
        mark = "OK  " if item.ok else "FAIL"
        failed = failed or not item.ok
        print(f"[{mark}] {item.name}: {item.detail}")
    if failed:
        print("\n" + clicker.PERMISSION_HELP.rsplit("\n\n", 1)[0])
        return 1
    print("\nEverything looks good.")
    return 0


def acquire_backend(ui) -> Optional[clicker.VirtualInput]:
    """Create the uinput devices; offer a retry popup when that fails."""
    while True:
        try:
            return clicker.VirtualInput()
        except clicker.InputBackendError as exc:
            log.error("uinput unavailable: %s", exc)
            retry = ui.show_error_dialog(
                None,
                "Cannot create virtual input devices",
                str(exc),
                clicker.PERMISSION_HELP,
                retry=True,
            )
            if not retry:
                return None


def main(argv: Optional[List[str]] = None) -> int:
    args, qt_args = parse_args(list(sys.argv[1:] if argv is None else argv))
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(threadName)s: %(message)s",
    )
    if args.check:
        return run_check()

    try:
        import ui
    except ImportError as exc:
        sys.stderr.write(
            f"Wayclicker could not import the user interface: {exc}\n"
            "Install PyQt6 (Arch: python-pyqt6, pip: pip install PyQt6).\n")
        return 1

    app = ui.create_application([sys.argv[0], *qt_args])
    backend = acquire_backend(ui)
    if backend is None:
        return 1

    bridge = ui.Bridge()
    engine = clicker.AutoClicker(
        backend,
        on_state=bridge.state_changed.emit,
        on_progress=bridge.progress.emit,
        on_error=bridge.error.emit,
    )
    hotkey_code = clicker.resolve_key(clicker.DEFAULT_HOTKEY)
    listener = clicker.HotkeyListener(
        hotkey_code,
        on_hotkey=engine.toggle,
        on_devices=bridge.devices_changed.emit,
    )
    window = ui.MainWindow(engine, listener, bridge, hotkey_code)

    def cleanup() -> None:
        listener.stop()
        engine.shutdown()
        backend.close()

    app.aboutToQuit.connect(cleanup)
    signal_timer = ui.install_signal_handlers(app)

    listener.start()
    window.show()
    code = app.exec()
    del signal_timer
    return code


if __name__ == "__main__":
    sys.exit(main())
