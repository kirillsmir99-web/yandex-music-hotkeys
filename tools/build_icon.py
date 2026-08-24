from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication, QIcon


def main() -> int:
    _app = QGuiApplication([])
    root = Path(__file__).parents[1]
    source = str(root / "assets" / "app-icon.svg")
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QIcon(source).pixmap(QSize(size, size))
        icon.addPixmap(pixmap)
    target = root / "assets" / "app-icon.ico"
    if not icon.pixmap(QSize(256, 256)).save(str(target), "ICO"):
        raise RuntimeError(f"Не удалось создать {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
