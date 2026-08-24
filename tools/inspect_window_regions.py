import ctypes
from ctypes import wintypes


def main() -> int:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    process_ids: set[int] = set()

    process_snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)

    class ProcessEntry(ctypes.Structure):
        _fields_ = (
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("process_id", wintypes.DWORD),
            ("default_heap", ctypes.c_size_t),
            ("module_id", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent_process_id", wintypes.DWORD),
            ("priority", ctypes.c_long),
            ("flags", wintypes.DWORD),
            ("executable", wintypes.WCHAR * 260),
        )

    entry = ProcessEntry(size=ctypes.sizeof(ProcessEntry))
    if ctypes.windll.kernel32.Process32FirstW(process_snapshot, ctypes.byref(entry)):
        while True:
            if entry.executable.casefold() == "elarionmusiccontrol.exe":
                process_ids.add(int(entry.process_id))
            if not ctypes.windll.kernel32.Process32NextW(process_snapshot, ctypes.byref(entry)):
                break
    ctypes.windll.kernel32.CloseHandle(process_snapshot)

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def inspect(hwnd, _parameter) -> bool:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value not in process_ids or not user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        region = gdi32.CreateRectRgn(0, 0, 0, 0)
        region_type = user32.GetWindowRgn(hwnd, region)
        points = tuple(
            f"{x},{y}={int(gdi32.PtInRegion(region, x, y))}"
            for x, y in ((2, 2), (10, 10), (20, 20), (30, 5), (5, 30))
        )
        print(
            f"hwnd={int(hwnd)} pos={rect.left},{rect.top} "
            f"size={rect.right - rect.left}x{rect.bottom - rect.top} "
            f"region={region_type} points={' '.join(points)}"
        )
        gdi32.DeleteObject(region)
        return True

    user32.EnumWindows(callback_type(inspect), 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
