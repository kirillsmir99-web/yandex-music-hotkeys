from datetime import UTC, datetime, timedelta

from ym_overlay.config import AUTHOR, HOTKEYS, NATIVE_HOTKEYS
from ym_overlay.lyrics import LyricLine, LyricsService, estimate_plain_lyrics, parse_lrc
from ym_overlay.media import MediaController, session_score
from ym_overlay.preferences import (
    LYRICS_OFFSET_DEFAULT,
    default_hotkey_sequences,
    normalize_lyrics_offset,
    sequence_to_keyboard,
    sequence_to_native,
    validate_hotkeys,
)
from ym_overlay.windows import PassthroughHotkeyManager


def test_yandex_session_has_priority() -> None:
    assert session_score("YandexMusic.Desktop") > session_score("Spotify.exe")
    assert session_score("YandexMusic.Desktop") > session_score("chrome.exe")
    assert session_score("chrome.exe") < 0


def test_parse_lrc_supports_multiple_timestamps() -> None:
    assert parse_lrc("[00:01.50][00:04.00]Строка\n[00:02.00]Вторая") == [
        LyricLine(1.5, "Строка"),
        LyricLine(2.0, "Вторая"),
        LyricLine(4.0, "Строка"),
    ]


def test_parse_lrc_supports_word_timestamps() -> None:
    line = parse_lrc("[00:01.00]<00:01.00>ля <00:01.40>тополя")[0]
    assert line.text == "ля тополя"
    assert [(word.start, word.text) for word in line.words] == [(1.0, "ля"), (1.4, "тополя")]


def test_public_metadata_and_hotkeys() -> None:
    assert AUTHOR == "BRAT12344321"
    assert len(HOTKEYS) == 16
    assert len(NATIVE_HOTKEYS) == 16
    assert NATIVE_HOTKEYS["shift+tab"] == (0x0004, 0x09)
    assert NATIVE_HOTKEYS["ctrl+shift+tab"] == (0x0006, 0x09)
    assert len({item[0] for item in HOTKEYS}) == len(HOTKEYS)


def test_hotkey_parser_accepts_modifiers_and_named_keys() -> None:
    assert sequence_to_native("Ctrl+Shift+X") == (0x0006, 0x58)
    assert sequence_to_native("Shift+Tab") == (0x0004, 0x09)
    assert sequence_to_native("Alt+F12") == (0x0001, 0x7B)
    assert sequence_to_native("Win+Left") == (0x0008, 0x25)
    assert sequence_to_keyboard("Ctrl + Shift + Tab") == "ctrl+shift+tab"
    assert sequence_to_keyboard("Meta+PageUp") == "windows+page up"


def test_default_hotkeys_are_valid_and_duplicates_are_rejected() -> None:
    defaults = default_hotkey_sequences()
    assert len(validate_hotkeys(defaults)) == len(HOTKEYS)
    defaults["ctrl+shift+z"] = defaults["ctrl+shift+x"]
    try:
        validate_hotkeys(defaults)
    except ValueError as error:
        assert "Конфликт" in str(error)
    else:
        raise AssertionError("Duplicate hotkey was accepted")


def test_passthrough_modifier_tracking_does_not_transform_keys() -> None:
    manager = PassthroughHotkeyManager((), lambda _: None)
    manager._down_keys.update({0xA0, 0xA2})
    assert manager._current_modifiers() == 0x0006
    manager._down_keys.remove(0xA0)
    assert manager._current_modifiers() == 0x0002


def test_timeline_position_uses_last_updated_anchor() -> None:
    class Timeline:
        position = timedelta(seconds=12)
        end_time = timedelta(seconds=90)
        last_updated_time = datetime.now(UTC) - timedelta(seconds=8)

    position = MediaController._timeline_position(Timeline(), playing=True)
    assert 19.5 <= position <= 20.5


def test_timeline_position_does_not_advance_while_paused() -> None:
    class Timeline:
        position = timedelta(seconds=12)
        end_time = timedelta(seconds=90)
        last_updated_time = datetime.now(UTC) - timedelta(seconds=8)

    assert MediaController._timeline_position(Timeline(), playing=False) == 12


def test_lyrics_offset_is_clamped_and_legacy_extreme_is_reset() -> None:
    assert normalize_lyrics_offset(-3.0, reset_legacy_extreme=True) == LYRICS_OFFSET_DEFAULT
    assert normalize_lyrics_offset(2.0) == 1.5
    assert normalize_lyrics_offset(-2.0) == -1.5
    assert normalize_lyrics_offset("bad") == LYRICS_OFFSET_DEFAULT


def test_plain_lyrics_fallback_is_marked_and_timed() -> None:
    lines = estimate_plain_lyrics("Первая строка\n\nВторая строка", 30.0)
    assert [line.text for line in lines] == ["Первая строка", "Вторая строка"]
    assert all(line.estimated for line in lines)
    assert 0 < lines[0].start < lines[1].start < 30


def test_yandex_track_match_is_strict_and_uses_duration() -> None:
    candidates = [
        {
            "id": "wrong-version",
            "title": "Песня",
            "artists": [{"name": "Артист"}],
            "durationMs": 190_000,
        },
        {
            "id": "exact",
            "title": "Песня",
            "artists": [{"name": "Артист"}],
            "durationMs": 180_400,
        },
        {
            "id": "wrong-artist",
            "title": "Песня",
            "artists": [{"name": "Другой"}],
            "durationMs": 180_000,
        },
    ]
    selected = LyricsService._select_yandex_track(candidates, "Артист", "Песня", 180.0)
    assert selected["id"] == "exact"
