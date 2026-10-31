"""屏幕截图。基于 mss，输出物理像素，与 UIA 坐标同一坐标系。

实测：本机 5120x1600 虚拟桌面下，mss 抓取的像素与 GDI GetPixel 逐点一致（Δ=0），
      与 UIA 的 BoundingRectangle 完全对齐。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import time
from typing import Optional, Tuple

from . import dpi, geom, winapi as w

_RUNTIME_DIR = os.path.join(os.environ.get("TEMP") or os.environ.get("TMP") or ".",
                            "screen-agent")


def runtime_dir() -> str:
    os.makedirs(_RUNTIME_DIR, exist_ok=True)
    return _RUNTIME_DIR


def _mss():
    import mss
    return mss


def monitors() -> list:
    with _mss().MSS() as sct:
        return [dict(m) for m in sct.monitors]


def grab_raw(region: Optional[dict] = None):
    """抓取原始 BGRA 缓冲。region 为 {left, top, width, height}；None = 整个虚拟桌面。"""
    with _mss().MSS() as sct:
        target = dict(region) if region else dict(sct.monitors[0])
        shot = sct.grab(target)
        return shot


def grab_pil(region: Optional[dict] = None):
    """返回 PIL.Image（RGB）与它对应的屏幕矩形。"""
    from PIL import Image
    shot = grab_raw(region)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    rect = (shot.left, shot.top, shot.left + shot.width, shot.top + shot.height)
    return img, rect


def grab_array(region: Optional[dict] = None):
    """返回 numpy 数组（H,W,3 RGB），供 OCR/视觉算法使用。"""
    import numpy as np
    shot = grab_raw(region)
    arr = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)
    return arr, (shot.left, shot.top, shot.left + shot.width, shot.top + shot.height)


def save(region: Optional[dict] = None, path: Optional[str] = None,
         prefix: str = "shot") -> Tuple[str, Tuple[int, int, int, int]]:
    """截图并保存为 PNG。返回 (文件路径, 屏幕矩形)。"""
    img, rect = grab_pil(region)
    if path is None:
        path = os.path.join(runtime_dir(),
                            f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}_{os.getpid()}.png")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path)
    return path, rect


def window_region(hwnd: int, pad: int = 0) -> Optional[dict]:
    """取得某个窗口的屏幕矩形（物理像素）。窗口最小化时返回 None。"""
    if not hwnd or not w.user32.IsWindow(hwnd):
        return None
    if w.user32.IsIconic(hwnd):
        return None
    r = wt.RECT()
    if not w.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    l, t, rr, b = r.left - pad, r.top - pad, r.right + pad, r.bottom + pad
    vd = dpi.virtual_desktop()
    # 裁剪到虚拟桌面，避免超出范围导致 mss 报错
    l = max(l, vd["left"])
    t = max(t, vd["top"])
    rr = min(rr, vd["left"] + vd["width"])
    b = min(b, vd["top"] + vd["height"])
    if rr <= l or b <= t:
        return None
    return {"left": l, "top": t, "width": rr - l, "height": b - t}


def pixel(x: int, y: int) -> Optional[Tuple[int, int, int]]:
    """读取屏幕上某一点的颜色（用 GDI，最快）。"""
    hdc = w.user32.GetDC(None)
    if not hdc:
        return None
    try:
        v = w.gdi32.GetPixel(hdc, int(x), int(y))
        if v == 0xFFFFFFFF:
            return None
        return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    finally:
        w.user32.ReleaseDC(None, hdc)


def region_rect(region) -> Tuple[int, int, int, int]:
    if region is None:
        vd = dpi.virtual_desktop()
        return (vd["left"], vd["top"], vd["left"] + vd["width"], vd["top"] + vd["height"])
    return geom.to_rect(region)
