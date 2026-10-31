"""DPI 感知与坐标系基线。

★ 铁律：本模块的 setup() 必须在 import uiautomation / mss 之前调用。
   进程一旦确定 DPI 感知级别就无法更改，若被第三方库抢先设置为偏差级别，
   会导致「截图坐标 ≠ UIA 坐标 ≠ 点击坐标」的经典错位。

本机实测（2560x1600 @150% 主屏 + 2560x1600 副屏，虚拟桌面 5120x1600）：
  - PMv2 感知下，UIA 返回的 BoundingRectangle 即物理像素，与 Win32 GetWindowRect、
    与 mss 截图像素一一对应，偏差 0。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

from . import winapi as w

_setup_done = False
_mode = "unknown"


def setup() -> str:
    """把当前进程设置为 Per-Monitor V2 DPI 感知。返回实际生效的模式。"""
    global _setup_done, _mode
    if _setup_done:
        return _mode

    # 首选 PMv2（Win10 1703+）
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
        if w.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _mode = "per-monitor-v2"
            _setup_done = True
            return _mode
    except AttributeError:
        pass

    # 退而求其次：Per-Monitor (Win8.1+)
    try:
        hr = ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        if hr == 0:
            _mode = "per-monitor"
            _setup_done = True
            return _mode
    except Exception:
        pass

    # 最后兜底：System DPI aware
    try:
        if w.user32.SetProcessDPIAware():
            _mode = "system-aware"
    except Exception:
        _mode = "unaware"

    _setup_done = True
    return _mode


def mode() -> str:
    return _mode


def virtual_desktop() -> dict:
    """虚拟桌面（所有显示器拼接）的物理像素边界。"""
    return {
        "left": w.user32.GetSystemMetrics(w.SM_XVIRTUALSCREEN),
        "top": w.user32.GetSystemMetrics(w.SM_YVIRTUALSCREEN),
        "width": w.user32.GetSystemMetrics(w.SM_CXVIRTUALSCREEN),
        "height": w.user32.GetSystemMetrics(w.SM_CYVIRTUALSCREEN),
    }


def primary_screen() -> dict:
    return {
        "width": w.user32.GetSystemMetrics(w.SM_CXSCREEN),
        "height": w.user32.GetSystemMetrics(w.SM_CYSCREEN),
    }


def monitor_count() -> int:
    return w.user32.GetSystemMetrics(w.SM_CMONITORS)


def monitors() -> list:
    """枚举每台显示器的边界与有效 DPI。"""
    out = []
    vd = virtual_desktop()

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    monitor_list = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.POINTER(RECT), ctypes.c_void_p)
    def _cb(hmon, hdc, lprc, lp):
        mi = w.MONITORINFO()
        mi.cbSize = ctypes.sizeof(w.MONITORINFO)
        dpi_x = dpi_y = 0
        if w.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            if w.shcore is not None:
                dx, dy = ctypes.c_uint(), ctypes.c_uint()
                if w.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                    dpi_x, dpi_y = dx.value, dy.value
            monitor_list.append({
                "rect": [mi.rcMonitor.left, mi.rcMonitor.top,
                         mi.rcMonitor.right, mi.rcMonitor.bottom],
                "work": [mi.rcWork.left, mi.rcWork.top,
                         mi.rcWork.right, mi.rcWork.bottom],
                "primary": bool(mi.dwFlags & 1),
                "dpi": dpi_x or 96,
                "scale": round((dpi_x or 96) / 96 * 100),
            })
        return 1

    try:
        w.user32.EnumDisplayMonitors(None, None, _cb, None)
    except Exception:
        pass
    out = monitor_list
    if not out:
        out = [{"rect": [vd["left"], vd["top"], vd["left"] + vd["width"],
                         vd["top"] + vd["height"]],
                "work": [0, 0, vd["width"], vd["height"]],
                "primary": True, "dpi": 96, "scale": 100}]
    return out


def primary_rect() -> dict:
    """主显示器的**绝对**矩形 {left, top, right, bottom}。

    注意：primary_screen() 里的 width/height 来自 SM_CXSCREEN / SM_CYSCREEN，
    它们是**相对主显示器原点**的尺寸，不是虚拟桌面绝对坐标。
    当副屏被插到主屏左侧时，主屏原点会变成 (2560, 0)，此时拿 width/height
    当绝对坐标用会算错（例如「显示桌面」热区保护会守错矩形）。
    需要判断"某个绝对坐标是否位于主屏某个角"时，一律用本函数。
    """
    for m in monitors():
        if m.get("primary"):
            l, t, r, b = m["rect"]
            return {"left": int(l), "top": int(t), "right": int(r), "bottom": int(b)}
    # 兜底：主屏原点在 (0,0)
    vd = virtual_desktop()
    return {"left": 0, "top": 0,
            "right": primary_screen()["width"], "bottom": primary_screen()["height"]}


def describe() -> dict:
    vd = virtual_desktop()
    return {
        "dpi_mode": _mode,
        "virtual_desktop": vd,
        "primary": primary_screen(),
        "monitor_count": monitor_count(),
        "monitors": monitors(),
    }


def in_virtual_desktop(x: int, y: int, margin: int = 0) -> bool:
    vd = virtual_desktop()
    return (vd["left"] - margin <= x < vd["left"] + vd["width"] + margin
            and vd["top"] - margin <= y < vd["top"] + vd["height"] + margin)


def rect_in_virtual_desktop(l: int, t: int, r: int, b: int) -> bool:
    """判断一个矩形是否落在虚拟桌面内有实际交集（用于识别不可信的 UIA 坐标）。"""
    if r <= l or b <= t:
        return False
    vd = virtual_desktop()
    return (l < vd["left"] + vd["width"] and r > vd["left"]
            and t < vd["top"] + vd["height"] and b > vd["top"])


__all__ = ["setup", "mode", "virtual_desktop", "primary_screen", "primary_rect",
           "monitor_count", "monitors", "describe",
           "in_virtual_desktop", "rect_in_virtual_desktop"]
