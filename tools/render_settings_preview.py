from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from ym_overlay.settings_ui import FirstRunWizard, SettingsDialog


def main() -> int:
    app = QApplication([])
    settings = QSettings("BRAT12344321-preview", "YandexMusicGameOverlay")
    fonts = ["Grechka SHA", "Boingster", "Grunge SHA", "Sour Gummy"]
    output = Path(__file__).parents[1] / "artifacts"
    output.mkdir(exist_ok=True)

    first_run = FirstRunWizard(settings, fonts)
    first_run.show()
    for index, name in enumerate(("interface", "karaoke", "hotkeys")):
        first_run.tabs.setCurrentIndex(index)
        app.processEvents()
        first_run.grab().save(str(output / f"first-run-{name}-preview.png"))
    first_run.close()

    dialog = SettingsDialog(settings, fonts)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / "settings-preview.png"))
    dialog.close()
    settings.clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
