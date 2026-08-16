from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QGuiApplication, QPainterPath, QRegion
from PyQt6.QtWidgets import QWidget

IS_WINDOWS = sys.platform == "win32"
user32 = ctypes.windll.user32 if IS_WINDOWS else None
dwmapi = ctypes.windll.dwmapi if IS_WINDOWS else None

GWL_EXSTYLE = -20
GCL_STYLE = -26
CS_DROPSHADOW = 0x00020000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_NCRENDERING_POLICY = 2
DWMNCRP_DISABLED = 1
DWMWCP_ROUND = 2
DWMWA_COLOR_NONE = 0xFFFFFFFE
DWMSBT_TRANSIENTWINDOW = 3
WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WH_KEYBOARD_LL = 13
MONITOR_DEFAULTTONEAREST = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class AccentPolicy(ctypes.Structure):
    _fields_ = (
        ("state", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("gradient_color", ctypes.c_uint),
        ("animation_id", ctypes.c_int),
    )


class WindowCompositionAttributeData(ctypes.Structure):
    _fields_ = (
        ("attribute", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("size", ctypes.c_size_t),
    )


class MonitorInfo(ctypes.Structure):
    _fields_ = (
        ("size", wintypes.DWORD),
        ("monitor", wintypes.RECT),
        ("work", wintypes.RECT),
        ("flags", wintypes.DWORD),
    )


class LowLevelKeyboardInput(ctypes.Structure):
    _fields_ = (
        ("virtual_key", wintypes.DWORD),
        ("scan_code", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", ctypes.c_size_t),
    )


class GlobalHotkeyManager:
    """Low-latency Win32 hotkeys that never hook or delay bare modifier keys."""

    def __init__(
        self,
        bindings: tuple[tuple[int, int, str], ...],
        callback: Callable[[str], None],
    ):
        self._bindings = bindings
        self._callback = callback
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self.registered_count = 0

    def start(self) -> int:
        if not IS_WINDOWS or self._thread:
            return 0
        self._thread = threading.Thread(target=self._run, name="ym-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        return self.registered_count

    def _run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        registered: dict[int, str] = {}
        for identifier, (modifiers, virtual_key, action) in enumerate(self._bindings, 1):
            if user32.RegisterHotKey(
                None,
                identifier,
                modifiers | MOD_NOREPEAT,
                virtual_key,
            ):
                registered[identifier] = action
        self.registered_count = len(registered)
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY and message.wParam in registered:
                self._callback(registered[message.wParam])
        for identifier in registered:
            user32.UnregisterHotKey(None, identifier)

    def stop(self) -> None:
        if not self._thread:
            return
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = 0

    def restart(self, bindings: tuple[tuple[int, int, str], ...]) -> int:
        self.stop()
        self._bindings = bindings
        self.registered_count = 0
        self._ready.clear()
        return self.start()


class PassthroughHotkeyManager:
    """Observe complete hotkeys while always forwarding every physical key unchanged."""

    _modifier_keys = {
        0x0002: {0x11, 0xA2, 0xA3},  # Ctrl
        0x0004: {0x10, 0xA0, 0xA1},  # Shift
        0x0001: {0x12, 0xA4, 0xA5},  # Alt
        0x0008: {0x5B, 0x5C},  # Windows
    }

    def __init__(
        self,
        bindings: tuple[tuple[int, int, str], ...],
        callback: Callable[[str], None],
    ) -> None:
        self._bindings = bindings
        self._callback = callback
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._hook_proc = None
        self._down_keys: set[int] = set()
        self.registered_count = 0
        self.last_error = 0

    def start(self) -> int:
        if not IS_WINDOWS or self._thread:
            return 0
        self._thread = threading.Thread(target=self._run, name="ym-game-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        return self.registered_count

    def _current_modifiers(self) -> int:
        result = 0
        for modifier, keys in self._modifier_keys.items():
            if self._down_keys.intersection(keys):
                result |= modifier
        return result

    def _run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        procedure_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        def observe(n_code: int, event: int, data_pointer: int) -> int:
            if n_code >= 0 and event in {WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP}:
                data = ctypes.cast(
                    data_pointer,
                    ctypes.POINTER(LowLevelKeyboardInput),
                ).contents
                key = int(data.virtual_key)
                was_down = key in self._down_keys
                if event in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                    self._down_keys.add(key)
                    if not was_down:
                        modifiers = self._current_modifiers()
                        for required, virtual_key, action in self._bindings:
                            if key == virtual_key and modifiers == required:
                                self._callback(action)
                                break
                else:
                    self._down_keys.discard(key)
            return user32.CallNextHookEx(self._hook, n_code, event, data_pointer)

        self._hook_proc = procedure_type(observe)
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int,
            procedure_type,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        ctypes.windll.kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_proc,
            ctypes.windll.kernel32.GetModuleHandleW(None),
            0,
        )
        self.last_error = 0 if self._hook else ctypes.windll.kernel32.GetLastError()
        self.registered_count = len(self._bindings) if self._hook else 0
        self._ready.set()
        if not self._hook:
            return
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._down_keys.clear()

    def stop(self) -> None:
        if not self._thread:
            return
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = 0
        self._hook_proc = None
        self.registered_count = 0
        self.last_error = 0
        self._ready.clear()

    def restart(self, bindings: tuple[tuple[int, int, str], ...]) -> int:
        self.stop()
        self._bindings = bindings
        return self.start()


def _enable_live_acrylic(hwnd: int) -> bool:
    """Ask the Windows compositor to blur the pixels currently behind the HWND."""
    composition = getattr(user32, "SetWindowCompositionAttribute", None)
    if composition is None:
        return False
    accent = AccentPolicy(
        state=ACCENT_ENABLE_ACRYLICBLURBEHIND,
        flags=2,
        # Keep the compositor blur live, but leave its rectangular tint effectively clear.
        # The dark glass tint is painted only inside our own rounded QPainterPath.
        gradient_color=0x06000000,
        animation_id=0,
    )
    data = WindowCompositionAttributeData(
        attribute=WCA_ACCENT_POLICY,
        data=ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
        size=ctypes.sizeof(accent),
    )
    return bool(composition(hwnd, ctypes.byref(data)))


def apply_overlay_style(widget: QWidget, *, click_through: bool, radius: int) -> bool:
    """Apply no-focus topmost behavior and a hard native rounded boundary."""
    if not IS_WINDOWS:
        return False
    hwnd = int(widget.winId())
    ex_style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex_style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if click_through:
        ex_style |= WS_EX_TRANSPARENT
    else:
        ex_style &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex_style)
    if hasattr(user32, "GetClassLongPtrW") and hasattr(user32, "SetClassLongPtrW"):
        class_style = user32.GetClassLongPtrW(hwnd, GCL_STYLE)
        user32.SetClassLongPtrW(hwnd, GCL_STYLE, class_style & ~CS_DROPSHADOW)

    non_client_policy = ctypes.c_int(DWMNCRP_DISABLED)
    dwmapi.DwmSetWindowAttribute(
        hwnd,
        DWMWA_NCRENDERING_POLICY,
        ctypes.byref(non_client_policy),
        ctypes.sizeof(non_client_policy),
    )

    corner = ctypes.c_int(DWMWCP_ROUND)
    dwmapi.DwmSetWindowAttribute(
        hwnd,
        DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(corner),
        ctypes.sizeof(corner),
    )
    border = ctypes.c_uint(DWMWA_COLOR_NONE)
    dwmapi.DwmSetWindowAttribute(
        hwnd,
        DWMWA_BORDER_COLOR,
        ctypes.byref(border),
        ctypes.sizeof(border),
    )

    apply_native_region(widget, radius)
    # DWM backdrops create an additional rectangular composition surface outside
    # a custom QRegion. Use the clipped Qt backdrop path so corner pixels stay clear.
    live_backdrop = False

    apply_native_region(widget, radius)
    raise_topmost(widget)
    return live_backdrop


def raise_topmost(widget: QWidget) -> None:
    if not IS_WINDOWS:
        return
    user32.SetWindowPos(
        int(widget.winId()),
        HWND_TOPMOST,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


def apply_rounded_mask(widget: QWidget, radius: int) -> None:
    clip_radius = radius + 2
    path = QPainterPath()
    path.addRoundedRect(
        0.0,
        0.0,
        float(widget.width()),
        float(widget.height()),
        clip_radius,
        clip_radius,
    )
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))
    apply_native_region(widget, radius)


def apply_native_region(widget: QWidget, radius: int) -> None:
    """Clip the HWND so compositor pixels cannot leak into square corners."""
    if not IS_WINDOWS or not widget.winId():
        return
    hwnd = int(widget.winId())
    dpi = user32.GetDpiForWindow(hwnd) if hasattr(user32, "GetDpiForWindow") else 96
    client_rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
        return
    width = max(1, client_rect.right - client_rect.left)
    height = max(1, client_rect.bottom - client_rect.top)
    clip_radius = radius + 2
    diameter = max(2, round(clip_radius * 2 * dpi / 96))
    region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, width, height, diameter, diameter)
    if region and not user32.SetWindowRgn(hwnd, region, True):
        ctypes.windll.gdi32.DeleteObject(region)


def active_screen_geometry() -> QRect:
    """Return the screen containing the foreground window, not just primary screen."""
    screens = QGuiApplication.screens()
    if not screens:
        return QRect(0, 0, 1920, 1080)
    if IS_WINDOWS:
        hwnd = user32.GetForegroundWindow()
        rect = wintypes.RECT()
        if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            center = QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            screen = QGuiApplication.screenAt(center)
            if screen:
                return screen.geometry()
    return QGuiApplication.primaryScreen().geometry()


def foreground_is_fullscreen() -> bool:
    """Detect a real foreground window covering its monitor without polling processes."""
    if not IS_WINDOWS:
        return False
    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        return False
    class_name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, class_name, len(class_name))
    if class_name.value in {"Progman", "WorkerW", "Shell_TrayWnd"}:
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    margin = 3
    return (
        rect.left <= info.monitor.left + margin
        and rect.top <= info.monitor.top + margin
        and rect.right >= info.monitor.right - margin
        and rect.bottom >= info.monitor.bottom - margin
    )


def foreground_prefers_passthrough() -> bool:
    """Use pass-through keys in fullscreen apps and Minecraft, including windowed mode."""
    if not IS_WINDOWS:
        return False
    if foreground_is_fullscreen():
        return True
    hwnd = user32.GetForegroundWindow()
    process_id = wintypes.DWORD()
    if not hwnd or not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id)):
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if not handle:
        return False
    try:
        path = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(path))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return False
        executable = path.value.rsplit("\\", 1)[-1].casefold()
        return executable in {"javaw.exe", "minecraft.exe", "minecraft.windows.exe"}
    finally:
        kernel32.CloseHandle(handle)


class SingleInstance:
    def __init__(self, name: str):
        self._handle = None
        self.already_running = False
        if IS_WINDOWS:
            self._handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
            self.already_running = ctypes.windll.kernel32.GetLastError() == 183

    def close(self) -> None:
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
