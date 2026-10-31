"""OCR 视觉兜底层 —— 当 UIA 拿不到控件树时（游戏、自绘界面、远程桌面、
部分 WinUI 应用、Canvas 渲染的应用），用文字识别来定位屏幕上的可点击目标。

引擎按可用性自动选择，都没有时本模块整体降级为空操作，不影响主流程：
  1) RapidOCR (onnxruntime) —— 中文效果好，离线，无需额外安装 exe
  2) Windows 内置 OCR —— 系统自带模型，零下载
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
from typing import List, Optional

from . import capture, geom

_engine = None
_engine_name = "unavailable"
_checked = False
_quiet_done = False


def _silence():
    """抑制 RapidOCR/onnxruntime 的日志，避免污染 stdout 的 JSON 输出。

    RapidOCR 在初始化时会大量输出 INFO 级日志（走 stderr），且它自己的 logger
    往往在 import 前就已配置好 handler，逐名 setLevel 并不可靠。
    这里用 logging.disable(INFO) 全局屏蔽 INFO 及以下，保留 WARNING/ERROR
    （真实故障仍可见），再配合调用期间重定向 stdout 兜住直接 print 的情况。
    """
    global _quiet_done
    if _quiet_done:
        return
    _quiet_done = True
    try:
        logging.disable(logging.INFO)
    except Exception:
        pass
    for name in ("RapidOCR", "rapidocr", "onnxruntime"):
        try:
            lg = logging.getLogger(name)
            lg.setLevel(logging.ERROR)
            lg.propagate = False
            for h in list(lg.handlers):
                lg.removeHandler(h)
        except Exception:
            pass
    # 兜底：关闭所有名字里带 rapid / ocr 的 logger
    for name in list(logging.root.manager.loggerDict.keys()):
        low = name.lower()
        if "rapid" in low or "ocr" in low:
            try:
                lg = logging.getLogger(name)
                lg.disabled = True
                lg.setLevel(logging.CRITICAL)
                for h in list(lg.handlers):
                    lg.removeHandler(h)
            except Exception:
                pass


@contextlib.contextmanager
def _quiet_stdout():
    """临时吞掉第三方库直接 print 的内容。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def _detect():
    global _engine, _engine_name, _checked
    if _checked:
        return
    _checked = True
    _silence()

    # --- 1) RapidOCR ---
    for modname, ctor in (("rapidocr", "RapidOCR"),):
        try:
            with _quiet_stdout():
                mod = __import__(modname, fromlist=[ctor])
                eng = getattr(mod, ctor)()
            _engine = ("rapidocr", eng)
            _engine_name = "rapidocr"
            return
        except Exception:
            continue
    try:
        with _quiet_stdout():
            from rapidocr_onnxruntime import RapidOCR as _R  # type: ignore
            _engine = ("rapidocr", _R())
        _engine_name = "rapidocr-onnxruntime"
        return
    except Exception:
        pass

    # --- 2) Windows 内置 OCR ---
    try:
        import winsdk.windows.media.ocr  # noqa: F401
        _engine = ("winsdk", None)
        _engine_name = "windows-ocr"
        return
    except Exception:
        pass

    _engine_name = "unavailable"


def available() -> bool:
    _detect()
    return _engine_name != "unavailable"


def engine_name() -> str:
    _detect()
    return _engine_name


def status() -> dict:
    _detect()
    return {
        "available": available(),
        "engine": _engine_name,
        "hint": ("" if available() else
                 "未安装 OCR 引擎。可执行： <python> -m pip install rapidocr "
                 "（约需下载 onnxruntime 与中文识别模型，一次性）"),
    }


def _rapidocr_boxes(arr) -> List[dict]:
    kind, eng = _engine
    out: List[dict] = []
    if kind == "rapidocr":
        res = eng(arr)
        boxes = getattr(res, "boxes", None)
        txts = getattr(res, "txts", None)
        scores = getattr(res, "scores", None)
        if boxes is None or txts is None:
            return out
        for i, poly in enumerate(boxes):
            try:
                xs = [float(p[0]) for p in poly]
                ys = [float(p[1]) for p in poly]
            except Exception:
                continue
            out.append({
                "text": str(txts[i]),
                "score": float(scores[i]) if scores is not None else None,
                "rect": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
            })
    return out


def _winsdk_boxes(arr) -> List[dict]:
    """Windows 内置 OCR（异步 API 的同步包装）。"""
    import asyncio
    import numpy as np
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.security.cryptography import CryptographicBuffer

    async def run():
        h, w, _ = arr.shape
        buf = arr.tobytes()
        sb = SoftwareBitmap.create_copy_from_buffer(
            CryptographicBuffer.create_from_byte_array(buf),
            BitmapPixelFormat.RGBA8, w, h, BitmapAlphaMode.PREMULTIPLIED)
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            eng = OcrEngine.try_create_from_language(Language("zh-Hans-CN"))
        if eng is None:
            return []
        result = await eng.recognize_async(sb)
        items = []
        for line in result.lines:
            rects = []
            for word in line.words:
                r = word.bounding_rect
                rects.append([int(r.x), int(r.y), int(r.x + r.width), int(r.y + r.height)])
            if not rects:
                continue
            l = min(r[0] for r in rects)
            t = min(r[1] for r in rects)
            rr = max(r[2] for r in rects)
            b = max(r[3] for r in rects)
            items.append({"text": line.text, "score": None, "rect": [l, t, rr, b]})
        return items

    # RGBA8 而非 RGB
    if arr.shape[2] == 3:
        alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
        arr = np.concatenate([arr, alpha], axis=2)
    return asyncio.run(run())


def recognize(region=None, min_score: float = 0.5,
              max_results: int = 200) -> List[dict]:
    """识别屏幕（或指定区域）内的文字，返回带屏幕绝对坐标的文字块。

    返回 [{text, score, rect: [l,t,r,b], center: [x,y]}]
    """
    _detect()
    if not available():
        return []
    arr, screen_rect = capture.grab_array(region)
    try:
        with _quiet_stdout():
            if _engine[0] == "rapidocr":
                raw = _rapidocr_boxes(arr)
            elif _engine[0] == "winsdk":
                raw = _winsdk_boxes(arr)
            else:
                raw = []
    except Exception as e:
        return [{"error": f"OCR 失败: {type(e).__name__}: {e}"}]

    ox, oy = screen_rect[0], screen_rect[1]
    out: List[dict] = []
    for b in raw:
        if b.get("score") is not None and b["score"] < min_score:
            continue
        l, t, r, bo = b["rect"]
        rect = [l + ox, t + oy, r + ox, bo + oy]
        out.append({
            "text": b["text"],
            "score": b.get("score"),
            "rect": rect,
            "center": list(geom.center(rect)),
        })
    out.sort(key=lambda x: (x["rect"][1], x["rect"][0]))
    return out[:max_results]


def find_text(text: str, region=None, exact: bool = False,
              min_score: float = 0.5) -> List[dict]:
    """在屏幕上按文字查找目标块。"""
    if not text:
        return []
    tl = text.lower()
    hits = []
    for b in recognize(region=region, min_score=min_score):
        if b.get("error"):
            continue
        bt = (b.get("text") or "").lower()
        if (bt == tl) if exact else (tl in bt):
            hits.append(b)
    return hits
