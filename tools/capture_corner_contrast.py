from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from ym_overlay.models import OverlayMessage
from ym_overlay.ui import ToastHUD


class ContrastBackground(QWidget):
    def __init__(self, x: int, y: int) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setGeometry(x, y, 470, 144)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        colors = (QColor(240, 45, 95), QColor(20, 205, 220))
        tile = 18
        for y in range(0, self.height(), tile):
            for x in range(0, self.width(), tile):
                painter.fillRect(x, y, tile, tile, colors[(x // tile + y // tile) % 2])


def main() -> int:
    app = QApplication([])
    x, y = 1450, 245
    background = ContrastBackground(x, y)
    background.show()
    toast = ToastHUD(lambda: 0.0)
    toast.display(OverlayMessage("Проверка углов", "Физическая маска HWND", "settings"), notifications=True)

    def capture() -> None:
        points = tuple(
            f"{px},{py}={int(toast.mask().contains(QPoint(px, py)))}"
            for px, py in ((2, 2), (10, 10), (20, 20), (30, 5), (5, 30))
        )
        print(f"toast_geometry={toast.x()},{toast.y()} {toast.width()}x{toast.height()}")
        print(f"toast_mask={' '.join(points)}")
        screen = QGuiApplication.primaryScreen()
        if screen:
            target = Path(__file__).parents[1] / "artifacts" / "corner-contrast.png"
            screen.grabWindow(0, x, y, 470, 144).save(str(target), "PNG")
            print(target)
        app.quit()

    QTimer.singleShot(700, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
