"""几何工具：矩形归一化、点包含、坐标校验。

统一约定：矩形用 (left, top, right, bottom) 的物理像素四元组表示，
         点用 (x, y) 物理像素表示。全项目不做任何逻辑像素换算。
"""
from __future__ import annotations

from typing import Iterable, Tuple


def to_rect(v) -> Tuple[int, int, int, int]:
    """把各种形态的矩形输入归一化成 (l, t, r, b)。"""
    if v is None:
        return (0, 0, 0, 0)
    if isinstance(v, dict):
        if "left" in v:
            return (int(v["left"]), int(v["top"]),
                    int(v["left"]) + int(v.get("width", 0)),
                    int(v["top"]) + int(v.get("height", 0)))
        return (int(v.get("l", 0)), int(v.get("t", 0)),
                int(v.get("r", 0)), int(v.get("b", 0)))
    if hasattr(v, "left") and hasattr(v, "right"):
        return (int(v.left), int(v.top), int(v.right), int(v.bottom))
    seq = list(v)
    if len(seq) == 4:
        return tuple(int(x) for x in seq)  # type: ignore[return-value]
    raise ValueError(f"无法解析矩形: {v!r}")


def width(rect) -> int:
    l, t, r, b = to_rect(rect)
    return max(0, r - l)


def height(rect) -> int:
    l, t, r, b = to_rect(rect)
    return max(0, b - t)


def center(rect) -> Tuple[int, int]:
    l, t, r, b = to_rect(rect)
    return ((l + r) // 2, (t + b) // 2)


def intersect(a, b) -> bool:
    al, at, ar, ab = to_rect(a)
    bl, bt, br, bb = to_rect(b)
    return not (ar <= bl or br <= al or ab <= bt or bb <= at)


def contains_point(rect, x: int, y: int) -> bool:
    l, t, r, b = to_rect(rect)
    return l <= x < r and t <= y < b


def union(rects: Iterable) -> Tuple[int, int, int, int]:
    rs = [to_rect(r) for r in rects if width(r) > 0 and height(r) > 0]
    if not rs:
        return (0, 0, 0, 0)
    return (min(r[0] for r in rs), min(r[1] for r in rs),
            max(r[2] for r in rs), max(r[3] for r in rs))


def clamp_point(x: int, y: int, rect) -> Tuple[int, int]:
    """把一个点夹取到矩形内（留 1px 边距），用于避免点到边框外触发窗口缩放。"""
    l, t, r, b = to_rect(rect)
    if r <= l or b <= t:
        return (x, y)
    pad = 1 if (r - l) > 4 and (b - t) > 4 else 0
    return (min(max(x, l + pad), r - 1 - pad), min(max(y, t + pad), b - 1 - pad))


def as_list(rect) -> list:
    return list(to_rect(rect))
