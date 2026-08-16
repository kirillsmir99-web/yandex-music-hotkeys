import ctypes

import keyboard
from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ym_overlay.windows import foreground_is_fullscreen


class FullscreenKeyProbe(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.received = 0
        self.received_plain_tab = 0
        self.received_shift_down = 0
        self.received_shift_up = 0
        self.setWindowTitle("Yandex Music Overlay pass-through test")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background: #080a10; color: white; font-size: 24px;")
        layout = QVBoxLayout(self)
        label = QLabel("Проверка передачи Shift + Tab в полноэкранное приложение…")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and event.key() in {
            Qt.Key.Key_Tab,
            Qt.Key.Key_Backtab,
        }:
            self.received += 1
            if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.received_plain_tab += 1
        if event.type() in {QEvent.Type.KeyPress, QEvent.Type.KeyRelease} and event.key() == Qt.Key.Key_Shift:
            if event.type() == QEvent.Type.KeyPress:
                self.received_shift_down += 1
            elif event.type() == QEvent.Type.KeyRelease:
                self.received_shift_up += 1
        return super().eventFilter(watched, event)


def send_shift_tab() -> None:
    keyboard.send("shift+tab")


def send_plain_tab() -> None:
    keyboard.send("tab")


def send_bare_shift() -> None:
    keyboard.press("shift")
    QTimer.singleShot(70, lambda: keyboard.release("shift"))


def focus_probe(probe: QWidget) -> None:
    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    if foreground_thread:
        user32.AttachThreadInput(current_thread, foreground_thread, True)
    probe.raise_()
    probe.activateWindow()
    probe.setFocus()
    user32.BringWindowToTop(int(probe.winId()))
    user32.SetWindowPos(int(probe.winId()), -1, 0, 0, 0, 0, 0x0003 | 0x0040)
    user32.SetForegroundWindow(int(probe.winId()))
    user32.SwitchToThisWindow(int(probe.winId()), True)
    user32.SetFocus(int(probe.winId()))
    if foreground_thread:
        user32.AttachThreadInput(current_thread, foreground_thread, False)


def main() -> int:
    app = QApplication([])
    probe = FullscreenKeyProbe()
    app.installEventFilter(probe)
    probe.showFullScreen()
    focus_probe(probe)
    QTimer.singleShot(120, lambda: focus_probe(probe))
    QTimer.singleShot(320, lambda: focus_probe(probe))
    QTimer.singleShot(520, lambda: focus_probe(probe))
    QTimer.singleShot(650, lambda: print(f"detected_fullscreen={int(foreground_is_fullscreen())}"))
    QTimer.singleShot(700, send_plain_tab)
    QTimer.singleShot(900, send_bare_shift)
    QTimer.singleShot(1100, send_shift_tab)
    QTimer.singleShot(1700, app.quit)
    app.exec()
    print(f"fullscreen_received_tab_events={probe.received}")
    print(f"fullscreen_received_plain_tab={probe.received_plain_tab}")
    print(f"fullscreen_received_shift_down={probe.received_shift_down}")
    print(f"fullscreen_received_shift_up={probe.received_shift_up}")
    return 0 if all(
        (
            probe.received,
            probe.received_plain_tab,
            probe.received_shift_down,
            probe.received_shift_up,
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
