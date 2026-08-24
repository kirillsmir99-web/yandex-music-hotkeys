from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget


class AnimatedBackground(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.phase = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry() if screen else self.geometry()
        self.setGeometry(geometry.width() - 478, 22, 470, 144)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.advance)
        self._timer.start(700)

    def advance(self) -> None:
        self.phase = not self.phase
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        colors = (
            (QColor(235, 48, 96), QColor(26, 190, 210))
            if self.phase
            else (QColor(65, 85, 235), QColor(250, 165, 24))
        )
        tile = 36
        for y in range(0, self.height(), tile):
            for x in range(0, self.width(), tile):
                painter.fillRect(x, y, tile, tile, colors[(x // tile + y // tile) % 2])


def main() -> int:
    app = QApplication([])
    window = AnimatedBackground()
    window.show()
    QTimer.singleShot(7_000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
