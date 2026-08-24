from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import QSettings

from .config import APP_ID, AUTHOR

LEGACY_APP_ID = "YandexMusicGameOverlay"
MIGRATION_KEY = "migration/elarion_music_control_4"


def app_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    target = root / APP_ID
    target.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_files(root / LEGACY_APP_ID, target)
    return target


def create_settings() -> QSettings:
    current = QSettings(AUTHOR, APP_ID)
    if current.value(MIGRATION_KEY, False, type=bool):
        return current
    legacy = QSettings(AUTHOR, LEGACY_APP_ID)
    if not current.allKeys():
        for key in legacy.allKeys():
            current.setValue(key, legacy.value(key))
    current.setValue(MIGRATION_KEY, True)
    current.sync()
    return current


def _migrate_legacy_files(legacy: Path, target: Path) -> None:
    if not legacy.is_dir() or legacy.resolve() == target.resolve():
        return
    source_cache = legacy / "lyrics-cache-v2.json"
    target_cache = target / "lyrics-cache-v2.json"
    if source_cache.is_file() and not target_cache.exists():
        shutil.copy2(source_cache, target_cache)
    old_fonts = legacy / "fonts"
    new_fonts = target / "fonts"
    if old_fonts.is_dir() and not new_fonts.exists():
        shutil.copytree(old_fonts, new_fonts)
