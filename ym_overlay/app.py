from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import keyboard
from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import (
    APP_NAME,
    FONT_FAMILY,
    FONT_PRESETS,
    HOTKEYS,
    MUTEX_NAME,
    find_yandex_music,
    resource_path,
)
from .lyrics import LyricLine, LyricsService
from .media import MediaController
from .models import OverlayMessage, TrackState
from .preferences import (
    LYRICS_OFFSET_DEFAULT,
    default_hotkey_sequences,
    load_hotkey_sequences,
    normalize_lyrics_offset,
    validate_hotkeys,
)
from .settings_ui import FirstRunWizard, SettingsDialog
from .ui import HelpHUD, ToastHUD, UiBridge
from .windows import (
    GlobalHotkeyManager,
    PassthroughHotkeyManager,
    SingleInstance,
    foreground_prefers_passthrough,
)

ACTION_BY_HOTKEY = {
    "ctrl+shift+x": "next",
    "ctrl+shift+z": "prev",
    "ctrl+shift+c": "play_pause",
    "ctrl+shift+left": "seek_left",
    "ctrl+shift+right": "seek_right",
    "ctrl+shift+up": "vol_up",
    "ctrl+shift+down": "vol_down",
    "ctrl+shift+m": "vol_mute",
    "ctrl+shift+o": "toggle_notifications",
    "ctrl+shift+l": "toggle_karaoke",
    "ctrl+shift+k": "show_help",
    "ctrl+shift+h": "launch",
    "ctrl+shift+f": "cycle_font",
    "shift+tab": "lyrics_later",
    "ctrl+shift+tab": "lyrics_earlier",
    "ctrl+shift+g": "toggle_edit_mode",
}


def _configure_logging() -> logging.Logger:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "YandexMusicGameOverlay"
    local.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(local / "overlay.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("ym_overlay")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


class OverlayApplication:
    def __init__(self, qt_app: QApplication, logger: logging.Logger):
        self.qt_app = qt_app
        self.logger = logger
        self.bridge = UiBridge()
        self.media = MediaController(self.bridge.message.emit, self.bridge.track.emit)
        self.lyrics = LyricsService()
        self.settings = QSettings("BRAT12344321", "YandexMusicGameOverlay")
        self.toast = ToastHUD(self.media.current_position)
        self.help = HelpHUD()
        self.notifications = True
        self.karaoke = False
        self.current_track = TrackState()
        self._lyrics_key = ""
        self._last_action: dict[str, float] = {}
        self._escape_hotkey = None
        self._edit_mode = False
        self._help_before_edit = False
        native_bindings = self._native_bindings()
        self.hotkeys = GlobalHotkeyManager(native_bindings, self.bridge.hotkey.emit)
        self.game_hotkeys = PassthroughHotkeyManager(native_bindings, self.bridge.hotkey.emit)
        self._passthrough_active: bool | None = None
        self._game_active = False
        self._smart_visibility = True
        self._game_mode = True
        self.tray: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tray_karaoke_action: QAction | None = None
        self._font_presets = self._available_font_presets()
        self._font_index = self._restore_font_index()
        offset_migrated = self._setting_bool("lyrics_offset_migrated_3_5_5", False)
        self._lyrics_offset = normalize_lyrics_offset(
            self.settings.value("lyrics_offset", LYRICS_OFFSET_DEFAULT),
            reset_legacy_extreme=not offset_migrated,
        )
        if not offset_migrated:
            self.settings.setValue("lyrics_offset", self._lyrics_offset)
            self.settings.setValue("lyrics_offset_migrated_3_5_5", True)
        if not self._apply_font(self._font_presets[self._font_index]):
            self._font_index = next(
                (index for index, preset in enumerate(self._font_presets) if preset[1]),
                0,
            )
            self._apply_font(self._font_presets[self._font_index])
        self.toast.set_lyrics_offset(self._lyrics_offset)
        self._apply_preferences()
        self._refresh_help_status()

        self.bridge.message.connect(self._show_message)
        self.bridge.track.connect(self._track_changed)
        self.bridge.lyrics.connect(self._lyrics_ready)
        self.bridge.toggle_help.connect(self._toggle_help)
        self.bridge.close_help.connect(self._close_help)
        self.bridge.toggle_notifications.connect(self._toggle_notifications)
        self.bridge.toggle_karaoke.connect(self._toggle_karaoke)
        self.bridge.cycle_font.connect(self.cycle_font)
        self.bridge.lyrics_offset.connect(self.adjust_lyrics_offset)
        self.bridge.toggle_edit_mode.connect(self._toggle_edit_mode)
        self.bridge.hotkey.connect(self._dispatch)
        self._game_timer = QTimer(self.qt_app)
        self._game_timer.setInterval(200)
        self._game_timer.timeout.connect(self._check_game_mode)

    def start(self) -> None:
        self.media.start()
        self._switch_hotkey_mode(foreground_prefers_passthrough())
        registered = (
            self.game_hotkeys.registered_count
            if self._passthrough_active
            else self.hotkeys.registered_count
        )
        self.logger.info("Overlay started; %d/%d hotkeys registered", registered, len(HOTKEYS))
        self._build_tray()
        self._game_timer.start()
        if self._setting_bool("auto_karaoke", False):
            self._toggle_karaoke()
        if not self._setting_bool("first_run_complete", False):
            QTimer.singleShot(450, self._show_first_run)

    def _native_bindings(self) -> tuple[tuple[int, int, str], ...]:
        sequences = load_hotkey_sequences(self.settings)
        try:
            parsed = validate_hotkeys(sequences)
        except ValueError as error:
            self.logger.warning("Invalid saved hotkeys; defaults restored: %s", error)
            for canonical, sequence in default_hotkey_sequences().items():
                self.settings.setValue(f"hotkeys/{canonical}", sequence)
            parsed = validate_hotkeys(load_hotkey_sequences(self.settings))
        return tuple(
            (modifiers, virtual_key, ACTION_BY_HOTKEY[canonical])
            for modifiers, virtual_key, canonical in parsed
        )

    def _setting_bool(self, key: str, default: bool) -> bool:
        return str(self.settings.value(key, str(default).lower())).lower() in {"1", "true", "yes"}

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(str(resource_path("assets/app-icon.ico"))), self.qt_app)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        help_action = menu.addAction("Показать подсказку")
        help_action.triggered.connect(self._toggle_help)
        self._tray_karaoke_action = menu.addAction("Караоке")
        self._tray_karaoke_action.setCheckable(True)
        self._tray_karaoke_action.triggered.connect(self._toggle_karaoke)
        menu.addAction("Переместить и масштабировать", self._toggle_edit_mode)
        menu.addSeparator()
        menu.addAction("Настройки…", self._open_settings)
        menu.addAction("Запустить Яндекс Музыку", self._launch_yandex_music)
        menu.addSeparator()
        menu.addAction("Выход", self.qt_app.quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._toggle_help()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

    def _show_first_run(self) -> None:
        dialog = FirstRunWizard(self.settings, [item[0] for item in self._font_presets])
        if dialog.exec():
            self._after_settings_saved(dialog)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, [item[0] for item in self._font_presets])
        if dialog.exec():
            self._after_settings_saved(dialog)

    def _after_settings_saved(self, dialog: SettingsDialog) -> None:
        if dialog.reset_layout_requested:
            self._reset_layout()
        self._font_index = self._restore_font_index()
        self._apply_preferences()
        registered = self._reload_hotkeys()
        self.logger.info("Hotkeys reloaded; %d/%d registered", registered, len(HOTKEYS))
        message = "настройки применены" if registered == len(HOTKEYS) else "часть сочетаний занята"
        self.toast.display(OverlayMessage("Настройки", message, "settings"), notifications=True)

    def _apply_preferences(self) -> None:
        self._smart_visibility = self._setting_bool("smart_visibility", True)
        self._game_mode = self._setting_bool("game_mode", True)
        opacity = max(45, min(100, int(self.settings.value("panel_opacity", 82)))) / 100.0
        for window in (self.toast, self.toast.karaoke_hud, self.help):
            window.set_panel_opacity(opacity)
        self.toast.karaoke_hud.set_mode(str(self.settings.value("karaoke_mode", "full")))
        self.toast.karaoke_hud.set_fps(int(self.settings.value("karaoke_fps", 60)))
        self._lyrics_offset = normalize_lyrics_offset(
            self.settings.value("lyrics_offset", LYRICS_OFFSET_DEFAULT)
        )
        self.toast.set_lyrics_offset(self._lyrics_offset)
        self.help.set_hotkeys(load_hotkey_sequences(self.settings))
        selected = str(self.settings.value("font_preset", "Sour Gummy"))
        preset = next((item for item in self._font_presets if item[0] == selected), None)
        if preset:
            self._apply_font(preset)

    def _reset_layout(self) -> None:
        for name, window in (
            ("toast", self.toast),
            ("karaoke", self.toast.karaoke_hud),
            ("help", self.help),
        ):
            self.settings.remove(f"positions/{name}")
            self.settings.remove(f"scale/{name}")
            window.set_ui_scale(1.0)

    def _check_game_mode(self) -> None:
        game_foreground = foreground_prefers_passthrough()
        self._game_active = self._game_mode and game_foreground
        self._switch_hotkey_mode(game_foreground)

    def _start_passthrough_hotkeys(self) -> int:
        return self.game_hotkeys.restart(self._native_bindings())

    def _stop_passthrough_hotkeys(self) -> None:
        self.game_hotkeys.stop()

    def _switch_hotkey_mode(self, passthrough: bool) -> int:
        if passthrough == self._passthrough_active:
            return self.game_hotkeys.registered_count if passthrough else self.hotkeys.registered_count
        self.hotkeys.stop()
        self._stop_passthrough_hotkeys()
        self._passthrough_active = passthrough
        if passthrough:
            registered = self._start_passthrough_hotkeys()
            self.logger.info(
                "Hotkeys switched to game pass-through; %d/%d active; win32=%d",
                registered,
                len(HOTKEYS),
                self.game_hotkeys.last_error,
            )
            return registered
        registered = self.hotkeys.restart(self._native_bindings())
        self.logger.info("Hotkeys switched to desktop capture; %d/%d active", registered, len(HOTKEYS))
        return registered

    def _reload_hotkeys(self) -> int:
        if self._passthrough_active:
            return self.game_hotkeys.restart(self._native_bindings())
        return self.hotkeys.restart(self._native_bindings())

    def _dispatch(self, action: str) -> None:
        now = time.monotonic()
        debounce = 0.35 if action in {"show_help", "toggle_karaoke", "toggle_notifications"} else 0.09
        if now - self._last_action.get(action, 0.0) < debounce:
            return
        self._last_action[action] = now
        self.logger.info("Hotkey action: %s", action)
        if action == "show_help":
            self.bridge.toggle_help.emit()
        elif action == "toggle_notifications":
            self.bridge.toggle_notifications.emit()
        elif action == "toggle_karaoke":
            self.bridge.toggle_karaoke.emit()
        elif action == "launch":
            self._launch_yandex_music()
        elif action == "cycle_font":
            self.bridge.cycle_font.emit()
        elif action in {"lyrics_later", "lyrics_earlier"}:
            delta = -0.10 if action == "lyrics_later" else 0.10
            self.bridge.lyrics_offset.emit(delta)
        elif action == "toggle_edit_mode":
            self.bridge.toggle_edit_mode.emit()
        else:
            self.media.submit(action)

    def _available_font_presets(self) -> list[tuple[str, str | None]]:
        families = set(QFontDatabase.families())
        presets: list[tuple[str, str | None]] = []
        for label, aliases in FONT_PRESETS:
            family = next((alias for alias in aliases if alias in families), None)
            presets.append((label, family))
        return presets

    def _restore_font_index(self) -> int:
        saved = str(self.settings.value("font_preset", "Sour Gummy"))
        return next((i for i, item in enumerate(self._font_presets) if item[0] == saved), 0)

    def _apply_font(self, preset: tuple[str, str | None]) -> bool:
        label, family = preset
        if not family:
            return False
        self.toast.set_display_font(family)
        self.help.set_display_font(family)
        self.settings.setValue("font_preset", label)
        return True

    def _refresh_help_status(self) -> None:
        active = str(self.settings.value("font_preset", "Sour Gummy"))
        available = sum(1 for _, family in self._font_presets if family)
        self.help.set_status(active, self._lyrics_offset, available, len(self._font_presets))

    def cycle_font(self) -> None:
        self._font_index = (self._font_index + 1) % len(self._font_presets)
        preset = self._font_presets[self._font_index]
        message = (
            preset[0]
            if self._apply_font(preset)
            else f"{preset[0]} · добавьте файл в папку fonts"
        )
        self._refresh_help_status()
        self.toast.display(OverlayMessage("Шрифт интерфейса", message, "settings"), notifications=True)

    def adjust_lyrics_offset(self, delta: float) -> None:
        self._lyrics_offset = normalize_lyrics_offset(self._lyrics_offset + delta)
        self.settings.setValue("lyrics_offset", self._lyrics_offset)
        self.toast.set_lyrics_offset(self._lyrics_offset)
        self._refresh_help_status()
        sign = "+" if self._lyrics_offset >= 0 else ""
        self.toast.display(
            OverlayMessage("Синхронизация караоке", f"{sign}{self._lyrics_offset:.2f} с", "settings"),
            notifications=True,
        )

    def _toggle_help(self) -> None:
        self.help.toggle()
        if self.help.is_shown and self._escape_hotkey is None:
            self._escape_hotkey = keyboard.add_hotkey(
                "esc", self.bridge.close_help.emit, suppress=True, trigger_on_release=False
            )
        elif not self.help.is_shown:
            self._remove_escape_hotkey()

    def _close_help(self) -> None:
        if self.help.is_shown:
            self.help.close_animated()
        self._remove_escape_hotkey()

    def _remove_escape_hotkey(self) -> None:
        if self._escape_hotkey is not None:
            keyboard.remove_hotkey(self._escape_hotkey)
            self._escape_hotkey = None

    def _toggle_edit_mode(self) -> None:
        self._edit_mode = not self._edit_mode
        if self._edit_mode:
            self._help_before_edit = self.help.is_shown
            if not self.help.is_shown:
                self.help.toggle()
        self.toast.set_edit_mode(self._edit_mode)
        self.help.set_edit_mode(self._edit_mode)
        if not self._edit_mode and not self._help_before_edit and self.help.is_shown:
            self.help.close_animated()
        status = (
            "перетащите HUD, караоке и подсказку мышью"
            if self._edit_mode
            else "все позиции сохранены"
        )
        self.toast.display(OverlayMessage("Режим перемещения", status, "settings"), notifications=True)

    def _launch_yandex_music(self) -> None:
        executable = find_yandex_music()
        if not executable:
            self.bridge.message.emit(OverlayMessage("Яндекс Музыка", "Приложение не найдено", "error"))
            return
        try:
            subprocess.Popen([str(executable)], close_fds=True)
            self.bridge.message.emit(OverlayMessage("Яндекс Музыка", "Запуск…", "settings"))
        except OSError:
            self.bridge.message.emit(OverlayMessage("Яндекс Музыка", "Не удалось запустить", "error"))

    def _show_message(self, message: OverlayMessage) -> None:
        if (
            self._smart_visibility
            and self._game_active
            and message.kind == "track"
        ):
            return
        self.toast.display(message, notifications=self.notifications)

    def _track_changed(self, state: TrackState) -> None:
        previous_key = (
            f"{self.current_track.artist}\0{self.current_track.title}"
            if self.current_track.title
            else ""
        )
        self.current_track = state
        self.toast.update_track(state)
        if self.tray:
            tooltip = f"{state.title} — {state.artist}" if state.artist else APP_NAME
            self.tray.setToolTip(tooltip[:120])
        key = f"{state.artist}\0{state.title}" if state.title else ""
        if key and key != previous_key:
            self.toast.prepare_lyrics(key)
        self._request_lyrics(state)

    def _toggle_notifications(self) -> None:
        self.notifications = not self.notifications
        status = "включены" if self.notifications else "выключены"
        self.toast.display(
            OverlayMessage("Уведомления", status, "settings"),
            notifications=True,
        )

    def _toggle_karaoke(self) -> None:
        self.karaoke = not self.karaoke
        self.toast.set_karaoke(self.karaoke)
        if self._tray_karaoke_action:
            self._tray_karaoke_action.setChecked(self.karaoke)
        if self.karaoke:
            self._request_lyrics(self.current_track, force=True)

    def _request_lyrics(self, state: TrackState, *, force: bool = False) -> None:
        if not state.title or not state.artist:
            return
        key = f"{state.artist}\0{state.title}"
        if not force and key == self._lyrics_key:
            return
        self._lyrics_key = key
        self.lyrics.request(
            state.artist,
            state.title,
            self.bridge.lyrics.emit,
            duration=state.duration,
            album=state.album,
            refresh=force,
        )

    def _lyrics_ready(self, key: str, lines: list[LyricLine]) -> None:
        if key == self._lyrics_key:
            self.toast.set_lyrics(key, lines)

    def close(self) -> None:
        self.logger.info("Overlay stopping")
        self._remove_escape_hotkey()
        self.hotkeys.stop()
        self._stop_passthrough_hotkeys()
        if self.tray:
            self.tray.hide()
        self.lyrics.close()
        self.media.stop()


def run() -> int:
    logger = _configure_logging()
    instance = SingleInstance(MUTEX_NAME)
    if instance.already_running:
        return 0

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setQuitOnLastWindowClosed(False)
    font_ids = [
        QFontDatabase.addApplicationFont(
            str(resource_path("assets/fonts/WixMadeforDisplay-Variable.ttf"))
        ),
        QFontDatabase.addApplicationFont(
            str(resource_path("assets/fonts/WixMadeforText-Variable.ttf"))
        ),
        QFontDatabase.addApplicationFont(
            str(resource_path("assets/fonts/SourGummy-Variable.ttf"))
        ),
    ]
    optional_font_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "YandexMusicGameOverlay" / "fonts"
    optional_font_dir.mkdir(parents=True, exist_ok=True)
    for font_path in (*optional_font_dir.glob("*.ttf"), *optional_font_dir.glob("*.otf")):
        QFontDatabase.addApplicationFont(str(font_path))
    text_id = font_ids[1]
    family = QFontDatabase.applicationFontFamilies(text_id)[0] if text_id >= 0 else FONT_FAMILY
    qt_app.setFont(QFont(family, 10))
    overlay = OverlayApplication(qt_app, logger)

    def report_exception(exc_type, exc_value, traceback) -> None:
        logger.exception("Unhandled exception", exc_info=(exc_type, exc_value, traceback))

    sys.excepthook = report_exception
    qt_app.aboutToQuit.connect(overlay.close)
    try:
        overlay.start()
        return qt_app.exec()
    finally:
        instance.close()
