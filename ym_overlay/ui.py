from __future__ import annotations

from bisect import bisect_right

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSettings,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .config import AUTHOR, DISPLAY_FONT_FAMILY, FONT_FAMILY, HOTKEYS, THEME
from .lyrics import LyricLine
from .models import OverlayMessage, TrackState
from .preferences import LYRICS_OFFSET_DEFAULT, normalize_lyrics_offset
from .windows import active_screen_geometry, apply_overlay_style, apply_rounded_mask, raise_topmost


class UiBridge(QObject):
    message = pyqtSignal(object)
    track = pyqtSignal(object)
    lyrics = pyqtSignal(str, object)
    hotkey = pyqtSignal(str)
    toggle_help = pyqtSignal()
    toggle_notifications = pyqtSignal()
    toggle_karaoke = pyqtSignal()
    cycle_font = pyqtSignal()
    lyrics_offset = pyqtSignal(float)
    close_help = pyqtSignal()
    toggle_edit_mode = pyqtSignal()


class LiquidGlassWindow(QWidget):
    def __init__(self, radius: int, *, click_through: bool, position_key: str):
        super().__init__()
        self._radius = radius
        self._click_through = click_through
        self._position_key = position_key
        self._settings = QSettings("BRAT12344321", "YandexMusicGameOverlay")
        self._ui_scale = max(
            0.7,
            min(1.6, float(self._settings.value(f"scale/{position_key}", 1.0))),
        )
        self._base_size = QSize()
        self._font_sizes: list[tuple[QWidget, float]] = []
        self._fixed_dimensions: list[tuple[QWidget, int | None, int | None]] = []
        self._edit_mode = False
        self._drag_offset: QPoint | None = None
        self._native_ready = False
        self._live_backdrop = False
        self._backdrop = QPixmap()
        self._reveal = 1.0
        self._panel_opacity = 0.82
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, click_through)
        self.setStyleSheet("* { background: transparent; border: none; }")

    def finalize_scaling(self, width: int, height: int) -> None:
        self._base_size = QSize(width, height)
        self._font_sizes = [
            (child, child.font().pointSizeF())
            for child in self.findChildren(QWidget)
            if child.font().pointSizeF() > 0
        ]
        limit = 16_000_000
        self._fixed_dimensions = []
        for child in self.findChildren(QWidget):
            fixed_width = (
                child.minimumWidth()
                if child.minimumWidth() == child.maximumWidth() < limit
                else None
            )
            fixed_height = (
                child.minimumHeight()
                if child.minimumHeight() == child.maximumHeight() < limit
                else None
            )
            if fixed_width is not None or fixed_height is not None:
                self._fixed_dimensions.append((child, fixed_width, fixed_height))
        self._apply_ui_scale()

    def set_base_size(self, width: int, height: int) -> None:
        self._base_size = QSize(width, height)
        self._apply_ui_scale()

    def set_ui_scale(self, scale: float, *, save: bool = True) -> None:
        self._ui_scale = max(0.7, min(1.6, round(scale, 2)))
        if save:
            self._settings.setValue(f"scale/{self._position_key}", self._ui_scale)
        self._apply_ui_scale()

    def set_panel_opacity(self, opacity: float) -> None:
        self._panel_opacity = max(0.45, min(1.0, opacity))
        self.update()

    def _apply_ui_scale(self) -> None:
        if self._base_size.isEmpty():
            return
        for child, point_size in self._font_sizes:
            font = child.font()
            font.setPointSizeF(max(7.0, point_size * self._ui_scale))
            child.setFont(font)
        for child, fixed_width, fixed_height in self._fixed_dimensions:
            if fixed_width is not None:
                child.setFixedWidth(max(1, round(fixed_width * self._ui_scale)))
            if fixed_height is not None:
                child.setFixedHeight(max(1, round(fixed_height * self._ui_scale)))
        self.resize(
            max(1, round(self._base_size.width() * self._ui_scale)),
            max(1, round(self._base_size.height() * self._ui_scale)),
        )

    def wheelEvent(self, event) -> None:
        if self._edit_mode and event.angleDelta().y():
            step = 0.05 if event.angleDelta().y() > 0 else -0.05
            self.set_ui_scale(self._ui_scale + step)
            event.accept()
            return
        super().wheelEvent(event)

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._click_through = not enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        self.setCursor(
            Qt.CursorShape.SizeAllCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        if self._native_ready:
            self._live_backdrop = apply_overlay_style(
                self, click_through=not enabled, radius=self._radius
            )

    def saved_position(self, fallback: QPoint) -> QPoint:
        x = self._settings.value(f"positions/{self._position_key}/x")
        y = self._settings.value(f"positions/{self._position_key}/y")
        if x is None or y is None:
            return fallback
        point = QPoint(int(x), int(y))
        center = point + QPoint(self.width() // 2, self.height() // 2)
        if QGuiApplication.screenAt(center):
            return point
        return fallback

    def _save_position(self) -> None:
        self._settings.setValue(f"positions/{self._position_key}/x", self.x())
        self._settings.setValue(f"positions/{self._position_key}/y", self.y())

    def mousePressEvent(self, event) -> None:
        if self._edit_mode and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._edit_mode and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._edit_mode and self._drag_offset is not None:
            screen = (
                QGuiApplication.screenAt(self.frameGeometry().center())
                or QGuiApplication.primaryScreen()
            )
            if screen:
                area = screen.availableGeometry()
                self.move(
                    max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                    max(area.top(), min(self.y(), area.bottom() - self.height() + 1)),
                )
            self._drag_offset = None
            self._save_position()
            self.capture_backdrop()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:
        apply_rounded_mask(self, self._radius)
        if not self._native_ready:
            self._live_backdrop = apply_overlay_style(
                self,
                click_through=self._click_through,
                radius=self._radius,
            )
            self._native_ready = True
        apply_rounded_mask(self, self._radius)
        super().showEvent(event)
        raise_topmost(self)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        apply_rounded_mask(self, self._radius)

    def get_reveal(self) -> float:
        return self._reveal

    def set_reveal(self, value: float) -> None:
        self._reveal = max(0.0, min(1.0, value))
        self.update()

    reveal = pyqtProperty(float, get_reveal, set_reveal)

    def capture_backdrop(self) -> None:
        """Capture and soften only the pixels behind the final HUD rectangle."""
        if self._live_backdrop:
            self._backdrop = QPixmap()
            return
        center = QPoint(self.x() + self.width() // 2, self.y() + self.height() // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if not screen:
            self._backdrop = QPixmap()
            return
        geometry = screen.geometry()
        source = screen.grabWindow(
            0,
            self.x() - geometry.x(),
            self.y() - geometry.y(),
            self.width(),
            self.height(),
        )
        if source.isNull():
            self._backdrop = QPixmap()
            return
        small = source.scaled(
            max(1, self.width() // 14),
            max(1, self.height() // 14),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._backdrop = small.scaled(
            self.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(0.0, 0.0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(bounds, self._radius, self._radius)
        painter.setClipPath(path)

        if not self._backdrop.isNull():
            painter.drawPixmap(self.rect(), self._backdrop)

        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(
            0.0,
            QColor(*THEME.panel[:3], round(THEME.panel[3] * self._panel_opacity)),
        )
        base.setColorAt(0.52, QColor(1, 2, 6, round(184 * self._panel_opacity)))
        base.setColorAt(
            1.0,
            QColor(*THEME.panel_deep[:3], round(THEME.panel_deep[3] * self._panel_opacity)),
        )
        painter.fillPath(path, base)

        # The highlight travels once during reveal, then remains still at no CPU cost.
        highlight_x = self.width() * (-0.08 + 0.34 * self._reveal)
        bloom = QRadialGradient(highlight_x, -self.height() * 0.1, self.width() * 0.72)
        bloom.setColorAt(0.0, QColor(255, 255, 255, round(42 * self._reveal)))
        bloom.setColorAt(0.36, QColor(91, 112, 177, round(15 * self._reveal)))
        bloom.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, bloom)


class CoverArt(QWidget):
    def __init__(self, size: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pixmap = QPixmap()
        self._radius = max(12, size // 4)

    def set_bytes(self, data: bytes) -> None:
        pixmap = QPixmap()
        if data:
            pixmap.loadFromData(data)
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        painter.setClipPath(path)
        if self._pixmap.isNull():
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0.0, QColor(255, 214, 52))
            gradient.setColorAt(1.0, QColor(255, 116, 40))
            painter.fillRect(self.rect(), gradient)
            painter.setPen(QColor(24, 18, 12, 230))
            painter.setFont(QFont(DISPLAY_FONT_FAMILY, 18, QFont.Weight.Bold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "YM")
        else:
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, scaled)


class KaraokeLine(QWidget):
    """Paint a cached lyric layout with a smooth progress sweep."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        self._line: LyricLine | None = None
        self._line_end = 0.0
        self._position = 0.0
        self._word_ranges: list[tuple[str, float, float]] = []
        self._font_family = DISPLAY_FONT_FAMILY
        self._visual_scale = 1.0
        self._layout_cache = None
        self._transition = 1.0
        self._empty_text = "Синхронный текст не найден"
        self._animation = QPropertyAnimation(self, b"transition", self)
        self._animation.setDuration(170)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_transition(self) -> float:
        return self._transition

    def set_transition(self, value: float) -> None:
        self._transition = value
        self.update()

    transition = pyqtProperty(float, get_transition, set_transition)

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.update()

    def set_font_family(self, family: str) -> None:
        self._font_family = family
        self._layout_cache = None
        self.update()

    def set_visual_scale(self, scale: float) -> None:
        self._visual_scale = max(0.7, min(1.6, scale))
        self._layout_cache = None
        self.update()

    def set_line(self, line: LyricLine | None, line_end: float) -> None:
        if line == self._line and abs(line_end - self._line_end) < 0.01:
            return
        self._line = line
        self._line_end = max(line.start + 0.5, line_end) if line else 0.0
        self._word_ranges = self._build_word_ranges(line, self._line_end) if line else []
        self._layout_cache = None
        self._animation.stop()
        self._animation.setStartValue(0.55)
        self._animation.setEndValue(1.0)
        self._animation.start()
        self.update()

    def set_position(self, position: float) -> None:
        if abs(position - self._position) < 0.003:
            return
        self._position = position
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_cache = None

    def _layout(self):
        if self._layout_cache is not None:
            return self._layout_cache
        font = QFont(self._font_family, round(12 * self._visual_scale), QFont.Weight.DemiBold)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        metrics = QFontMetricsF(font)
        spacing = metrics.horizontalAdvance(" ") * 1.35
        widths = [metrics.horizontalAdvance(word) for word, _, _ in self._word_ranges]
        available = max(40.0, self.width() - 16.0)
        total = sum(widths) + spacing * max(0, len(widths) - 1)
        if total > available:
            font.setPointSizeF(max(9.0, 12.0 * available / total))
            metrics = QFontMetricsF(font)
            spacing = metrics.horizontalAdvance(" ") * 1.35
            widths = [metrics.horizontalAdvance(word) for word, _, _ in self._word_ranges]
            total = sum(widths) + spacing * max(0, len(widths) - 1)
        x = (self.width() - total) / 2.0
        baseline = self.height() / 2.0 + metrics.ascent() / 2.0 - 2.0
        paths = []
        for (word, _, _), width in zip(self._word_ranges, widths, strict=True):
            path = QPainterPath()
            path.addText(QPointF(x, baseline), font, word)
            paths.append(path)
            x += width + spacing
        self._layout_cache = (font, metrics, spacing, widths, total, paths)
        return self._layout_cache

    @staticmethod
    def _build_word_ranges(line: LyricLine, line_end: float) -> list[tuple[str, float, float]]:
        if line.words:
            ranges = []
            for index, word in enumerate(line.words):
                end = line.words[index + 1].start if index + 1 < len(line.words) else line_end
                ranges.append((word.text, word.start, max(word.start + 0.08, end)))
            return ranges

        words = line.text.split()
        if not words:
            return []
        weights = [max(1.0, len(word.strip(".,!?—-")) ** 0.72) for word in words]
        duration = max(0.5, line_end - line.start)
        unit = duration / sum(weights)
        cursor = line.start
        ranges = []
        for word, weight in zip(words, weights, strict=True):
            end = cursor + weight * unit
            ranges.append((word, cursor, end))
            cursor = end
        return ranges

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if not self._word_ranges:
            painter.setPen(QColor(255, 230, 122, 232))
            painter.setFont(QFont(FONT_FAMILY, 11, QFont.Weight.DemiBold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return

        _, _, _, widths, _, paths = self._layout()
        painter.setOpacity(0.72 + self._transition * 0.28)
        painter.translate(0.0, -(1.0 - self._transition) * 3.0)

        for (_, start, end), width, path in zip(
            self._word_ranges, widths, paths, strict=True
        ):
            active = start <= self._position < end
            passed = self._position >= end
            progress = max(0.0, min(1.0, (self._position - start) / max(0.08, end - start)))
            if active:
                color = QColor(181, 187, 207, 190)
            elif passed:
                color = QColor(244, 246, 255, 238)
            else:
                color = QColor(145, 153, 179, 164)
            painter.fillPath(path, color)
            if active:
                if progress >= 0.999:
                    painter.fillPath(path, QColor(255, 222, 84, 255))
                elif progress > 0.001:
                    left = path.boundingRect().left()
                    sweep = QLinearGradient(left, 0.0, left + width, 0.0)
                    edge_before = max(0.0, progress - 0.018)
                    edge_after = min(1.0, progress + 0.018)
                    sweep.setColorAt(0.0, QColor(255, 222, 84, 255))
                    sweep.setColorAt(edge_before, QColor(255, 222, 84, 255))
                    sweep.setColorAt(edge_after, QColor(255, 222, 84, 0))
                    sweep.setColorAt(1.0, QColor(255, 222, 84, 0))
                    painter.fillPath(path, sweep)


class KaraokeHUD(LiquidGlassWindow):
    """Independent movable lyric window with its own saved position."""

    def __init__(self, position_provider):
        super().__init__(THEME.radius, click_through=True, position_key="karaoke")
        self._position_provider = position_provider
        self._enabled = False
        self._lyrics: list[LyricLine] = []
        self._lyric_times: list[float] = []
        self._lyrics_key = ""
        self._lyrics_offset = LYRICS_OFFSET_DEFAULT
        self._last_lyric = -1
        self._last_position = -1.0
        self._mode = "full"
        self._fps = 60
        self._playing = False
        self.resize(430, 140)
        self.setWindowOpacity(1.0)
        self._build()
        self.finalize_scaling(430, 140)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._sync_lyrics)
        self.lyric_current.set_visual_scale(self._ui_scale)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(3)
        self.badge = QLabel("КАРАОКЕ · ЯНДЕКС МУЗЫКА")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFont(QFont(DISPLAY_FONT_FAMILY, 8, QFont.Weight.Bold))
        self.badge.setStyleSheet("color: rgba(255,218,74,235); letter-spacing: 1.2px;")
        self.lyric_prev = self._lyric_label()
        self.lyric_current = KaraokeLine(self)
        self.lyric_next = self._lyric_label()
        root.addWidget(self.badge)
        root.addWidget(self.lyric_prev)
        root.addWidget(self.lyric_current, 1)
        root.addWidget(self.lyric_next)

    @staticmethod
    def _lyric_label() -> QLabel:
        label = QLabel("")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setFont(QFont(FONT_FAMILY, 9))
        label.setStyleSheet("color: rgba(174,181,204,110);")
        return label

    def set_display_font(self, family: str) -> None:
        badge_font = self.badge.font()
        badge_font.setFamily(family)
        self.badge.setFont(badge_font)
        self.lyric_current.set_font_family(family)

    def set_ui_scale(self, scale: float, *, save: bool = True) -> None:
        super().set_ui_scale(scale, save=save)
        self.lyric_current.set_visual_scale(self._ui_scale)

    def set_mode(self, mode: str) -> None:
        if mode not in {"full", "two_line", "single", "words"}:
            mode = "full"
        self._mode = mode
        self.badge.setVisible(mode != "words")
        self.lyric_prev.setVisible(mode == "full")
        self.lyric_next.setVisible(mode in {"full", "two_line"})
        height = {"full": 140, "two_line": 116, "single": 94, "words": 76}[mode]
        self.set_base_size(430, height)

    def set_fps(self, fps: int) -> None:
        self._fps = fps if fps in {30, 60, 90} else 60
        self._refresh_timer_rate()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self._refresh_timer_rate()
        if self._enabled and not playing:
            self._sync_lyrics()

    def _refresh_timer_rate(self) -> None:
        interval = max(11, round(1000 / self._fps)) if self._playing else 250
        self._timer.setInterval(interval)

    def set_lyrics_offset(self, seconds: float) -> None:
        self._lyrics_offset = normalize_lyrics_offset(seconds)
        self._last_lyric = -1
        self._last_position = -1.0
        if self._enabled:
            self._sync_lyrics()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            self.move(self._target_position())
            self.lyric_current.set_line(None, 0.0)
            self._timer.start()
            self.show()
            raise_topmost(self)
        else:
            self._timer.stop()
            if not self._edit_mode:
                self.hide()

    def set_edit_mode(self, enabled: bool) -> None:
        super().set_edit_mode(enabled)
        if enabled:
            self.lyric_current.set_empty_text("ПЕРЕТАЩИТЕ ОКНО КАРАОКЕ")
            self.move(self._target_position())
            self.show()
            raise_topmost(self)
        elif not self._enabled:
            self.hide()

    def set_lyrics(self, key: str, lines: list[LyricLine]) -> None:
        self._lyrics_key = key
        self._lyrics = lines
        self._lyric_times = [line.start for line in lines]
        self.badge.setText(
            "ТЕКСТ · ПРИМЕРНАЯ СИНХРОНИЗАЦИЯ"
            if lines and any(line.estimated for line in lines)
            else f"ТЕКСТ · {lines[0].source.upper()}"
            if lines and lines[0].source
            else "КАРАОКЕ · ЯНДЕКС МУЗЫКА"
        )
        self._last_lyric = -1
        self._last_position = -1.0
        if not lines:
            self.lyric_prev.setText("")
            self.lyric_current.set_empty_text("Синхронный текст не найден")
            self.lyric_current.set_line(None, 0.0)
            self.lyric_next.setText("")

    def prepare_lyrics(self, key: str) -> None:
        if key == self._lyrics_key:
            return
        self._lyrics_key = key
        self._lyrics = []
        self._lyric_times = []
        self.badge.setText("КАРАОКЕ · ЯНДЕКС МУЗЫКА")
        self._last_lyric = -1
        self._last_position = -1.0
        self.lyric_prev.setText("")
        self.lyric_next.setText("")
        self.lyric_current.set_empty_text("Ищем синхронный текст…")
        self.lyric_current.set_line(None, 0.0)

    def _sync_lyrics(self) -> None:
        if not self._lyrics:
            return
        position = self._position_provider() + self._lyrics_offset
        index = self._last_lyric
        if index == -1:
            if position >= self._lyric_times[0]:
                index = bisect_right(self._lyric_times, position) - 1
        elif (
            index >= len(self._lyrics)
            or position < self._lyrics[index].start
            or (index + 1 < len(self._lyrics) and position >= self._lyrics[index + 1].start)
            or (self._last_position >= 0 and position < self._last_position - 0.25)
        ):
            index = bisect_right(self._lyric_times, position) - 1
        if index != self._last_lyric:
            self._last_lyric = index
            line = self._lyrics[index] if index >= 0 else None
            line_end = (
                self._lyrics[index + 1].start
                if index + 1 < len(self._lyrics)
                else position + 4.0
            )
            self.lyric_prev.setText(self._lyrics[index - 1].text if index > 0 else "")
            self.lyric_current.set_line(line, line_end)
            self.lyric_next.setText(
                self._lyrics[index + 1].text if index + 1 < len(self._lyrics) else ""
            )
        self.lyric_current.set_position(position)
        self._last_position = position

    def _target_position(self) -> QPoint:
        screen = active_screen_geometry()
        fallback = QPoint(screen.right() - self.width() - 28, screen.top() + 158)
        return self.saved_position(fallback)


class ToastHUD(LiquidGlassWindow):
    def __init__(self, position_provider):
        super().__init__(THEME.radius, click_through=True, position_key="toast")
        self._position_provider = position_provider
        self._karaoke = False
        self._current_cover = b""
        self.resize(430, 104)
        self.setWindowOpacity(0.0)
        self._build()
        self.finalize_scaling(430, 104)
        self.karaoke_hud = KaraokeHUD(position_provider)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_animated)
        self._enter_group: QParallelAnimationGroup | None = None
        self._exit_group: QParallelAnimationGroup | None = None
        self._display_font_family = DISPLAY_FONT_FAMILY

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 18, 14)
        root.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.cover = CoverArt(76, self)
        row.addWidget(self.cover)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.badge = QLabel("ЯНДЕКС МУЗЫКА")
        self.badge.setFont(QFont(DISPLAY_FONT_FAMILY, 8, QFont.Weight.Bold))
        self.badge.setStyleSheet("color: rgba(255, 218, 74, 235); letter-spacing: 1.4px;")
        self.title = QLabel("Яндекс Музыка")
        self.title.setFont(QFont(DISPLAY_FONT_FAMILY, 14, QFont.Weight.Bold))
        self.title.setStyleSheet("color: rgba(246, 248, 255, 252);")
        self.artist = QLabel("")
        self.artist.setFont(QFont(FONT_FAMILY, 10))
        self.artist.setStyleSheet("color: rgba(174, 181, 204, 218);")
        text.addWidget(self.badge)
        text.addWidget(self.title)
        text.addWidget(self.artist)
        self.volume = QProgressBar()
        self.volume.setFixedHeight(5)
        self.volume.setTextVisible(False)
        self.volume.setRange(0, 100)
        self.volume.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,24); border-radius: 2px; }"
            "QProgressBar::chunk { background: rgba(255,213,57,235); border-radius: 2px; }"
        )
        self.volume.hide()
        text.addWidget(self.volume)
        row.addLayout(text, 1)
        root.addLayout(row)

    def set_display_font(self, family: str) -> None:
        self._display_font_family = family
        for widget in (self.badge, self.title):
            font = widget.font()
            font.setFamily(family)
            widget.setFont(font)
        self.karaoke_hud.set_display_font(family)
        self.update()

    def set_lyrics_offset(self, seconds: float) -> None:
        self.karaoke_hud.set_lyrics_offset(seconds)

    def set_edit_mode(self, enabled: bool) -> None:
        super().set_edit_mode(enabled)
        self.karaoke_hud.set_edit_mode(enabled)
        if enabled:
            self._hide_timer.stop()
            self._animate_in()
        elif not self._karaoke:
            self._hide_timer.start(1800)

    def display(self, message: OverlayMessage, *, notifications: bool) -> None:
        if not notifications and message.kind not in {"settings", "error"}:
            return
        self.title.setText(message.title or "Яндекс Музыка")
        self.artist.setText(message.subtitle)
        if message.cover:
            self._current_cover = message.cover
            self.cover.set_bytes(message.cover)
        self.volume.setVisible(message.kind == "volume" and message.value >= 0)
        if message.value >= 0:
            self.volume.setValue(message.value)
        if self.isVisible():
            self._hide_timer.stop()
            raise_topmost(self)
        else:
            self._animate_in()
        if not self._edit_mode:
            timeout = {
                "volume": 1200,
                "seek": 1500,
                "settings": 2200,
                "track": 2800,
                "error": 3600,
            }.get(message.kind, 2400)
            self._hide_timer.start(timeout)

    def update_track(self, state: TrackState) -> None:
        self.karaoke_hud.set_playing(state.playing)
        if state.cover:
            self._current_cover = state.cover
            if self.isVisible():
                self.cover.set_bytes(state.cover)
        if self._karaoke:
            self.title.setText(state.title)
            self.artist.setText(state.artist)

    def set_karaoke(self, enabled: bool) -> None:
        self._karaoke = enabled
        self.karaoke_hud.set_enabled(enabled)
        if enabled:
            if not self.isVisible():
                self._animate_in()
            else:
                raise_topmost(self)
            if not self._edit_mode:
                self._hide_timer.start(2400)
        else:
            self.badge.setText("ЯНДЕКС МУЗЫКА")
            if not self._edit_mode:
                self._hide_timer.start(1800)

    def set_lyrics(self, key: str, lines: list[LyricLine]) -> None:
        self.karaoke_hud.set_lyrics(key, lines)

    def prepare_lyrics(self, key: str) -> None:
        self.karaoke_hud.prepare_lyrics(key)

    def _target_position(self) -> QPoint:
        screen = active_screen_geometry()
        fallback = QPoint(screen.right() - self.width() - 28, screen.top() + 42)
        return self.saved_position(fallback)

    def _animate_in(self) -> None:
        self._hide_timer.stop()
        if self._exit_group:
            self._exit_group.stop()
        if self._enter_group:
            self._enter_group.stop()
        target = self._target_position()
        was_visible = self.isVisible()
        if was_visible:
            self.move(target)
            raise_topmost(self)
            return
        self.move(target)
        if not was_visible:
            self.capture_backdrop()
            self.setWindowOpacity(0.0)
            self.set_reveal(0.0)
        self.move(target + QPoint(26 if not was_visible else 8, -7))
        self.show()
        raise_topmost(self)

        group = QParallelAnimationGroup(self)
        position = QPropertyAnimation(self, b"pos", group)
        position.setDuration(300)
        position.setStartValue(self.pos())
        position.setEndValue(target)
        position.setEasingCurve(QEasingCurve.Type.OutCubic)
        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setDuration(320)
        opacity.setStartValue(self.windowOpacity())
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
        reveal = QPropertyAnimation(self, b"reveal", group)
        reveal.setDuration(360)
        reveal.setStartValue(self.get_reveal())
        reveal.setEndValue(1.0)
        reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(position)
        group.addAnimation(opacity)
        group.addAnimation(reveal)
        self._enter_group = group
        group.start()

    def _hide_animated(self) -> None:
        if self._edit_mode:
            return
        if self._enter_group:
            self._enter_group.stop()
        if self._exit_group:
            self._exit_group.stop()
        group = QParallelAnimationGroup(self)
        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setDuration(330)
        opacity.setStartValue(self.windowOpacity())
        opacity.setEndValue(0.0)
        opacity.setEasingCurve(QEasingCurve.Type.InOutCubic)
        position = QPropertyAnimation(self, b"pos", group)
        position.setDuration(360)
        position.setStartValue(self.pos())
        position.setEndValue(self.pos() + QPoint(30, 6))
        position.setEasingCurve(QEasingCurve.Type.InOutCubic)
        group.addAnimation(opacity)
        group.addAnimation(position)
        group.finished.connect(self.hide)
        self._exit_group = group
        group.start()


class HelpHUD(LiquidGlassWindow):
    def __init__(self):
        super().__init__(40, click_through=True, position_key="help")
        self.resize(960, 510)
        self.setWindowOpacity(0.0)
        self._shown = False
        self._animation: QParallelAnimationGroup | None = None
        self._display_font_family = DISPLAY_FONT_FAMILY
        self._font_widgets: list[QLabel] = []
        self._hotkey_labels: dict[str, QLabel] = {}
        self._build()
        self.finalize_scaling(960, 510)

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 22)
        root.setSpacing(18)

        rail = QFrame()
        rail.setFixedWidth(230)
        rail.setStyleSheet("background: rgba(0,0,0,72); border-radius: 22px;")
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(22, 22, 22, 20)
        rail_layout.setSpacing(0)
        eyebrow = QLabel("ЯНДЕКС МУЗЫКА")
        eyebrow.setFont(QFont(DISPLAY_FONT_FAMILY, 8, QFont.Weight.Bold))
        eyebrow.setStyleSheet("color: rgba(255,218,74,235); letter-spacing: 1.5px;")
        eyebrow.setWordWrap(True)
        rail_layout.addWidget(eyebrow)
        rail_layout.addSpacing(16)
        heading = QLabel("БЫСТРЫЙ\nДОСТУП")
        heading.setFont(QFont(DISPLAY_FONT_FAMILY, 25, QFont.Weight.Bold))
        heading.setStyleSheet("color: rgba(247,249,255,252);")
        rail_layout.addWidget(heading)
        rail_layout.addSpacing(10)
        rail_layout.addStretch()
        self.font_status = QLabel("Шрифт  ·  Sour Gummy  (1/4)")
        self.offset_status = QLabel("Караоке  ·  +0.25 с")
        for status in (self.font_status, self.offset_status):
            status.setFont(QFont(FONT_FAMILY, 9, QFont.Weight.DemiBold))
            status.setStyleSheet(
                "color: rgba(220,224,239,220); background: rgba(255,255,255,12);"
                "border-radius: 9px; padding: 7px 9px;"
            )
            rail_layout.addWidget(status)
            rail_layout.addSpacing(7)
        author = QLabel(f"Автор\n{AUTHOR}")
        author.setFont(QFont(FONT_FAMILY, 9))
        author.setStyleSheet("color: rgba(135,142,164,180);")
        rail_layout.addSpacing(8)
        rail_layout.addWidget(author)
        root.addWidget(rail)

        content = QVBoxLayout()
        content.setSpacing(14)
        title = QLabel("Пульт быстрого доступа")
        title.setFont(QFont(DISPLAY_FONT_FAMILY, 20, QFont.Weight.Bold))
        title.setStyleSheet("color: rgba(247,249,255,250);")
        content.addWidget(title)
        hint = QLabel("Esc — закрыть   ·   Ctrl + Shift + G — переместить окна")
        hint.setFont(QFont(FONT_FAMILY, 9))
        hint.setStyleSheet("color: rgba(151,159,184,190);")
        content.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        groups = (
            ("ПЛЕЕР", HOTKEYS[0:5]),
            ("ЗВУК", HOTKEYS[5:8]),
            ("ИНТЕРФЕЙС И КАРАОКЕ", HOTKEYS[8:]),
        )
        grid.addWidget(self._shortcut_card(*groups[0]), 0, 0, 2, 1)
        grid.addWidget(self._shortcut_card(*groups[1]), 0, 1)
        grid.addWidget(self._shortcut_card(*groups[2]), 1, 1)
        content.addLayout(grid, 1)
        root.addLayout(content, 1)
        self._font_widgets.extend((eyebrow, heading, title))

    def _shortcut_card(self, title: str, shortcuts) -> QFrame:
        card = QFrame()
        card.setStyleSheet("background: rgba(255,255,255,10); border-radius: 18px;")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setFont(QFont(self._display_font_family, 8, QFont.Weight.Bold))
        heading.setStyleSheet("color: rgba(255,218,74,225); letter-spacing: 1.2px;")
        layout.addWidget(heading)
        self._font_widgets.append(heading)
        for canonical, keys, description in shortcuts:
            row = QHBoxLayout()
            row.setSpacing(10)
            key = QLabel(self._format_shortcut(keys))
            key.setFixedWidth(76)
            key.setAlignment(Qt.AlignmentFlag.AlignCenter)
            key.setFont(QFont(self._display_font_family, 8, QFont.Weight.Bold))
            key.setStyleSheet(
                "color: rgba(245,247,255,235); background: rgba(0,0,0,74);"
                "border-radius: 8px; padding: 5px 4px;"
            )
            label = QLabel(description)
            label.setFont(QFont(FONT_FAMILY, 9))
            label.setStyleSheet("color: rgba(190,196,216,225);")
            row.addWidget(key)
            row.addWidget(label, 1)
            layout.addLayout(row)
            self._font_widgets.append(key)
            self._hotkey_labels[canonical] = key
        layout.addStretch()
        return card

    @staticmethod
    def _format_shortcut(keys: str) -> str:
        normalized = keys.replace("+", " + ") if " + " not in keys else keys
        if normalized.startswith("Ctrl + Shift + "):
            return f"C+S · {normalized.removeprefix('Ctrl + Shift + ')}"
        if normalized.startswith("Shift + "):
            return f"S · {normalized.removeprefix('Shift + ')}"
        if normalized.startswith("Ctrl + "):
            return f"C · {normalized.removeprefix('Ctrl + ')}"
        if normalized.startswith("Alt + "):
            return f"A · {normalized.removeprefix('Alt + ')}"
        return normalized

    def set_hotkeys(self, sequences: dict[str, str]) -> None:
        for canonical, sequence in sequences.items():
            label = self._hotkey_labels.get(canonical)
            if label:
                label.setText(self._format_shortcut(sequence))

    def set_display_font(self, family: str) -> None:
        self._display_font_family = family
        for widget in self._font_widgets:
            font = widget.font()
            font.setFamily(family)
            font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
            widget.setFont(font)

    def set_status(self, font_name: str, offset: float, available: int, total: int) -> None:
        sign = "+" if offset >= 0 else ""
        self.font_status.setText(f"Шрифт  ·  {font_name}  ({available}/{total})")
        self.offset_status.setText(f"Караоке  ·  {sign}{offset:.2f} с")

    @property
    def is_shown(self) -> bool:
        return self._shown

    def close_animated(self) -> None:
        if self._shown:
            self.toggle()

    def toggle(self) -> None:
        self._shown = not self._shown
        if self._animation:
            self._animation.stop()
        group = QParallelAnimationGroup(self)
        if self._shown:
            screen = active_screen_geometry()
            fallback = QPoint(
                screen.left() + (screen.width() - self.width()) // 2,
                screen.top() + (screen.height() - self.height()) // 2,
            )
            point = self.saved_position(fallback)
            target = QRect(
                point.x(),
                point.y(),
                self.width(),
                self.height(),
            )
            self.setGeometry(target)
            self.capture_backdrop()
            start_rect = target.adjusted(14, 14, -14, -14)
            self.setGeometry(start_rect)
            self.setWindowOpacity(0.0)
            self.set_reveal(0.0)
            self.show()
            raise_topmost(self)
            geometry = QPropertyAnimation(self, b"geometry", group)
            geometry.setDuration(300)
            geometry.setStartValue(start_rect)
            geometry.setEndValue(target)
            geometry.setEasingCurve(QEasingCurve.Type.OutCubic)
            opacity = QPropertyAnimation(self, b"windowOpacity", group)
            opacity.setDuration(340)
            opacity.setStartValue(0.0)
            opacity.setEndValue(1.0)
            opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
            reveal = QPropertyAnimation(self, b"reveal", group)
            reveal.setDuration(380)
            reveal.setStartValue(0.0)
            reveal.setEndValue(1.0)
            reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(geometry)
            group.addAnimation(opacity)
            group.addAnimation(reveal)
        else:
            current = self.geometry()
            geometry = QPropertyAnimation(self, b"geometry", group)
            geometry.setDuration(340)
            geometry.setStartValue(current)
            geometry.setEndValue(current.adjusted(10, 10, -10, -10))
            geometry.setEasingCurve(QEasingCurve.Type.InOutCubic)
            opacity = QPropertyAnimation(self, b"windowOpacity", group)
            opacity.setDuration(300)
            opacity.setStartValue(self.windowOpacity())
            opacity.setEndValue(0.0)
            opacity.setEasingCurve(QEasingCurve.Type.InOutCubic)
            group.addAnimation(geometry)
            group.addAnimation(opacity)
            group.finished.connect(self.hide)
        self._animation = group
        group.start()
