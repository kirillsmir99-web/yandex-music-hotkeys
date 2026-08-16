from pathlib import Path

from PyQt6.QtGui import QGuiApplication


def main() -> int:
    _app = QGuiApplication([])
    screen = QGuiApplication.primaryScreen()
    if not screen:
        raise RuntimeError("No screen available")
    geometry = screen.geometry()
    width, height = 430, 104
    x = geometry.width() - width - 28
    y = 42
    image = screen.grabWindow(0, x, y, width, height)
    target = Path(__file__).parents[1] / "artifacts" / "live-toast.png"
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"Unable to save {target}")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
