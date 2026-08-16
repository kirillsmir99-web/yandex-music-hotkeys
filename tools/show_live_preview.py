from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from ym_overlay.models import OverlayMessage
from ym_overlay.ui import ToastHUD


def main() -> int:
    app = QApplication([])
    toast = ToastHUD(lambda: 0.0)
    toast.display(
        OverlayMessage("After Hours", "The Weeknd", "track"),
        notifications=True,
    )
    QTimer.singleShot(8_000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

