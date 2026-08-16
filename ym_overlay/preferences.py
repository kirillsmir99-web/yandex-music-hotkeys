from __future__ import annotations

from PyQt6.QtCore import QSettings

from .config import HOTKEYS

KARAOKE_MODES: tuple[tuple[str, str], ...] = (
    ("full", "Три строки"),
    ("two_line", "Текущая + следующая"),
    ("single", "Одна строка"),
    ("words", "Только активные слова"),
)

LYRICS_OFFSET_DEFAULT = 0.15
LYRICS_OFFSET_LIMIT = 1.5


def normalize_lyrics_offset(value: object, *, reset_legacy_extreme: bool = False) -> float:
    """Return a safe karaoke timing correction in seconds."""
    try:
        offset = float(value)
    except (TypeError, ValueError):
        return LYRICS_OFFSET_DEFAULT
    if reset_legacy_extreme and abs(offset) >= 2.5:
        return LYRICS_OFFSET_DEFAULT
    return round(max(-LYRICS_OFFSET_LIMIT, min(LYRICS_OFFSET_LIMIT, offset)), 2)


def default_hotkey_sequences() -> dict[str, str]:
    return {
        canonical: display.replace("←", "Left").replace("→", "Right").replace("↑", "Up").replace("↓", "Down")
        for canonical, display, _ in HOTKEYS
    }


def load_hotkey_sequences(settings: QSettings) -> dict[str, str]:
    defaults = default_hotkey_sequences()
    return {
        canonical: str(settings.value(f"hotkeys/{canonical}", defaults[canonical]))
        for canonical, _, _ in HOTKEYS
    }


def sequence_to_native(sequence: str) -> tuple[int, int]:
    """Convert a portable Qt key sequence into RegisterHotKey modifiers and VK."""
    parts = [part.strip() for part in sequence.replace(" ", "").split("+") if part.strip()]
    if not parts:
        raise ValueError("Пустое сочетание")
    modifiers = 0
    aliases = {"CONTROL": "CTRL", "CMD": "META", "WIN": "META"}
    while parts and aliases.get(parts[0].upper(), parts[0].upper()) in {
        "CTRL",
        "SHIFT",
        "ALT",
        "META",
    }:
        raw_modifier = parts.pop(0).upper()
        modifier = aliases.get(raw_modifier, raw_modifier)
        modifiers |= {"CTRL": 0x0002, "SHIFT": 0x0004, "ALT": 0x0001, "META": 0x0008}[
            modifier
        ]
    if len(parts) != 1 or modifiers == 0:
        raise ValueError("Нужна одна клавиша и хотя бы один модификатор")
    key = parts[0].upper()
    if len(key) == 1 and "A" <= key <= "Z":
        return modifiers, ord(key)
    if len(key) == 1 and "0" <= key <= "9":
        return modifiers, ord(key)
    named = {
        "TAB": 0x09,
        "SPACE": 0x20,
        "LEFT": 0x25,
        "←": 0x25,
        "UP": 0x26,
        "↑": 0x26,
        "RIGHT": 0x27,
        "→": 0x27,
        "DOWN": 0x28,
        "↓": 0x28,
        "HOME": 0x24,
        "END": 0x23,
        "PAGEUP": 0x21,
        "PAGEDOWN": 0x22,
        "[": 0xDB,
        "]": 0xDD,
    }
    if key in named:
        return modifiers, named[key]
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return modifiers, 0x6F + int(key[1:])
    raise ValueError(f"Клавиша {parts[0]} не поддерживается")


def validate_hotkeys(sequences: dict[str, str]) -> tuple[tuple[int, int, str], ...]:
    result = []
    occupied: dict[tuple[int, int], str] = {}
    for canonical, sequence in sequences.items():
        native = sequence_to_native(sequence)
        if native in occupied:
            raise ValueError(f"Конфликт: {sequence} уже используется для {occupied[native]}")
        occupied[native] = canonical
        result.append((*native, canonical))
    return tuple(result)


def sequence_to_keyboard(sequence: str) -> str:
    """Convert portable Qt spelling to the non-suppressing keyboard hook spelling."""
    normalized = sequence.casefold().replace(" ", "")
    parts = normalized.split("+")
    aliases = {
        "meta": "windows",
        "win": "windows",
        "pageup": "page up",
        "pagedown": "page down",
    }
    return "+".join(aliases.get(part, part) for part in parts)
