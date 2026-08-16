import ctypes
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from tools.capture_corner_contrast import ContrastBackground


def send_notification_hotkey() -> None:
    keybd_event = ctypes.windll.user32.keybd_event
    for key in (0x11, 0x10, 0x4F):
        keybd_event(key, 0, 0, 0)
    for key in (0x4F, 0x10, 0x11):
        keybd_event(key, 0, 2, 0)


def main() -> int:
    app = QApplication([])
    x, y = 1450, 245
    background = ContrastBackground(x, y)
    background.show()

    def capture() -> None:
        screen = QGuiApplication.primaryScreen()
        if screen:
            target = Path(__file__).parents[1] / "artifacts" / "installed-corners-3.5.4.png"
            screen.grabWindow(0, x, y, 470, 144).save(str(target), "PNG")
            print(target)
        app.quit()

    QTimer.singleShot(200, send_notification_hotkey)
    QTimer.singleShot(850, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
