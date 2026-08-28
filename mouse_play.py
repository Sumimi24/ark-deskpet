import ctypes
import time
from ctypes import wintypes

from PySide6.QtCore import QPoint


_user32 = ctypes.windll.user32
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [wintypes.INT, wintypes.INT]
_user32.SetCursorPos.restype = wintypes.BOOL


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
_user32.GetLastInputInfo.restype = wintypes.BOOL
_user32.GetAsyncKeyState.argtypes = [wintypes.INT]
_user32.GetAsyncKeyState.restype = wintypes.SHORT
_kernel32 = ctypes.windll.kernel32
_kernel32.GetTickCount64.restype = ctypes.c_ulonglong

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04


def cursor_position():
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        return None
    return QPoint(point.x, point.y)


def set_cursor_position(point):
    return bool(_user32.SetCursorPos(int(point.x()), int(point.y())))


def idle_seconds():
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick = _kernel32.GetTickCount64()
    return max(0.0, (tick - info.dwTime) / 1000.0)


def mouse_buttons_down():
    return any(_user32.GetAsyncKeyState(key) & 0x8000 for key in (
        VK_LBUTTON,
        VK_RBUTTON,
        VK_MBUTTON,
    ))


def cursor_moved_from(point, tolerance=4):
    current = cursor_position()
    if current is None or point is None:
        return True
    return abs(current.x() - point.x()) > tolerance or abs(current.y() - point.y()) > tolerance


def monotonic():
    return time.monotonic()
