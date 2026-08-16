import sys
from pathlib import Path

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import ym_overlay.windows as windows
from ym_overlay.config import resource_path
from ym_overlay.lyrics import LyricLine
from ym_overlay.models import OverlayMessage
from ym_overlay.ui import HelpHUD, ToastHUD


def main() -> int:
    windows.IS_WINDOWS = False
    app = QApplication(sys.argv)
    QFontDatabase.addApplicationFont(
        str(resource_path("assets/fonts/WixMadeforDisplay-Variable.ttf"))
    )
    QFontDatabase.addApplicationFont(
        str(resource_path("assets/fonts/WixMadeforText-Variable.ttf"))
    )
    output = Path(__file__).parents[1] / "artifacts"
    output.mkdir(exist_ok=True)

    position = [0.0]
    toast = ToastHUD(lambda: position[0])
    toast.display(
        OverlayMessage("Город засыпает", "Исполнитель · Новый трек", "track"),
        notifications=True,
    )
    app.processEvents()
    toast.grab().save(str(output / "toast-preview.png"))

    position[0] = 2.2
    toast.set_karaoke(True)
    toast.set_lyrics(
        "preview",
        [
            LyricLine(0.0, "Город засыпает"),
            LyricLine(1.3, "Ля ля ля тополя"),
            LyricLine(4.5, "Следующая строка песни"),
        ],
    )
    toast.karaoke_hud._sync_lyrics()
    QTest.qWait(450)
    toast.karaoke_hud.grab().save(str(output / "karaoke-preview.png"))

    help_hud = HelpHUD()
    help_hud.toggle()
    app.processEvents()
    help_hud.grab().save(str(output / "help-preview.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
