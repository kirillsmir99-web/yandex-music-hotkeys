from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Yandex Music Game Overlay"
AUTHOR = "BRAT12344321"
MUTEX_NAME = "Local\\YandexMusicGameOverlay_BRAT_12344321"


@dataclass(frozen=True, slots=True)
class Theme:
    panel: tuple[int, int, int, int] = (2, 3, 7, 174)
    panel_deep: tuple[int, int, int, int] = (0, 1, 4, 198)
    text: tuple[int, int, int, int] = (244, 246, 255, 250)
    text_muted: tuple[int, int, int, int] = (174, 181, 204, 218)
    accent: tuple[int, int, int, int] = (255, 210, 55, 255)
    radius: int = 32


THEME = Theme()
FONT_FAMILY = "Wix Madefor Text"
DISPLAY_FONT_FAMILY = "Wix Madefor Display"


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parents[1]))
    return base / relative

HOTKEYS: tuple[tuple[str, str, str], ...] = (
    ("ctrl+shift+x", "Ctrl + Shift + X", "Следующий трек"),
    ("ctrl+shift+z", "Ctrl + Shift + Z", "Предыдущий трек"),
    ("ctrl+shift+c", "Ctrl + Shift + C", "Пауза / воспроизведение"),
    ("ctrl+shift+left", "Ctrl + Shift + ←", "Назад на 10 секунд"),
    ("ctrl+shift+right", "Ctrl + Shift + →", "Вперёд на 10 секунд"),
    ("ctrl+shift+up", "Ctrl + Shift + ↑", "Громче в микшере Windows"),
    ("ctrl+shift+down", "Ctrl + Shift + ↓", "Тише в микшере Windows"),
    ("ctrl+shift+m", "Ctrl + Shift + M", "Выключить / включить звук"),
    ("ctrl+shift+o", "Ctrl + Shift + O", "Уведомления"),
    ("ctrl+shift+l", "Ctrl + Shift + L", "Караоке"),
    ("ctrl+shift+k", "Ctrl + Shift + K", "Подсказка клавиш"),
    ("ctrl+shift+h", "Ctrl + Shift + H", "Запустить Яндекс Музыку"),
    ("ctrl+shift+f", "Ctrl + Shift + F", "Сменить шрифт интерфейса"),
    ("shift+tab", "Shift + Tab", "Текст караоке позже"),
    ("ctrl+shift+tab", "Ctrl + Shift + Tab", "Текст караоке раньше"),
    ("ctrl+shift+g", "Ctrl + Shift + G", "Переместить окна HUD"),
)

NATIVE_HOTKEYS: dict[str, tuple[int, int]] = {
    "ctrl+shift+x": (0x0006, 0x58),
    "ctrl+shift+z": (0x0006, 0x5A),
    "ctrl+shift+c": (0x0006, 0x43),
    "ctrl+shift+left": (0x0006, 0x25),
    "ctrl+shift+right": (0x0006, 0x27),
    "ctrl+shift+up": (0x0006, 0x26),
    "ctrl+shift+down": (0x0006, 0x28),
    "ctrl+shift+m": (0x0006, 0x4D),
    "ctrl+shift+o": (0x0006, 0x4F),
    "ctrl+shift+l": (0x0006, 0x4C),
    "ctrl+shift+k": (0x0006, 0x4B),
    "ctrl+shift+h": (0x0006, 0x48),
    "ctrl+shift+f": (0x0006, 0x46),
    "shift+tab": (0x0004, 0x09),
    "ctrl+shift+tab": (0x0006, 0x09),
    "ctrl+shift+g": (0x0006, 0x47),
}

FONT_PRESETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Grechka SHA", ("Grechka SHA", "Grechka")),
    ("Boingster", ("Boingster",)),
    ("Grunge SHA", ("Grunge SHA", "Grunge")),
    ("Sour Gummy", ("Sour Gummy", "Sour Gummy Black")),
)


def yandex_music_candidates() -> tuple[Path, ...]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    return (
        local / "Programs" / "YandexMusic" / "Яндекс Музыка.exe",
        local / "Yandex" / "YandexMusic" / "Яндекс Музыка.exe",
        local / "Programs" / "YandexMusic" / "Yandex Music.exe",
    )


def find_yandex_music() -> Path | None:
    return next((path for path in yandex_music_candidates() if path.is_file()), None)
