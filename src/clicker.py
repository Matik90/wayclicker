"""Wayclicker core backend.

This module has no GUI dependencies. It provides three building blocks:

* :class:`VirtualInput` - creates virtual mouse/keyboard devices through
  ``/dev/uinput`` and injects events with them.
* :class:`HotkeyListener` - watches every keyboard under ``/dev/input`` with
  ``evdev`` so a global hotkey works on Wayland (and in full-screen games),
  without grabbing the devices.
* :class:`AutoClicker` - a persistent worker thread that performs the actual
  click / key-press loop, controlled with :mod:`threading` events.

Neither evdev nor uinput talk to the display server. That is why they keep
working on Wayland compositors (KDE Plasma, Niri, Hyprland, Sway, GNOME ...)
where applications are not allowed to listen to or synthesise global input.
"""

from __future__ import annotations

import grp
import logging
import os
import select
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple

from evdev import InputDevice, ecodes, list_devices
from evdev.uinput import UInput, UInputError

__version__ = "1.0.0"
APP_NAME = "Wayclicker"
APP_ID = "wayclicker"
DEFAULT_HOTKEY = "F4"

log = logging.getLogger(APP_ID)

UINPUT_PATHS = ("/dev/uinput", "/dev/input/uinput", "/dev/misc/uinput")
VIRTUAL_DEVICE_PREFIX = "Wayclicker Virtual"
MOUSE_DEVICE_NAME = f"{VIRTUAL_DEVICE_PREFIX} Mouse"
KEYBOARD_DEVICE_NAME = f"{VIRTUAL_DEVICE_PREFIX} Keyboard"

MIN_INTERVAL_MS = 1          # lower bound for the delay between two actions
MAX_HOLD_SECONDS = 0.030     # how long a button/key stays pressed
MAX_GAP_SECONDS = 0.040      # pause between the two clicks of a double click
MIN_PHASE_SECONDS = 0.0005   # shortest hold / gap used for tiny intervals
DEVICE_SETTLE_SECONDS = 0.3  # compositors need a moment to adopt new devices
HOTKEY_DEBOUNCE_SECONDS = 0.2
PROGRESS_INTERVAL_SECONDS = 0.1
DEVICE_RESCAN_SECONDS = 3.0

PERMISSION_HELP = (
    "Wayclicker needs two kernel interfaces:\n"
    "  - /dev/uinput  to create virtual input devices (write access)\n"
    "  - /dev/input/event*  to detect the global hotkey (read access)\n\n"
    "How to fix it:\n"
    "  1. Load the module:  sudo modprobe uinput\n"
    "  2. Install the udev rule (assets/60-wayclicker.rules) so the 'input' "
    "group may use /dev/uinput.\n"
    "  3. Add yourself to the group:  sudo usermod -aG input $USER\n"
    "  4. Log out and back in (group changes only apply to new sessions).\n\n"
    "Run 'wayclicker --check' for a diagnosis, or see the README."
)


class InputBackendError(RuntimeError):
    """The virtual input devices are missing, inaccessible or broke down."""


class InvalidKeyError(ValueError):
    """A key name could not be translated into a kernel key code."""


class ConfigError(ValueError):
    """The clicker settings are not valid."""


# --------------------------------------------------------------------------
# Key name handling
# --------------------------------------------------------------------------

_KEY_ALIASES: Dict[str, str] = {
    "space": "KEY_SPACE", "spacebar": "KEY_SPACE",
    "enter": "KEY_ENTER", "return": "KEY_ENTER",
    "esc": "KEY_ESC", "escape": "KEY_ESC",
    "tab": "KEY_TAB",
    "backspace": "KEY_BACKSPACE",
    "shift": "KEY_LEFTSHIFT", "lshift": "KEY_LEFTSHIFT", "rshift": "KEY_RIGHTSHIFT",
    "ctrl": "KEY_LEFTCTRL", "control": "KEY_LEFTCTRL",
    "lctrl": "KEY_LEFTCTRL", "rctrl": "KEY_RIGHTCTRL",
    "alt": "KEY_LEFTALT", "lalt": "KEY_LEFTALT", "ralt": "KEY_RIGHTALT",
    "altgr": "KEY_RIGHTALT",
    "super": "KEY_LEFTMETA", "meta": "KEY_LEFTMETA", "win": "KEY_LEFTMETA",
    "windows": "KEY_LEFTMETA",
    "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
    "pgup": "KEY_PAGEUP", "pageup": "KEY_PAGEUP",
    "pgdn": "KEY_PAGEDOWN", "pagedown": "KEY_PAGEDOWN",
    "home": "KEY_HOME", "end": "KEY_END",
    "ins": "KEY_INSERT", "insert": "KEY_INSERT",
    "del": "KEY_DELETE", "delete": "KEY_DELETE",
    "caps": "KEY_CAPSLOCK", "capslock": "KEY_CAPSLOCK",
    "numlock": "KEY_NUMLOCK", "scrolllock": "KEY_SCROLLLOCK",
    "pause": "KEY_PAUSE", "break": "KEY_PAUSE",
    "print": "KEY_SYSRQ", "prtsc": "KEY_SYSRQ", "printscreen": "KEY_SYSRQ",
    "menu": "KEY_COMPOSE",
    "minus": "KEY_MINUS", "-": "KEY_MINUS",
    "equal": "KEY_EQUAL", "=": "KEY_EQUAL",
    "[": "KEY_LEFTBRACE", "]": "KEY_RIGHTBRACE",
    ";": "KEY_SEMICOLON", "'": "KEY_APOSTROPHE",
    "`": "KEY_GRAVE", "\\": "KEY_BACKSLASH",
    ",": "KEY_COMMA", ".": "KEY_DOT", "/": "KEY_SLASH",
}


def resolve_key(name: str) -> int:
    """Translate a human friendly key name into a Linux key code.

    Accepts single characters (``f``, ``7``), function keys (``F4``), common
    aliases (``space``, ``enter``, ``ctrl``) and raw evdev names
    (``KEY_KP0``). Matching is case-insensitive.
    """
    raw = (name or "").strip()
    if not raw:
        raise InvalidKeyError("No key specified.")
    candidate = _KEY_ALIASES.get(raw.lower())
    if candidate is None:
        cleaned = raw.upper().replace(" ", "_")
        candidate = cleaned if cleaned.startswith("KEY_") else f"KEY_{cleaned}"
    code = ecodes.ecodes.get(candidate)
    # Codes >= 256 are buttons (BTN_*) or out of range; only keyboard keys here.
    if not isinstance(code, int) or not 0 < code < 256:
        raise InvalidKeyError(f'Unknown key "{raw}".')
    return code


def key_display_name(code: int) -> str:
    """Return a short readable name (``F4``, ``SPACE``, ``E``) for a key code."""
    name = ecodes.KEY.get(code)
    if isinstance(name, (list, tuple)):
        name = name[0] if name else None
    if not name:
        return f"KEY_{code}"
    return name[4:] if name.startswith("KEY_") else name


def common_key_names() -> List[str]:
    """Key names offered as suggestions in the UI."""
    names = [chr(c) for c in range(ord("a"), ord("z") + 1)]
    names += [str(d) for d in range(10)]
    names += [f"F{n}" for n in range(1, 25)]
    names += sorted({a for a in _KEY_ALIASES if len(a) > 1 and a.isalpha()})
    valid = []
    for name in names:
        try:
            resolve_key(name)
        except InvalidKeyError:
            continue
        valid.append(name)
    return valid


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

class TargetKind(Enum):
    MOUSE = "mouse"
    KEYBOARD = "keyboard"


class ClickType(Enum):
    SINGLE = "single"
    DOUBLE = "double"


class RepeatMode(Enum):
    UNTIL_STOPPED = "until_stopped"
    COUNT = "count"


MOUSE_BUTTONS: Dict[str, int] = {
    "LMB": ecodes.BTN_LEFT,
    "RMB": ecodes.BTN_RIGHT,
}


@dataclass(frozen=True)
class ClickConfig:
    """Immutable, validated description of one autoclicker run."""

    target: TargetKind = TargetKind.MOUSE
    mouse_button: str = "LMB"
    key_code: Optional[int] = None
    click_type: ClickType = ClickType.SINGLE
    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    milliseconds: int = 100
    repeat_mode: RepeatMode = RepeatMode.UNTIL_STOPPED
    repeat_count: int = 1

    def __post_init__(self) -> None:
        for label, value in (
            ("Hours", self.hours),
            ("Minutes", self.minutes),
            ("Seconds", self.seconds),
            ("Milliseconds", self.milliseconds),
        ):
            if not isinstance(value, int) or value < 0:
                raise ConfigError(f"{label} must be a non-negative whole number.")
        if self.target is TargetKind.MOUSE:
            if self.mouse_button not in MOUSE_BUTTONS:
                raise ConfigError(f'Unknown mouse button "{self.mouse_button}".')
        else:
            if self.key_code is None or not 0 < self.key_code < 256:
                raise ConfigError("Choose a keyboard key to press.")
        if self.repeat_mode is RepeatMode.COUNT and self.repeat_count < 1:
            raise ConfigError("The repeat count must be at least 1.")

    @property
    def total_ms(self) -> int:
        """Delay between two actions, exactly as entered (may be 0)."""
        return ((self.hours * 60 + self.minutes) * 60 + self.seconds) * 1000 \
            + self.milliseconds

    @property
    def interval(self) -> float:
        """Delay between two actions in seconds, never below 1 ms."""
        return max(MIN_INTERVAL_MS, self.total_ms) / 1000.0

    @property
    def target_code(self) -> int:
        """Kernel code of the button or key that will be pressed."""
        if self.target is TargetKind.MOUSE:
            return MOUSE_BUTTONS[self.mouse_button]
        assert self.key_code is not None
        return self.key_code


# --------------------------------------------------------------------------
# Permission diagnostics
# --------------------------------------------------------------------------

def find_uinput_path() -> Optional[str]:
    """Return the first existing uinput device node, if any."""
    for path in UINPUT_PATHS:
        if os.path.exists(path):
            return path
    return None


def diagnose_uinput() -> Optional[str]:
    """Return a human readable problem description, or ``None`` if usable."""
    path = find_uinput_path()
    if path is None:
        return ("/dev/uinput does not exist. The 'uinput' kernel module is "
                "probably not loaded (try: sudo modprobe uinput).")
    if not os.access(path, os.W_OK):
        return (f"No write access to {path}. Your user is not allowed to create "
                "virtual input devices.")
    return None


@dataclass
class DiagnosticItem:
    ok: bool
    name: str
    detail: str


def run_diagnostics() -> List[DiagnosticItem]:
    """Collect information used by ``wayclicker --check``."""
    items: List[DiagnosticItem] = []
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    items.append(DiagnosticItem(True, "Session type", session))

    problem = diagnose_uinput()
    items.append(DiagnosticItem(
        problem is None, "uinput (inject input)",
        problem or f"{find_uinput_path()} is writable"))

    try:
        group_names = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
    except KeyError:
        group_names = set()
    in_group = "input" in group_names
    items.append(DiagnosticItem(
        in_group, "'input' group (this session)",
        "member" if in_group else
        "not a member (after 'usermod -aG input $USER' log out and in again)"))

    readable = denied = 0
    for path in list_devices():
        try:
            InputDevice(path).close()
            readable += 1
        except PermissionError:
            denied += 1
        except OSError:
            continue
    items.append(DiagnosticItem(
        readable > 0, "evdev (read hotkeys)",
        f"{readable} readable event device(s), {denied} denied"))
    return items


# --------------------------------------------------------------------------
# uinput: virtual devices
# --------------------------------------------------------------------------

class VirtualInput:
    """Virtual mouse and keyboard created through ``/dev/uinput``.

    Two separate devices are used because compositors classify a device by its
    capabilities; a pure mouse and a pure keyboard are recognised everywhere.
    Raises :class:`InputBackendError` if the devices cannot be created.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pressed: Set[int] = set()
        self._mouse: Optional[UInput] = None
        self._keyboard: Optional[UInput] = None
        self._ready_at = 0.0

        problem = diagnose_uinput()
        if problem:
            raise InputBackendError(problem)

        key_codes = sorted(c for c in ecodes.KEY if 0 < c < 256)
        mouse_caps = {
            ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y,
                            ecodes.REL_WHEEL, ecodes.REL_HWHEEL],
        }
        try:
            self._keyboard = UInput({ecodes.EV_KEY: key_codes},
                                    name=KEYBOARD_DEVICE_NAME)
            self._mouse = UInput(mouse_caps, name=MOUSE_DEVICE_NAME)
        except (UInputError, OSError) as exc:
            self.close()
            raise InputBackendError(
                f"Could not create virtual input devices: {exc}") from exc
        self._ready_at = time.monotonic() + DEVICE_SETTLE_SECONDS
        log.info("Virtual devices created (%s, %s)",
                 KEYBOARD_DEVICE_NAME, MOUSE_DEVICE_NAME)

    def _device_for(self, code: int) -> UInput:
        device = self._mouse if code >= ecodes.BTN_MISC else self._keyboard
        if device is None:
            raise InputBackendError("The virtual input devices are closed.")
        return device

    def _emit(self, code: int, value: int) -> None:
        device = self._device_for(code)
        delay = self._ready_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        try:
            with self._lock:
                device.write(ecodes.EV_KEY, code, value)
                device.syn()
                if value:
                    self._pressed.add(code)
                else:
                    self._pressed.discard(code)
        except (UInputError, OSError) as exc:
            raise InputBackendError(
                f"Writing to the virtual input device failed: {exc}") from exc

    def press(self, code: int) -> None:
        self._emit(code, 1)

    def release(self, code: int) -> None:
        self._emit(code, 0)

    def release_all(self) -> None:
        """Release everything still held down (safety net, never raises)."""
        for code in list(self._pressed):
            try:
                self._emit(code, 0)
            except InputBackendError:
                self._pressed.discard(code)

    def close(self) -> None:
        self.release_all()
        for name in ("_keyboard", "_mouse"):
            device = getattr(self, name)
            setattr(self, name, None)
            if device is not None:
                try:
                    device.close()
                except OSError:
                    pass


# --------------------------------------------------------------------------
# Worker thread
# --------------------------------------------------------------------------

class ClickerState(Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"


StateCallback = Callable[[ClickerState], None]
ProgressCallback = Callable[[int], None]
ErrorCallback = Callable[[str, bool], None]


class AutoClicker:
    """Runs the click loop in a dedicated background thread.

    The thread lives for the whole lifetime of the object and is steered with
    three :class:`threading.Event` objects:

    * ``_start_event``   - a run was requested,
    * ``_stop_event``    - the current run must end (also used as the
      interruptible sleep between two actions),
    * ``_shutdown_event`` - the application is quitting.

    Callbacks are invoked from the worker thread; GUI code must hand them over
    to its own thread (the UI does that with Qt signals).
    """

    def __init__(
        self,
        backend: VirtualInput,
        on_state: Optional[StateCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
        on_error: Optional[ErrorCallback] = None,
    ) -> None:
        self._backend = backend
        self._on_state = on_state
        self._on_progress = on_progress
        self._on_error = on_error

        self._lock = threading.RLock()
        self._config: Optional[ClickConfig] = None
        self._config_error = "The settings are not valid."
        self._active_config: Optional[ClickConfig] = None
        self._running = False

        self._start_event = threading.Event()
        self._stop_event = threading.Event()
        self._shutdown_event = threading.Event()
        self._thread = threading.Thread(
            target=self._worker_loop, name="wayclicker-worker", daemon=True)
        self._thread.start()

    # -- public API --------------------------------------------------------

    @property
    def state(self) -> ClickerState:
        with self._lock:
            return ClickerState.RUNNING if self._running else ClickerState.IDLE

    def set_config(self, config: Optional[ClickConfig], error: str = "") -> None:
        """Store the settings used by the next run (``None`` = invalid)."""
        with self._lock:
            self._config = config
            if error:
                self._config_error = error

    def start(self) -> bool:
        """Begin clicking. Returns ``False`` if it could not be started."""
        with self._lock:
            if self._running or self._shutdown_event.is_set():
                return False
            config = self._config
            if config is not None:
                self._active_config = config
                self._stop_event.clear()
                self._running = True
                self._start_event.set()
                return True
            message = self._config_error
        self._notify_error(message, False)
        return False

    def stop(self) -> bool:
        """Ask the current run to end. Returns ``False`` if nothing ran."""
        with self._lock:
            if not self._running:
                return False
            self._stop_event.set()
            return True

    def toggle(self) -> None:
        """Start when idle, stop when running (used by the global hotkey)."""
        if self.state is ClickerState.RUNNING:
            self.stop()
        else:
            self.start()

    def shutdown(self) -> None:
        """Stop clicking and terminate the worker thread."""
        self._shutdown_event.set()
        self._stop_event.set()
        self._start_event.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)

    # -- worker ------------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            if not self._start_event.wait(timeout=0.25):
                continue
            self._start_event.clear()
            if self._shutdown_event.is_set():
                break
            self._run_session()

    @staticmethod
    def _timings(interval: float) -> Tuple[float, float]:
        """Return (hold, gap) so that one action always fits in the interval."""
        hold = min(MAX_HOLD_SECONDS, max(MIN_PHASE_SECONDS, interval * 0.25))
        gap = min(MAX_GAP_SECONDS, max(MIN_PHASE_SECONDS, interval * 0.25))
        return hold, gap

    def _tap(self, code: int, hold: float) -> None:
        self._backend.press(code)
        try:
            time.sleep(hold)
        finally:
            self._backend.release(code)

    def _perform(self, config: ClickConfig, hold: float, gap: float) -> None:
        code = config.target_code
        self._tap(code, hold)
        if config.click_type is ClickType.DOUBLE:
            time.sleep(gap)
            self._tap(code, hold)

    def _run_session(self) -> None:
        config = self._active_config
        if config is None:  # cannot happen via start(); defensive only
            with self._lock:
                self._running = False
            return
        interval = config.interval
        hold, gap = self._timings(interval)
        limit = config.repeat_count if config.repeat_mode is RepeatMode.COUNT else None
        count = 0
        failure: Optional[str] = None
        last_report = 0.0

        self._notify_state(ClickerState.RUNNING)
        log.info("Run started: %s, every %.3f s", config.target.value, interval)
        try:
            next_time = time.monotonic()
            while not self._stop_event.is_set():
                self._perform(config, hold, gap)
                count += 1

                now = time.monotonic()
                if now - last_report >= PROGRESS_INTERVAL_SECONDS:
                    last_report = now
                    self._notify_progress(count)
                if limit is not None and count >= limit:
                    break

                next_time += interval
                delay = next_time - time.monotonic()
                if delay > 0:
                    if self._stop_event.wait(delay):
                        break
                else:
                    # Running behind schedule: do not fire a burst to catch up.
                    next_time = time.monotonic()
        except InputBackendError as exc:
            failure = str(exc)
            log.error("Run aborted: %s", exc)
        except Exception as exc:  # noqa: BLE001 - the worker must never die silently
            failure = f"Unexpected error: {exc}"
            log.exception("Unexpected error in worker thread")
        finally:
            self._backend.release_all()
            with self._lock:
                self._running = False
            self._notify_progress(count)
            log.info("Run finished after %d action(s)", count)
            self._notify_state(ClickerState.IDLE)
            if failure:
                self._notify_error(failure, True)

    # -- callbacks ---------------------------------------------------------

    def _notify_state(self, state: ClickerState) -> None:
        self._safe_call(self._on_state, state)

    def _notify_progress(self, count: int) -> None:
        self._safe_call(self._on_progress, count)

    def _notify_error(self, message: str, fatal: bool) -> None:
        self._safe_call(self._on_error, message, fatal)

    @staticmethod
    def _safe_call(callback: Optional[Callable], *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001
            log.exception("Callback raised an exception")


# --------------------------------------------------------------------------
# evdev: global hotkey
# --------------------------------------------------------------------------

DevicesCallback = Callable[[int, int], None]
CaptureCallback = Callable[[int], None]


class HotkeyListener:
    """Watches all keyboards for one global trigger key.

    Devices are opened read-only in spirit: they are never *grabbed*, so every
    key press still reaches the focused application normally. Keyboards that
    are plugged in later are picked up by a periodic rescan, unplugged ones are
    dropped automatically.
    """

    def __init__(
        self,
        hotkey_code: int,
        on_hotkey: Callable[[], None],
        on_devices: Optional[DevicesCallback] = None,
    ) -> None:
        self._hotkey_code = hotkey_code
        self._on_hotkey = on_hotkey
        self._on_devices = on_devices

        self._lock = threading.Lock()
        self._capture: Optional[CaptureCallback] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_trigger = 0.0
        self._last_report: Optional[Tuple[int, int]] = None
        self._device_count = 0
        self._ignored: Set[str] = set()
        self._rescan_now = False

    # -- public API --------------------------------------------------------

    @property
    def device_count(self) -> int:
        return self._device_count

    def set_hotkey(self, code: int) -> None:
        with self._lock:
            self._hotkey_code = code

    def capture(self, callback: CaptureCallback) -> None:
        """Report the next key press to *callback* instead of acting on it."""
        with self._lock:
            self._capture = callback

    def cancel_capture(self) -> None:
        with self._lock:
            self._capture = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="wayclicker-hotkeys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _is_keyboard(device: InputDevice) -> bool:
        if device.name.startswith(VIRTUAL_DEVICE_PREFIX):
            return False  # never listen to ourselves
        try:
            keys = set(device.capabilities().get(ecodes.EV_KEY, []))
        except OSError:
            return False
        return bool(keys & {ecodes.KEY_A, ecodes.KEY_SPACE,
                            ecodes.KEY_ENTER, ecodes.KEY_F1})

    def _scan(self, devices: Dict[str, InputDevice]) -> int:
        """Synchronise *devices* with /dev/input. Returns the denied count."""
        denied = 0
        try:
            paths = set(list_devices())
        except OSError:
            paths = set()

        for path in list(devices):
            if path not in paths:
                self._drop(devices, path)
        self._ignored &= paths

        for path in sorted(paths):
            if path in devices or path in self._ignored:
                continue
            try:
                device = InputDevice(path)
            except PermissionError:
                denied += 1
                continue
            except OSError:
                continue
            if self._is_keyboard(device):
                devices[path] = device
                log.info("Listening on %s (%s)", path, device.name)
            else:
                device.close()
                self._ignored.add(path)
        return denied

    @staticmethod
    def _drop(devices: Dict[str, InputDevice], path: str) -> None:
        device = devices.pop(path, None)
        if device is not None:
            log.info("Stopped listening on %s", path)
            try:
                device.close()
            except OSError:
                pass

    def _report(self, count: int, denied: int) -> None:
        self._device_count = count
        if self._last_report == (count, denied):
            return
        self._last_report = (count, denied)
        if self._on_devices is not None:
            try:
                self._on_devices(count, denied)
            except Exception:  # noqa: BLE001
                log.exception("Device callback raised an exception")

    def _run(self) -> None:
        devices: Dict[str, InputDevice] = {}
        next_scan = 0.0
        denied = 0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_scan or self._rescan_now:
                    self._rescan_now = False
                    denied = self._scan(devices)
                    self._report(len(devices), denied)
                    next_scan = now + DEVICE_RESCAN_SECONDS

                if not devices:
                    self._stop.wait(0.5)
                    continue

                by_fd = {dev.fd: path for path, dev in devices.items()}
                try:
                    ready, _, _ = select.select(list(by_fd), [], [], 0.25)
                except (OSError, ValueError):
                    self._rescan_now = True
                    for path, dev in list(devices.items()):
                        try:
                            os.fstat(dev.fd)
                        except (OSError, ValueError):
                            self._drop(devices, path)
                    continue

                for fd in ready:
                    path = by_fd[fd]
                    if path in devices:
                        self._drain(devices, path)
        except Exception:  # noqa: BLE001
            log.exception("Hotkey listener crashed")
        finally:
            for path in list(devices):
                self._drop(devices, path)
            self._report(0, 0)

    def _drain(self, devices: Dict[str, InputDevice], path: str) -> None:
        device = devices[path]
        try:
            for event in device.read():
                if event.type == ecodes.EV_KEY:
                    self._handle_key(event.code, event.value)
        except BlockingIOError:
            pass
        except OSError:  # device unplugged (ENODEV) or otherwise gone
            self._drop(devices, path)
            self._rescan_now = True

    def _handle_key(self, code: int, value: int) -> None:
        if value != 1 or code >= 256:  # presses of real keys only
            return
        with self._lock:
            capture, self._capture = self._capture, None
            hotkey = self._hotkey_code
        if capture is not None:
            self._safe_call(capture, code)
            return
        if code != hotkey:
            return
        now = time.monotonic()
        if now - self._last_trigger < HOTKEY_DEBOUNCE_SECONDS:
            return
        self._last_trigger = now
        self._safe_call(self._on_hotkey)

    @staticmethod
    def _safe_call(callback: Callable, *args) -> None:
        try:
            callback(*args)
        except Exception:  # noqa: BLE001
            log.exception("Hotkey callback raised an exception")
