"""集中声明所有用到的 Win32 API，统一 restype/argtypes，避免 64 位句柄被截断。

本模块只做声明，不做 DPI 设置（那属于 dpi.py 的职责，且必须最先执行）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# ---------------------------------------------------------------- 常量
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_CMONITORS = 80
SM_CXSCREEN, SM_CYSCREEN = 0, 1

WM_DESTROY, WM_CLOSE = 0x0002, 0x0010
WM_COMMAND, WM_TIMER = 0x0111, 0x0113
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MOUSEMOVE = 0x0200

SW_RESTORE, SW_SHOW = 9, 5
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040

GA_ROOT = 2
MONITOR_DEFAULTTONEAREST = 2

# 鼠标事件标志
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

# 键盘事件标志
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# 常用虚拟键码
VK = {
    "backspace": 0x08, "tab": 0x09, "clear": 0x0C, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "help": 0x2F,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "sleep": 0x5F,
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63,
    "numpad4": 0x64, "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67,
    "numpad8": 0x68, "numpad9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "separator": 0x6C, "subtract": 0x6D,
    "decimal": 0x6E, "divide": 0x6F,
    "numlock": 0x90, "scrolllock": 0x91,
    "browser_back": 0xA6, "browser_forward": 0xA7, "browser_refresh": 0xA8,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play": 0xB3,
}
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i          # F1=0x70 ... F24=0x87
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)
# 需要扩展键标志的键（左右 ctrl/alt、方向键等）
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
                0x5B, 0x5C, 0x5D, 0x6F, 0xA3, 0xA5, 0x90, 0x2C}


# ---------------------------------------------------------------- 结构体
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", ctypes.c_ulong)]


# ---------------------------------------------------------------- 函数声明
_HANDLE_FNS = [
    "CreateWindowExW", "FindWindowW", "FindWindowExW", "GetForegroundWindow",
    "WindowFromPoint", "GetAncestor", "GetDC", "GetDesktopWindow",
    "MonitorFromPoint", "LoadCursorW", "GetSysColorBrush", "SetTimer", "GetCursor",
    "GetParent", "RealChildWindowFromPoint", "GetShellWindow",
]
for _fn in _HANDLE_FNS:
    try:
        getattr(user32, _fn).restype = ctypes.c_void_p
    except AttributeError:
        pass

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = ctypes.c_int
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.IsHungAppWindow.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = ctypes.c_int
user32.GetDoubleClickTime.restype = ctypes.c_uint
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, ctypes.c_uint]
user32.GetSystemMetricsForDpi.restype = ctypes.c_int
user32.MonitorFromPoint.argtypes = [wt.POINT, ctypes.c_ulong]
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint
user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p]
user32.EnumDisplayMonitors.restype = ctypes.c_int
# DPI 相关（必须在任何 UI 调用前使用）
try:
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
except AttributeError:
    pass
try:
    user32.SetProcessDPIAware.restype = ctypes.c_int
except AttributeError:
    pass

kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.c_int
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_int
kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                ctypes.c_wchar_p,
                                                ctypes.POINTER(ctypes.c_ulong)]
kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
user32.GetWindowThreadProcessId.restype = ctypes.c_ulong

# 键盘键名解析
user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
user32.VkKeyScanW.restype = ctypes.c_short
# 剪贴板
user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.restype = ctypes.c_int
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = ctypes.c_int
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = ctypes.c_int
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
# 前台窗口抢占（Windows 有前台锁定策略，需 AttachThreadInput 才能夺权）
user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int]
user32.AttachThreadInput.restype = ctypes.c_int
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.BringWindowToTop.restype = ctypes.c_int
user32.SetActiveWindow.argtypes = [ctypes.c_void_p]
user32.SetActiveWindow.restype = ctypes.c_void_p
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = ctypes.c_ulong

gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int

try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_uint),
                                        ctypes.POINTER(ctypes.c_uint)]
except OSError:  # pragma: no cover
    shcore = None


def get_last_error() -> int:
    return ctypes.get_last_error()


__all__ = [n for n in dir() if not n.startswith("_")]
