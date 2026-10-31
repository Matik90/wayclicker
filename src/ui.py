"""Wayclicker user interface (PyQt6).

The window only ever touches the backend through a small surface:

* ``AutoClicker.set_config / toggle`` - runs on the GUI thread,
* ``HotkeyListener.set_hotkey / capture`` - runs on the GUI thread,
* a :class:`Bridge` of Qt signals - lets the worker and hotkey threads report
  back. Qt delivers signals emitted from foreign threads as queued events, so
  every widget is only ever modified on the GUI thread.
"""

from __future__ import annotations

import logging
import signal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QColor, QGuiApplication, QIcon, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QComboBox,
    QCompleter,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from clicker import (
    APP_ID,
    APP_NAME,
    MIN_INTERVAL_MS,
    PERMISSION_HELP,
    AutoClicker,
    ClickConfig,
    ClickerState,
    ClickType,
    ConfigError,
    HotkeyListener,
    InvalidKeyError,
    RepeatMode,
    TargetKind,
    __version__,
    common_key_names,
    key_display_name,
    resolve_key,
)

log = logging.getLogger(APP_ID)

APP_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = APP_ROOT / "assets"
ICON_PATH = APP_ROOT / "img" / "icon.png"
MATIK90_PATH = APP_ROOT / "img" / "matik90.png"


# --------------------------------------------------------------------------
# Application setup helpers
# --------------------------------------------------------------------------

def _dark_palette() -> QPalette:
    """Base palette so that anything the stylesheet misses is still dark."""
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#10131a",
        QPalette.ColorRole.WindowText: "#e8ebf2",
        QPalette.ColorRole.Base: "#0c0f15",
        QPalette.ColorRole.AlternateBase: "#171b25",
        QPalette.ColorRole.ToolTipBase: "#171b25",
        QPalette.ColorRole.ToolTipText: "#e8ebf2",
        QPalette.ColorRole.Text: "#e8ebf2",
        QPalette.ColorRole.Button: "#171b25",
        QPalette.ColorRole.ButtonText: "#e8ebf2",
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: "#f2b04c",
        QPalette.ColorRole.HighlightedText: "#10131a",
        QPalette.ColorRole.PlaceholderText: "#5d667c",
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#566078"))
    return palette


def load_stylesheet() -> str:
    """Read assets/style.qss and point its url() references at assets/."""
    path = ASSETS_DIR / "style.qss"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Stylesheet %s not loaded: %s", path, exc)
        return ""
    return text.replace("@ASSETS@", ASSETS_DIR.as_posix())


def create_application(argv: List[str]) -> QApplication:
    """Create the QApplication with theme, icon and Wayland app-id."""
    # The desktop file name becomes the Wayland app_id, which compositors use
    # to match the window with wayclicker.desktop (icon, taskbar grouping).
    QGuiApplication.setDesktopFileName(APP_ID)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(load_stylesheet())
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    return app


def install_signal_handlers(app: QApplication) -> QTimer:
    """Let Ctrl+C / SIGTERM close the app. Keep the returned timer alive."""
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    timer = QTimer(app)
    timer.timeout.connect(lambda: None)  # wakes the interpreter for signals
    timer.start(250)
    return timer


def show_error_dialog(
    parent: Optional[QWidget],
    title: str,
    summary: str,
    details: str = "",
    retry: bool = False,
) -> bool:
    """Show a warning popup. Returns True if the user chose "Retry"."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(summary)
    if details:
        box.setInformativeText(details)
    retry_button = None
    if retry:
        retry_button = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
    else:
        box.addButton("Close", QMessageBox.ButtonRole.AcceptRole)
    box.exec()
    return retry_button is not None and box.clickedButton() is retry_button


def format_duration(total_ms: int) -> str:
    """Format milliseconds as ``1 h 2 min 3 s 40 ms`` (zero parts omitted)."""
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    parts = []
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    if seconds:
        parts.append(f"{seconds} s")
    if millis or not parts:
        parts.append(f"{millis} ms")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Thread bridge
# --------------------------------------------------------------------------

class Bridge(QObject):
    """Qt signals that backend threads emit and the window listens to."""

    state_changed = pyqtSignal(object)      # ClickerState
    progress = pyqtSignal(int)              # actions performed in this run
    error = pyqtSignal(str, bool)           # message, fatal
    devices_changed = pyqtSignal(int, int)  # keyboards opened, permission denied
    key_captured = pyqtSignal(int)          # key code from the capture mode


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class MainWindow(QWidget):
    CAPTURE_TIMEOUT_MS = 8000

    def __init__(
        self,
        engine: AutoClicker,
        listener: HotkeyListener,
        bridge: Bridge,
        hotkey_code: int,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._listener = listener
        self._bridge = bridge
        self._hotkey_code = hotkey_code
        self._capture_target: Optional[str] = None
        self._capture_token = 0
        self._capture_buttons: Dict[str, QPushButton] = {}
        self._input_warning_shown = False

        self.setObjectName("MainWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(500)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self._build_ui()
        self._wire_signals()
        self._refresh_target_state()
        self._refresh_repeat_state()
        self._on_settings_changed()
        self._update_toggle_button(running=False)
        self._update_devices_label(0, 0)
        self.adjustSize()

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)
        root.addLayout(self._build_header())

        self._settings = QWidget()
        settings = QVBoxLayout(self._settings)
        settings.setContentsMargins(0, 0, 0, 0)
        settings.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(self._build_hotkey_card(), 3)
        top_row.addWidget(self._build_click_type_card(), 2)
        settings.addLayout(top_row)
        settings.addWidget(self._build_target_card())
        settings.addWidget(self._build_interval_card())
        settings.addWidget(self._build_repeat_card())
        root.addWidget(self._settings)

        self._banner = QLabel()
        self._banner.setObjectName("ErrorBanner")
        self._banner.setWordWrap(True)
        self._banner.setVisible(False)
        root.addWidget(self._banner)

        self._toggle_button = QPushButton()
        self._toggle_button.setObjectName("PrimaryButton")
        self._toggle_button.setMinimumHeight(46)
        self._toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        root.addWidget(self._toggle_button)

        footer = QHBoxLayout()
        self._devices_label = QLabel()
        self._devices_label.setObjectName("Footer")
        self._count_label = QLabel("Actions sent: 0")
        self._count_label.setObjectName("Footer")
        footer.addWidget(self._devices_label, 1)
        footer.addWidget(self._count_label, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(footer)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)

        logo = QLabel()
        logo.setFixedSize(40, 40)
        if MATIK90_PATH.exists():
            pixmap = QPixmap(str(MATIK90_PATH)).scaled(
                40, 40,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            logo.setPixmap(pixmap)
        header.addWidget(logo)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        subtitle = QLabel("Autoclicker for Wayland, built on evdev and uinput")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)

        self._badge = QLabel()
        self._badge.setObjectName("StatusBadge")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setMinimumWidth(104)
        self._set_badge(running=False)
        header.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    @staticmethod
    def _make_card(title: str) -> Tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)
        caption = QLabel(title)
        caption.setObjectName("CardTitle")
        layout.addWidget(caption)
        return card, layout

    def _make_capture_button(self, target: str) -> QPushButton:
        button = QPushButton("Capture")
        button.setObjectName("GhostButton")
        button.setProperty("capturing", False)
        button.setToolTip("Click, then press the key you want to use")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Without focus a Space/Enter press cannot re-trigger the button.
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda: self._toggle_capture(target))
        self._capture_buttons[target] = button
        return button

    def _build_hotkey_card(self) -> QFrame:
        card, layout = self._make_card("Global hotkey")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._hotkey_edit = QLineEdit(key_display_name(self._hotkey_code))
        self._hotkey_edit.setMaxLength(24)
        self._hotkey_edit.setPlaceholderText("F4")
        self._hotkey_edit.setToolTip("Starts and stops the clicker from anywhere")
        row.addWidget(self._hotkey_edit, 1)
        row.addWidget(self._make_capture_button("hotkey"))
        layout.addLayout(row)
        hint = QLabel("Starts and stops from any window")
        hint.setObjectName("Hint")
        layout.addWidget(hint)
        return card

    def _build_click_type_card(self) -> QFrame:
        card, layout = self._make_card("Click type")
        self._click_combo = QComboBox()
        self._click_combo.addItem("Single click", ClickType.SINGLE)
        self._click_combo.addItem("Double click", ClickType.DOUBLE)
        layout.addWidget(self._click_combo)
        layout.addStretch(1)
        return card

    def _build_target_card(self) -> QFrame:
        card, layout = self._make_card("What to press")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self._radio_mouse = QRadioButton("Mouse button")
        self._radio_keyboard = QRadioButton("Keyboard key")
        self._radio_mouse.setChecked(True)
        self._target_group = QButtonGroup(self)
        self._target_group.addButton(self._radio_mouse)
        self._target_group.addButton(self._radio_keyboard)

        self._button_combo = QComboBox()
        self._button_combo.addItem("Left button (LMB)", "LMB")
        self._button_combo.addItem("Right button (RMB)", "RMB")

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("Type a key, for example f, space, e")
        self._key_edit.setMaxLength(24)
        completer = QCompleter(common_key_names(), self._key_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._key_edit.setCompleter(completer)

        grid.addWidget(self._radio_mouse, 0, 0)
        grid.addWidget(self._button_combo, 0, 1, 1, 2)
        grid.addWidget(self._radio_keyboard, 1, 0)
        grid.addWidget(self._key_edit, 1, 1)
        grid.addWidget(self._make_capture_button("key"), 1, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return card

    def _build_interval_card(self) -> QFrame:
        card, layout = self._make_card("Delay between actions")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        units = (("Hours", 999, 0), ("Mins", 999, 0), ("Secs", 999, 0), ("MS", 9999, 100))
        spins: List[QSpinBox] = []
        for column, (caption, maximum, default) in enumerate(units):
            label = QLabel(caption)
            label.setObjectName("FieldCaption")
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            spin = QSpinBox()
            spin.setRange(0, maximum)
            spin.setValue(default)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            grid.addWidget(label, 0, column)
            grid.addWidget(spin, 1, column)
            grid.setColumnStretch(column, 1)
            spins.append(spin)
        self._spin_hours, self._spin_minutes, self._spin_seconds, self._spin_ms = spins
        layout.addLayout(grid)

        self._total_label = QLabel()
        self._total_label.setObjectName("Hint")
        layout.addWidget(self._total_label)
        return card

    def _build_repeat_card(self) -> QFrame:
        card, layout = self._make_card("Repeat")
        self._radio_forever = QRadioButton("Repeat until stopped")
        self._radio_count = QRadioButton("Repeat")
        self._radio_forever.setChecked(True)
        self._repeat_group = QButtonGroup(self)
        self._repeat_group.addButton(self._radio_forever)
        self._repeat_group.addButton(self._radio_count)

        self._spin_count = QSpinBox()
        self._spin_count.setRange(1, 999_999_999)
        self._spin_count.setValue(10)
        self._spin_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spin_count.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._spin_count.setMinimumWidth(110)
        times = QLabel("times")

        layout.addWidget(self._radio_forever)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._radio_count)
        row.addWidget(self._spin_count)
        row.addWidget(times)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _wire_signals(self) -> None:
        self._toggle_button.clicked.connect(lambda: self._engine.toggle())

        self._radio_mouse.toggled.connect(self._on_target_toggled)
        self._radio_count.toggled.connect(self._on_repeat_toggled)
        self._hotkey_edit.textChanged.connect(self._on_hotkey_text_changed)
        self._hotkey_edit.editingFinished.connect(self._apply_hotkey_text)

        self._button_combo.currentIndexChanged.connect(self._on_settings_changed)
        self._click_combo.currentIndexChanged.connect(self._on_settings_changed)
        self._key_edit.textChanged.connect(self._on_settings_changed)
        for spin in (self._spin_hours, self._spin_minutes, self._spin_seconds,
                     self._spin_ms, self._spin_count):
            spin.valueChanged.connect(self._on_settings_changed)

        bridge = self._bridge
        bridge.state_changed.connect(self._on_state_changed)
        bridge.progress.connect(self._on_progress)
        bridge.error.connect(self._on_engine_error)
        bridge.devices_changed.connect(self._on_devices_changed)
        bridge.key_captured.connect(self._on_key_captured)

    # -- small view helpers ------------------------------------------------

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_flag(self, widget: QWidget, name: str, value: object) -> None:
        if widget.property(name) != value:
            widget.setProperty(name, value)
            self._repolish(widget)

    def _set_badge(self, running: bool) -> None:
        self._badge.setText("\u25cf  RUNNING" if running else "\u25cf  IDLE")
        self._set_flag(self._badge, "state", "running" if running else "idle")

    def _update_toggle_button(self, running: bool) -> None:
        name = key_display_name(self._hotkey_code)
        verb = "Stop clicking" if running else "Start clicking"
        self._toggle_button.setText(f"{verb} ({name})")
        self._set_flag(self._toggle_button, "running", running)

    def _show_message(self, text: str) -> None:
        self._banner.setText(text)
        self._banner.setVisible(bool(text))

    def _update_devices_label(self, count: int, denied: int) -> None:
        if count == 0:
            text = "Hotkey listener: no readable keyboard found"
        else:
            text = f"Hotkey listener: {count} keyboard{'s' if count != 1 else ''}"
        if denied:
            text += f" ({denied} without access)"
        self._devices_label.setText(text)
        self._set_flag(self._devices_label, "warn", count == 0)

    # -- settings ----------------------------------------------------------

    def _resolve_target_key(self) -> int:
        text = self._key_edit.text().strip()
        if not text:
            raise ConfigError("Enter the key to press, for example f, space or e.")
        try:
            return resolve_key(text)
        except InvalidKeyError as exc:
            raise ConfigError(str(exc)) from exc

    def _build_config(self) -> ClickConfig:
        keyboard = self._radio_keyboard.isChecked()
        key_code: Optional[int] = None
        if keyboard:
            key_code = self._resolve_target_key()
            if key_code == self._hotkey_code:
                raise ConfigError(
                    "The key to press must differ from the global hotkey "
                    f"({key_display_name(self._hotkey_code)}).")
        repeat = RepeatMode.COUNT if self._radio_count.isChecked() else RepeatMode.UNTIL_STOPPED
        return ClickConfig(
            target=TargetKind.KEYBOARD if keyboard else TargetKind.MOUSE,
            mouse_button=self._button_combo.currentData(),
            key_code=key_code,
            click_type=self._click_combo.currentData(),
            hours=self._spin_hours.value(),
            minutes=self._spin_minutes.value(),
            seconds=self._spin_seconds.value(),
            milliseconds=self._spin_ms.value(),
            repeat_mode=repeat,
            repeat_count=self._spin_count.value(),
        )

    def _update_total_label(self) -> None:
        total_ms = (((self._spin_hours.value() * 60 + self._spin_minutes.value()) * 60
                     + self._spin_seconds.value()) * 1000 + self._spin_ms.value())
        if total_ms < MIN_INTERVAL_MS:
            self._total_label.setText(
                f"Total: {MIN_INTERVAL_MS} ms (the shortest possible delay is used)")
            return
        text = f"Total: {format_duration(total_ms)}"
        if total_ms <= 10_000:
            rate = 1000 / total_ms
            text += f", about {rate:.0f} per second" if rate >= 10 \
                else f", about {rate:.2f} per second"
        self._total_label.setText(text)

    def _on_settings_changed(self, *_args) -> None:
        self._update_total_label()
        key_invalid = False
        if self._radio_keyboard.isChecked():
            try:
                self._resolve_target_key()
            except ConfigError:
                key_invalid = bool(self._key_edit.text().strip())
        self._set_flag(self._key_edit, "invalid", key_invalid)

        try:
            config = self._build_config()
        except ConfigError as exc:
            self._engine.set_config(None, str(exc))
            self._show_message(str(exc))
        else:
            self._engine.set_config(config)
            self._show_message("")

    def _on_target_toggled(self, _checked: bool) -> None:
        self._refresh_target_state()
        self._on_settings_changed()

    def _refresh_target_state(self) -> None:
        mouse = self._radio_mouse.isChecked()
        self._button_combo.setEnabled(mouse)
        self._key_edit.setEnabled(not mouse)
        self._capture_buttons["key"].setEnabled(not mouse)

    def _on_repeat_toggled(self, _checked: bool) -> None:
        self._refresh_repeat_state()
        self._on_settings_changed()

    def _refresh_repeat_state(self) -> None:
        self._spin_count.setEnabled(self._radio_count.isChecked())

    # -- hotkey ------------------------------------------------------------

    def _on_hotkey_text_changed(self, text: str) -> None:
        try:
            resolve_key(text)
            invalid = False
        except InvalidKeyError:
            invalid = bool(text.strip())
        self._set_flag(self._hotkey_edit, "invalid", invalid)

    def _apply_hotkey_text(self) -> None:
        text = self._hotkey_edit.text()
        if key_display_name(self._hotkey_code).lower() == text.strip().lower():
            self._hotkey_edit.setText(key_display_name(self._hotkey_code))
            return
        try:
            code = resolve_key(text)
        except InvalidKeyError as exc:
            self._hotkey_edit.setText(key_display_name(self._hotkey_code))
            self._show_message(f"{exc} The hotkey was not changed.")
            return
        self._set_hotkey(code)

    def _set_hotkey(self, code: int) -> None:
        self._hotkey_code = code
        self._listener.set_hotkey(code)
        self._hotkey_edit.setText(key_display_name(code))
        self._update_toggle_button(self._engine.state is ClickerState.RUNNING)
        self._on_settings_changed()

    # -- key capture -------------------------------------------------------

    def _toggle_capture(self, target: str) -> None:
        if self._capture_target is not None:
            self._end_capture()
            return
        if self._listener.device_count == 0:
            self._show_message(
                "No readable keyboard was found, so keys cannot be captured. "
                "Check the 'input' group setup described in the README.")
            return
        self._capture_target = target
        self._capture_token += 1
        token = self._capture_token
        button = self._capture_buttons[target]
        button.setText("Press a key...")
        self._set_flag(button, "capturing", True)
        self.setFocus()  # keep the pressed key out of the text fields
        self._listener.capture(self._bridge.key_captured.emit)
        QTimer.singleShot(self.CAPTURE_TIMEOUT_MS, lambda: self._capture_timeout(token))

    def _capture_timeout(self, token: int) -> None:
        if token == self._capture_token and self._capture_target is not None:
            self._end_capture()

    def _end_capture(self) -> None:
        self._listener.cancel_capture()
        self._capture_target = None
        for button in self._capture_buttons.values():
            button.setText("Capture")
            self._set_flag(button, "capturing", False)

    def _on_key_captured(self, code: int) -> None:
        target = self._capture_target
        if target is None:
            return
        self._end_capture()
        if target == "hotkey":
            self._set_hotkey(code)
        else:
            self._key_edit.setText(key_display_name(code))

    # -- backend notifications --------------------------------------------

    def _on_state_changed(self, state: object) -> None:
        running = state is ClickerState.RUNNING
        self._settings.setEnabled(not running)
        self._set_badge(running)
        self._update_toggle_button(running)
        if running:
            if self._capture_target is not None:
                self._end_capture()
            self._show_message("")

    def _on_progress(self, count: int) -> None:
        self._count_label.setText(f"Actions sent: {count}")

    def _on_engine_error(self, message: str, fatal: bool) -> None:
        if fatal:
            show_error_dialog(self, "Wayclicker stopped", message, PERMISSION_HELP)
        else:
            self._show_message(message)

    def _on_devices_changed(self, count: int, denied: int) -> None:
        self._update_devices_label(count, denied)
        if count == 0 and denied > 0 and not self._input_warning_shown:
            self._input_warning_shown = True
            QTimer.singleShot(0, self._warn_input_permissions)

    def _warn_input_permissions(self) -> None:
        show_error_dialog(
            self,
            "Cannot read keyboard devices",
            "Wayclicker is not allowed to read /dev/input/event*, so the global "
            "hotkey will not work. The Start button in this window still does.",
            PERMISSION_HELP,
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        self._engine.stop()
        super().closeEvent(event)
