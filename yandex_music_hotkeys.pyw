# -*- coding: utf-8 -*-
import sys
import os
import time
import tempfile
import asyncio
import ctypes
import ctypes.wintypes
import subprocess

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtSignal, QObject, QRectF, QPoint,
    QParallelAnimationGroup, QAbstractNativeEventFilter
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QHBoxLayout, QVBoxLayout,
    QFrame, QProgressBar
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QPainterPath, QColor, QPen,
    QBrush, QLinearGradient, QFont, QRegion
)

try:
    from pycaw.pycaw import AudioUtilities
    HAS_PYCAW = True
except ImportError:
    HAS_PYCAW = False

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager
    )
    from winsdk.windows.storage.streams import DataReader
    HAS_WINSDK = True
except ImportError:
    HAS_WINSDK = False

YANDEX_PATH = (
    r"C:\Users\Administrator\AppData\Local\Programs"
    r"\YandexMusic\Яндекс Музыка.exe"
)
BROWSERS = [
    'chrome', 'msedge', 'firefox', 'zen',
    'opera', 'brave', 'vivaldi', 'browser', 'e6b9ae8c41bb8ce6'
]

def _cover_path() -> str:
    return os.path.join(tempfile.gettempdir(), f"ym_cover_{int(time.time()*1000)}.jpg")

COVER_TEMP = _cover_path()

# ══════════════════════════════════════════════════════════
#  Win32 API
# ══════════════════════════════════════════════════════════
user32  = ctypes.windll.user32
dwmapi  = ctypes.windll.dwmapi

HWND_TOPMOST      = -1
SWP_NOMOVE        = 0x0002
SWP_NOSIZE        = 0x0001
SWP_NOACTIVATE    = 0x0010
SW_SHOWNOACTIVATE = 4
GWL_EXSTYLE       = -20
WS_EX_NOACTIVATE  = 0x08000000
WS_EX_TOOLWINDOW  = 0x00000080


class _ACCENT(ctypes.Structure):
    _fields_ = [
        ("AccentState",   ctypes.c_uint),
        ("AccentFlags",   ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId",   ctypes.c_uint),
    ]


class _WCA_DATA(ctypes.Structure):
    _fields_ = [
        ("Attribute",  ctypes.c_int),
        ("Data",       ctypes.POINTER(_ACCENT)),
        ("SizeOfData", ctypes.c_size_t),
    ]


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("l", ctypes.c_int), ("r", ctypes.c_int),
        ("t", ctypes.c_int), ("b", ctypes.c_int),
    ]


gdi32 = ctypes.windll.gdi32


def _set_win32_round_rgn(hwnd: int, w: int, h: int, radius: int):
    """Обрезает HWND на уровне Win32 OS — DWM Acrylic blur не выходит за скругление."""
    try:
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2, radius * 2)
        if rgn:
            user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass


def _only_topmost(hwnd: int):
    """Только SetWindowPos — НЕ ShowWindow, чтобы не сбрасывать WS_EX_LAYERED."""
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def _show_noactivate(hwnd: int):
    """SetWindowPos + ShowWindow для первоначального показа без фокуса."""
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def _set_noactivate(hwnd: int):
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def _apply_acrylic(hwnd: int):
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        v = ctypes.c_int(2)  # DWMWCP_ROUND = 2
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                     ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass
    try:
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        v = ctypes.c_int(3)
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                                     ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass
    try:
        acc = _ACCENT()
        acc.AccentState   = 4   # ACCENT_ENABLE_ACRYLICBLURBEHIND
        acc.AccentFlags   = 2
        acc.GradientColor = 0x00000000
        dat = _WCA_DATA()
        dat.Attribute  = 19     # WCA_ACCENT_POLICY
        dat.Data       = ctypes.pointer(acc)
        dat.SizeOfData = ctypes.sizeof(acc)
        user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(dat))
    except Exception:
        pass
    try:
        m = _MARGINS(-1, -1, -1, -1)
        dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
#  Signals
# ══════════════════════════════════════════════════════════
class _Signals(QObject):
    toast   = pyqtSignal(str, str, str, str, int)
    help    = pyqtSignal()
    overlay = pyqtSignal()


SIG = _Signals()
notifications_enabled = True


# ══════════════════════════════════════════════════════════
#  CoverWidget — обложка со скруглёнными углами
# ══════════════════════════════════════════════════════════
class CoverWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix: QPixmap | None = None
        self.setAutoFillBackground(False)

    def set_pixmap(self, pix: QPixmap | None):
        self._pix = pix
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = QRectF(0, 0, w, h)
        path = QPainterPath()
        path.addRoundedRect(r, 11, 11)
        p.setClipPath(path)
        if self._pix and not self._pix.isNull():
            scaled = self._pix.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            x = (w - scaled.width())  // 2
            y = (h - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
        else:
            p.fillPath(path, QColor(38, 40, 54, 130))
        p.end()


# ══════════════════════════════════════════════════════════
#  GlassPill — кнопка-пилля для HelpHUD
# ══════════════════════════════════════════════════════════
class GlassPill(QWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self.setFixedHeight(36)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = QRectF(0, 0, w, h)
        path = QPainterPath()
        path.addRoundedRect(r, 11, 11)

        # Тёмная подложка пилли
        p.fillPath(path, QColor(18, 20, 28, 168))

        # Глянцевый блик сверху
        gloss = QPainterPath()
        gloss.addRoundedRect(QRectF(0, 0, w, h * 0.50), 11, 11)
        gloss_path = path.intersected(gloss)
        grad = QLinearGradient(0, 0, 0, h * 0.50)
        grad.setColorAt(0.0, QColor(255, 255, 255, 42))
        grad.setColorAt(0.6, QColor(255, 255, 255,  8))
        grad.setColorAt(1.0, QColor(255, 255, 255,  0))
        p.fillPath(gloss_path, QBrush(grad))

        # Тонкая граница
        pen = QPen(QColor(255, 255, 255, 52))
        pen.setWidthF(0.8)
        p.setPen(pen)
        p.drawPath(path)

        # Текст
        p.setPen(QColor(205, 208, 226, 210))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


# ══════════════════════════════════════════════════════════
#  GlassWidget — базовый класс для HUD-окон
# ══════════════════════════════════════════════════════════
class GlassWidget(QWidget):
    def __init__(self, radius: int = 20, no_act: bool = True):
        super().__init__()
        self._radius = radius
        self._no_act = no_act
        self._glass_applied = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.setStyleSheet("""
            * { background: transparent; border: none; }
            QProgressBar { background: rgba(255,255,255,0.10); border-radius: 2px; }
            QProgressBar::chunk { background: rgba(255,204,0,0.82); border-radius: 2px; }
        """)

    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        if self._no_act:
            _set_noactivate(hwnd)
            _show_noactivate(hwnd)
        else:
            _only_topmost(hwnd)
        if not self._glass_applied:
            _apply_acrylic(hwnd)
            self._glass_applied = True

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(0, 0, w, h, Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        r = QRectF(0.5, 0.5, w - 1, h - 1)
        path = QPainterPath()
        path.addRoundedRect(r, self._radius, self._radius)

        p.fillPath(path, QColor(10, 11, 16, 148))

        grad = QLinearGradient(0, 0, 0, h * 0.45)
        grad.setColorAt(0.0, QColor(255, 255, 255, 22))
        grad.setColorAt(1.0, QColor(255, 255, 255,  0))
        p.fillPath(path, QBrush(grad))

        pen = QPen(QColor(255, 255, 255, 42))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.drawPath(path)
        p.end()


# ══════════════════════════════════════════════════════════
#  ToastHUD — всплывающее уведомление
# ══════════════════════════════════════════════════════════
class ToastHUD(GlassWidget):
    def __init__(self):
        super().__init__(radius=18, no_act=True)
        self.resize(362, 86)

        self._anim_group: QParallelAnimationGroup | None = None

        # Таймер topmost: ТОЛЬКО SetWindowPos, НЕ ShowWindow
        # (ShowWindow сбрасывает WS_EX_LAYERED и ломает анимацию прозрачности)
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(80)
        self._topmost_timer.timeout.connect(
            lambda: _only_topmost(int(self.winId()))
        )

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._animate_out)

        self._build_ui()

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(15, 11, 15, 11)
        lay.setSpacing(13)

        self.cover = CoverWidget(self)
        self.cover.setFixedSize(62, 62)
        lay.addWidget(self.cover)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        self.badge = QLabel("ЯНДЕКС МУЗЫКА", self)
        self.badge.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self.badge.setStyleSheet("color: rgba(215,172,52,0.84); letter-spacing: 1.2px;")
        col.addWidget(self.badge)

        self.title_lbl = QLabel("", self)
        self.title_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        self.title_lbl.setStyleSheet("color: rgba(235,237,248,0.97);")
        col.addWidget(self.title_lbl)

        self.artist_lbl = QLabel("", self)
        self.artist_lbl.setFont(QFont("Segoe UI", 10))
        self.artist_lbl.setStyleSheet("color: rgba(162,165,185,0.78);")
        col.addWidget(self.artist_lbl)

        self.vol_bar = QProgressBar(self)
        self.vol_bar.setFixedHeight(4)
        self.vol_bar.setTextVisible(False)
        self.vol_bar.hide()
        col.addWidget(self.vol_bar)

        lay.addLayout(col)

    def _animate_in(self):
        if self._anim_group:
            self._anim_group.stop()

        scr   = QApplication.primaryScreen().geometry()
        end_x = scr.width() - self.width() - 24
        end_y = 44
        st_y  = end_y + 24

        self.move(end_x, st_y)
        self.setWindowOpacity(0.0)
        self.show()
        self._topmost_timer.start()

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(320)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutQuart)

        slide = QPropertyAnimation(self, b"pos")
        slide.setDuration(380)
        slide.setStartValue(QPoint(end_x, st_y))
        slide.setEndValue(QPoint(end_x, end_y))
        slide.setEasingCurve(QEasingCurve.Type.OutExpo)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(fade)
        self._anim_group.addAnimation(slide)
        self._anim_group.start()

    def _animate_out(self):
        if self._anim_group:
            self._anim_group.stop()
        self._topmost_timer.stop()
        cur = self.pos()

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(400)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InQuart)

        slide = QPropertyAnimation(self, b"pos")
        slide.setDuration(420)
        slide.setStartValue(cur)
        slide.setEndValue(QPoint(cur.x() + 30, cur.y()))
        slide.setEasingCurve(QEasingCurve.Type.InQuart)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(fade)
        self._anim_group.addAnimation(slide)
        self._anim_group.finished.connect(self.hide)
        self._anim_group.start()

    def _load_cover(self, path: str, retries: int = 5):
        if not path or not os.path.exists(path):
            self.cover.set_pixmap(None)
            return
        try:
            sz = os.path.getsize(path)
            if sz < 256:
                raise ValueError(f"too small: {sz}")
            pix = QPixmap(path)
            if pix.isNull():
                raise ValueError("null pixmap")
            self.cover.set_pixmap(pix)
        except Exception:
            if retries > 0:
                delay = (6 - retries) * 180
                QTimer.singleShot(delay,
                    lambda p=path, r=retries: self._load_cover(p, r - 1))
            else:
                self.cover.set_pixmap(None)

    def display(self, title: str, artist: str, cover: str, kind: str, vol: int):
        if not notifications_enabled:
            return
        self.badge.setText(
            "ГРОМКОСТЬ" if kind == "vol" else
            "ПЕРЕМОТКА" if kind == "seek" else
            "ЯНДЕКС МУЗЫКА"
        )
        self.title_lbl.setText(
            (f"{vol}%" if vol >= 0 else title)[:30]
            if kind == "vol" else title[:30]
        )
        self.artist_lbl.setText(artist[:36])

        if kind == "vol" and vol >= 0:
            self.vol_bar.setValue(vol)
            self.vol_bar.show()
        else:
            self.vol_bar.hide()

        self._load_cover(cover)
        self._animate_in()
        self._hide_timer.start(3100)


# ══════════════════════════════════════════════════════════
#  HelpHUD — меню горячих клавиш
# ══════════════════════════════════════════════════════════
class HelpHUD(GlassWidget):
    def __init__(self):
        super().__init__(radius=30, no_act=False)
        self.setWindowOpacity(0.0)
        self.resize(560, 530)

        self._anim: QParallelAnimationGroup | None = None
        self._visible = False      # наш собственный флаг видимости
        self._last_toggle = 0.0   # debounce

        # Таймер topmost: ТОЛЬКО SetWindowPos
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(100)
        self._topmost_timer.timeout.connect(
            lambda: _only_topmost(int(self.winId()))
        )

        self._build_ui()

    def paintEvent(self, event):
        """Кастомный фон: тёмная стеклянная панель + блик справа сверху."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Очистить весь холст до 0-alpha
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(0, 0, w, h, Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        r = QRectF(0.5, 0.5, w - 1, h - 1)
        path = QPainterPath()
        path.addRoundedRect(r, self._radius, self._radius)

        # Основной тёмный фон
        p.fillPath(path, QColor(8, 9, 14, 218))

        # Блик в правой верхней части
        gr = QLinearGradient(w * 0.3, 0, w * 0.95, h * 0.45)
        gr.setColorAt(0.0, QColor(255, 255, 255,   0))
        gr.setColorAt(0.55, QColor(55,  60,  82,  28))
        gr.setColorAt(1.0, QColor(  0,   0,   0,   0))
        p.fillPath(path, QBrush(gr))

        # Тонкая граница
        pen = QPen(QColor(255, 255, 255, 45))
        pen.setWidthF(0.9)
        p.setPen(pen)
        p.drawPath(path)

        # Верхний highlight
        hi = QRectF(0.5, 0.5, w - 1, self._radius * 2.2)
        hi_path = QPainterPath()
        hi_path.addRoundedRect(hi, self._radius, self._radius)
        top = path.intersected(hi_path)
        hg = QLinearGradient(0, 0, 0, self._radius * 2.2)
        hg.setColorAt(0.0, QColor(255, 255, 255, 14))
        hg.setColorAt(1.0, QColor(255, 255, 255,  0))
        p.fillPath(top, QBrush(hg))
        p.end()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 18)
        lay.setSpacing(0)

        icon_pill = GlassPill("YM", self)
        icon_pill.setFixedSize(46, 46)
        lay.addWidget(icon_pill, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addSpacing(18)

        BINDS = [
            ("Ctrl + Shift + X",         "Следующий трек"),
            ("Ctrl + Shift + Z",         "Предыдущий трек"),
            ("Ctrl + Shift + C",         "Пауза / Воспроизведение"),
            ("Ctrl + Shift + ← / →",    "Перемотка ±10 секунд"),
            ("Ctrl + Shift + ↑ / ↓",    "Громче / Тише"),
            ("Ctrl + Shift + M",         "Мут (Выключить звук)"),
            ("Ctrl + Shift + O",         "Вкл / Выкл уведомления"),
            ("Ctrl + Shift + K",         "Показать / Скрыть это окно"),
            ("Ctrl + Shift + H",         "Запустить Яндекс Музыку"),
        ]

        for key_txt, desc_txt in BINDS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            pill = GlassPill(key_txt, self)
            pill.setFixedWidth(200)
            row.addWidget(pill)
            d = QLabel(desc_txt, self)
            d.setFont(QFont("Segoe UI", 11))
            d.setStyleSheet("color: rgba(168,171,190,0.82); padding-left: 20px;")
            row.addWidget(d)
            row.addStretch()
            lay.addLayout(row)
            lay.addSpacing(7)

        lay.addStretch()

        div = QFrame(self)
        div.setFixedHeight(1)
        div.setStyleSheet("background: rgba(255,255,255,0.07);")
        lay.addWidget(div)
        lay.addSpacing(8)

        hint = QLabel("Нажмите ESC или Ctrl+Shift+K для закрытия", self)
        hint.setFont(QFont("Segoe UI", 9))
        hint.setStyleSheet("color: rgba(100,103,122,0.60);")
        lay.addWidget(hint, alignment=Qt.AlignmentFlag.AlignCenter)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_menu()

    def _animate_in(self):
        if self._anim:
            self._anim.stop()
        scr = QApplication.primaryScreen().geometry()
        cx = (scr.width()  - self.width())  // 2
        cy = (scr.height() - self.height()) // 2

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(380)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutQuart)

        slide = QPropertyAnimation(self, b"pos")
        slide.setDuration(440)
        slide.setStartValue(QPoint(cx, cy + 28))
        slide.setEndValue(QPoint(cx, cy))
        slide.setEasingCurve(QEasingCurve.Type.OutExpo)

        self._anim = QParallelAnimationGroup(self)
        self._anim.addAnimation(fade)
        self._anim.addAnimation(slide)
        self._anim.start()

    def _animate_out(self):
        if self._anim:
            self._anim.stop()
        cur = self.pos()

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(300)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InQuart)

        slide = QPropertyAnimation(self, b"pos")
        slide.setDuration(320)
        slide.setStartValue(cur)
        slide.setEndValue(QPoint(cur.x(), cur.y() + 18))
        slide.setEasingCurve(QEasingCurve.Type.InQuart)

        self._anim = QParallelAnimationGroup(self)
        self._anim.addAnimation(fade)
        self._anim.addAnimation(slide)
        self._anim.finished.connect(self._on_hidden)
        self._anim.start()

    def _on_hidden(self):
        self._topmost_timer.stop()
        self._visible = False
        self.hide()

    def open_menu(self):
        scr = QApplication.primaryScreen().geometry()
        cx = (scr.width()  - self.width())  // 2
        cy = (scr.height() - self.height()) // 2
        self.move(cx, cy + 28)
        self.setWindowOpacity(0.0)
        self.show()
        # Применяем acrylic и topmost
        hwnd = int(self.winId())
        _apply_acrylic(hwnd)
        _only_topmost(hwnd)
        self._topmost_timer.start()
        self._animate_in()
        self._visible = True

    def close_menu(self):
        self._animate_out()

    def toggle(self):
        # Debounce 700 мс — предотвращает двойное срабатывание hotkey
        now = time.monotonic()
        if now - self._last_toggle < 0.7:
            return
        self._last_toggle = now

        if self._visible:
            self.close_menu()
        else:
            self.open_menu()


# ══════════════════════════════════════════════════════════
#  Media / async
# ══════════════════════════════════════════════════════════
async def _get_cover(info) -> str | None:
    if not info or not info.thumbnail:
        return None
    try:
        s = await info.thumbnail.open_read_async()
        if s.size == 0:
            return None
        r = DataReader(s)
        await r.load_async(s.size)
        buf = bytearray(s.size)
        r.read_bytes(buf)
        global COVER_TEMP
        COVER_TEMP = _cover_path()
        with open(COVER_TEMP, "wb") as f:
            f.write(buf)
        return COVER_TEMP
    except Exception:
        return None


async def _get_session():
    m = await Manager.request_async()
    for s in m.get_sessions():
        if "yandex" in s.source_app_user_model_id.lower():
            return s
    for s in m.get_sessions():
        aid = s.source_app_user_model_id.lower()
        if not any(b in aid for b in BROWSERS):
            return s
    return None


async def _seek(delta: int):
    s = await _get_session()
    if not s:
        return
    tl  = s.get_timeline_properties()
    cur = tl.position.total_seconds()
    new = int(max(0, (cur + delta) * 10_000_000))
    await s.try_change_playback_position_async(new)
    sign = f"+{delta}" if delta > 0 else str(delta)
    SIG.toast.emit(f"Перемотка {sign} сек", f"Позиция: {int(cur+delta)} с",
                   COVER_TEMP, "seek", -1)


async def _media(action: str):
    s = await _get_session()
    if not s:
        SIG.toast.emit("Яндекс Музыка", "Нет трека", "", "track", -1)
        return
    if action == "next":         await s.try_skip_next_async()
    elif action == "prev":       await s.try_skip_previous_async()
    elif action == "play_pause": await s.try_toggle_play_pause_async()
    await asyncio.sleep(0.5)
    try:
        info  = await s.try_get_media_properties_async()
        cover = await _get_cover(info)
        artist = info.artist or "Яндекс Музыка"
        SIG.toast.emit(info.title, artist, cover or "", "track", -1)
    except Exception:
        pass


def _run(coro):
    def go():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    threading.Thread(target=go, daemon=True).start()


def _vol(delta):
    if not HAS_PYCAW:
        return
    try:
        for s in AudioUtilities.GetAllSessions():
            if not s.Process:
                continue
            try:
                name = s.Process.name().lower()
                exe  = s.Process.exe().lower()
            except Exception:
                continue
            if "yandex" not in name and "yandex" not in exe:
                continue
            v = s.SimpleAudioVolume
            if delta == "mute":
                muted = not v.GetMute()
                v.SetMute(muted, None)
                pct = 0 if muted else int(v.GetMasterVolume() * 100)
                SIG.toast.emit("Громкость",
                               "Выключен" if muted else "Включён",
                               COVER_TEMP, "vol", pct)
            else:
                nv = max(0.0, min(1.0, v.GetMasterVolume() + delta))
                v.SetMasterVolume(nv, None)
                SIG.toast.emit("Громкость", f"{int(nv*100)}%",
                               COVER_TEMP, "vol", int(nv * 100))
    except Exception:
        pass


def handle(action: str):
    if action == "vol_up":          _vol(0.05);    return
    if action == "vol_down":        _vol(-0.05);   return
    if action == "vol_mute":        _vol("mute");  return
    if action == "seek_left"  and HAS_WINSDK: _run(_seek(-10)); return
    if action == "seek_right" and HAS_WINSDK: _run(_seek(+10)); return
    if action == "toggle_overlay":  SIG.overlay.emit(); return
    if action == "show_help":       SIG.help.emit();    return
    if action == "launch":
        if os.path.exists(YANDEX_PATH):
            subprocess.Popen([YANDEX_PATH])
            SIG.toast.emit("Яндекс Музыка", "Запуск...", "", "track", -1)
        return
    if action in ("next", "prev", "play_pause") and HAS_WINSDK:
        _run(_media(action))


# ==========================================================
# Native Hotkeys (Zero Latency)
# ==========================================================
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004

VK_Z = 0x5A
VK_X = 0x58
VK_C = 0x43
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_UP = 0x26
VK_DOWN = 0x28
VK_M = 0x4D
VK_O = 0x4F
VK_K = 0x4B
VK_H = 0x48

HOTKEY_MAPPING = {
    1: "prev",
    2: "next",
    3: "play_pause",
    4: "seek_left",
    5: "seek_right",
    6: "vol_up",
    7: "vol_down",
    8: "vol_mute",
    9: "toggle_overlay",
    10: "show_help",
    11: "launch",
}

class HotkeyEventFilter(QAbstractNativeEventFilter):
    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG" or eventType == b"windows_dispatcher_MSG":
            msg = ctypes.wintypes.MSG.from_address(message.__int__())
            if msg.message == WM_HOTKEY:
                action = HOTKEY_MAPPING.get(msg.wParam)
                if action:
                    handle(action)
                return True, 0
        return False, 0

def _register_hotkeys():
    user32.RegisterHotKey(0, 1, MOD_CONTROL | MOD_SHIFT, VK_Z)
    user32.RegisterHotKey(0, 2, MOD_CONTROL | MOD_SHIFT, VK_X)
    user32.RegisterHotKey(0, 3, MOD_CONTROL | MOD_SHIFT, VK_C)
    user32.RegisterHotKey(0, 4, MOD_CONTROL | MOD_SHIFT, VK_LEFT)
    user32.RegisterHotKey(0, 5, MOD_CONTROL | MOD_SHIFT, VK_RIGHT)
    user32.RegisterHotKey(0, 6, MOD_CONTROL | MOD_SHIFT, VK_UP)
    user32.RegisterHotKey(0, 7, MOD_CONTROL | MOD_SHIFT, VK_DOWN)
    user32.RegisterHotKey(0, 8, MOD_CONTROL | MOD_SHIFT, VK_M)
    user32.RegisterHotKey(0, 9, MOD_CONTROL | MOD_SHIFT, VK_O)
    user32.RegisterHotKey(0, 10, MOD_CONTROL | MOD_SHIFT, VK_K)
    user32.RegisterHotKey(0, 11, MOD_CONTROL | MOD_SHIFT, VK_H)




def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    toast = ToastHUD()
    hlp   = HelpHUD()

    SIG.toast.connect(lambda t, a, c, k, v: toast.display(t, a, c, k, v))
    SIG.help.connect(hlp.toggle)

    def _toggle_overlay():
        global notifications_enabled
        notifications_enabled = not notifications_enabled
        msg = "Включены" if notifications_enabled else "Выключены"
        toast.display("Уведомления", msg, COVER_TEMP, "vol", -1)

    SIG.overlay.connect(_toggle_overlay)

    app.installNativeEventFilter(HotkeyEventFilter())
    _register_hotkeys()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
