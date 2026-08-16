import sys
import time

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import ym_overlay.windows as windows
from ym_overlay.lyrics import LyricLine
from ym_overlay.models import OverlayMessage
from ym_overlay.ui import HelpHUD, ToastHUD


def main() -> int:
    windows.IS_WINDOWS = False
    app = QApplication(sys.argv)
    toast = ToastHUD(lambda: 0.0)
    toast.display(OverlayMessage("Первый трек", "Исполнитель"), notifications=True)
    QTest.qWait(500)
    toast.display(OverlayMessage("Второй трек", "Исполнитель"), notifications=True)
    QTest.qWait(700)
    assert toast.isVisible(), "Repeated notification closed itself"
    assert toast.windowOpacity() > 0.9, "Repeated notification did not finish appearing"
    assert not toast.mask().contains(toast.rect().topLeft()), "Top-left corner is inside the window mask"
    karaoke = toast.karaoke_hud
    position = [0.0]
    karaoke._position_provider = lambda: position[0]
    karaoke.set_lyrics(
        "smoke",
        [LyricLine(index * 2.0, f"Строка {index}") for index in range(180)],
    )
    karaoke.set_playing(True)
    for mode, expected_height in (("full", 140), ("two_line", 116), ("single", 94), ("words", 76)):
        karaoke.set_mode(mode)
        assert karaoke.height() == expected_height
    karaoke.set_ui_scale(1.25, save=False)
    assert karaoke.width() == 538 and karaoke.height() == 95
    assert not karaoke.mask().contains(karaoke.rect().topLeft())
    help_hud = HelpHUD()
    help_hud.toggle()
    QTest.qWait(50)
    assert not help_hud.mask().contains(help_hud.rect().topLeft())
    started = time.perf_counter()
    for tick in range(5000):
        position[0] = tick / 60
        karaoke._sync_lyrics()
    elapsed = time.perf_counter() - started
    assert elapsed < 0.35, f"Karaoke synchronization loop is too slow: {elapsed:.3f}s"
    toast.close()
    karaoke.close()
    help_hud.close()
    app.processEvents()
    print(f"ui_smoke=ok repeated_toast=visible rounded_mask=ok sync_5000={elapsed:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
