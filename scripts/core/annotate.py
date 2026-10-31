"""把扫描/OCR 结果画到截图上，生成可被人和 Agent 直接查看的标注图。

配色约定（高对比，屏幕内容之上仍清晰可辨）：
  青色  = 可点击元素
  橙色  = 不可点击 / 已禁用
  红色  = 坐标不可信（需改用视觉方案）
  紫色  = OCR 识别到的文字块
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

from . import geom

C_CLICK = (0, 200, 200)
C_DISABLED = (255, 150, 0)
C_SUSPECT = (240, 50, 50)
C_TEXT = (150, 150, 150)
C_OCR = (190, 90, 220)
C_LABEL_BG = (12, 16, 22)
C_LABEL_FG = (255, 255, 255)


def _font(size: int = 13):
    from PIL import ImageFont
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def _draw_box(draw, rect, screen_rect, color, width=2, fill_alpha=None, img=None):
    l, t, r, b = geom.to_rect(rect)
    ox, oy = screen_rect[0], screen_rect[1]
    box = [l - ox, t - oy, r - ox - 1, b - oy - 1]
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    if fill_alpha and img is not None:
        try:
            from PIL import ImageDraw
            overlay = ImageDraw.Draw(img, "RGBA")
            overlay.rectangle(box, fill=color + (fill_alpha,))
        except Exception:
            pass
    draw.rectangle(box, outline=color + (255,), width=width)
    return box


def _label(draw, box, text, color, font):
    if box is None:
        return
    x, y = box[0], box[1]
    try:
        tb = draw.textbbox((0, 0), text, font=font)
        w, h = tb[2] - tb[0] + 8, tb[3] - tb[1] + 6
    except Exception:
        w, h = 8 * len(text) + 8, 18
    ly = y - h if y - h >= 0 else y + 2
    draw.rectangle([x, ly, x + w, ly + h], fill=C_LABEL_BG + (235,))
    draw.rectangle([x, ly, x + 2, ly + h], fill=color + (255,))
    draw.text((x + 5, ly + 2), text, fill=C_LABEL_FG, font=font)


def annotate(img, screen_rect: Tuple[int, int, int, int], elements: List[dict],
             show_labels: bool = True, only_clickable: bool = False,
             max_labels: int = 200, ocr_blocks: Optional[List[dict]] = None):
    """在 PIL 图像上绘制元素框。screen_rect 为该图像对应的屏幕矩形。"""
    from PIL import ImageDraw
    if img.mode != "RGBA":
        base = img.convert("RGBA")
    else:
        base = img
    draw = ImageDraw.Draw(base, "RGBA")
    font = _font(13)

    shown = 0
    for e in elements:
        if only_clickable and not e.get("clickable"):
            continue
        plausible = e.get("plausible", True)
        if not plausible:
            color = C_SUSPECT
        elif not e.get("enabled", True) or not e.get("clickable"):
            color = C_DISABLED
        else:
            color = C_CLICK
        box = _draw_box(draw, e["rect"], screen_rect, color, width=2, img=base)
        if show_labels and box and shown < max_labels:
            tag = e.get("id") or e.get("role", "")
            nm = (e.get("name") or "").replace("\n", " ")[:16]
            text = f"{tag} {nm}".strip()
            _label(draw, box, text, color, font)
            shown += 1

    for b in (ocr_blocks or []):
        rect = b.get("rect")
        if not rect:
            continue
        box = _draw_box(draw, rect, screen_rect, C_OCR, width=1, img=base)
        if box and show_labels and shown < max_labels:
            _label(draw, box, (b.get("text") or "")[:18], C_OCR, font)
            shown += 1

    return base.convert("RGB") if img.mode != "RGBA" else base


def annotate_and_save(img, screen_rect, elements, path: str, **kw) -> str:
    out = annotate(img, screen_rect, elements, **kw)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    out.save(path)
    return path


def draw_cursor(img, screen_rect, pos: Tuple[int, int]):
    """标记光标当前位置（mss 截图不含光标）。"""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img.convert("RGBA") if img.mode != "RGBA" else img, "RGBA")
    x, y = pos[0] - screen_rect[0], pos[1] - screen_rect[1]
    if not (0 <= x < img.width and 0 <= y < img.height):
        return img
    draw.ellipse([x - 12, y - 12, x + 12, y + 12], outline=(255, 60, 60, 255), width=2)
    draw.line([x - 16, y, x + 16, y], fill=(255, 60, 60, 255), width=1)
    draw.line([x, y - 16, x, y + 16], fill=(255, 60, 60, 255), width=1)
    return img


def draw_grid(img, screen_rect, step: int = 200):
    """叠加坐标网格，便于人工核对坐标。"""
    from PIL import ImageDraw
    base = img.convert("RGBA") if img.mode != "RGBA" else img
    draw = ImageDraw.Draw(base, "RGBA")
    font = _font(11)
    ox, oy = screen_rect[0], screen_rect[1]
    for gx in range(0, base.width, step):
        sx = ox + gx
        draw.line([gx, 0, gx, base.height], fill=(255, 80, 80, 90), width=1)
        draw.text((gx + 3, 3), str(sx), fill=(255, 200, 200, 230), font=font)
    for gy in range(0, base.height, step):
        sy = oy + gy
        draw.line([0, gy, base.width, gy], fill=(255, 80, 80, 90), width=1)
        draw.text((3, gy + 3), str(sy), fill=(255, 200, 200, 230), font=font)
    return base.convert("RGB") if img.mode != "RGBA" else base
