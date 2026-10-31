"""鼠标输入：移动 + 左/右键点击 + 双击 + 滚轮。

★ 关键实现决策（实测得出，勿轻易改动）
  1. 移动光标用 SetCursorPos，不用 SendInput 的 MOUSEEVENTF_ABSOLUTE。
     原因：MOUSEEVENTF_ABSOLUTE 的归一化空间是「主显示器」而非虚拟桌面。
     本机主屏 2560 宽 / 虚拟桌面 5120 宽，若按虚拟桌面归一化，X 坐标会精确地
     减半（实测目标 300 → 实际 150），是极隐蔽的错位 bug。
     SetCursorPos 直接吃物理像素，双屏实测偏差 0px。
  2. 点击用 SendInput 只发 DOWN/UP（不带 MOVE 标志），作用于当前光标位置。
     SendInput 比 mouse_event 更底层，会生成完整的输入事件流，兼容性更好。
  3. 移动与点击之间留少量延时，否则部分应用（尤其 Electron / 游戏）会丢事件。
"""
from __future__ import annotations

import ctypes
import time
from typing import Optional, Tuple

from . import dpi, winapi as w

LEFT, RIGHT, MIDDLE = "left", "right", "middle"
_BTN_FLAGS = {
    LEFT: (w.MOUSEEVENTF_LEFTDOWN, w.MOUSEEVENTF_LEFTUP),
    RIGHT: (w.MOUSEEVENTF_RIGHTDOWN, w.MOUSEEVENTF_RIGHTUP),
    MIDDLE: (w.MOUSEEVENTF_MIDDLEDOWN, w.MOUSEEVENTF_MIDDLEUP),
}

# 最近一次操作记录，便于出错追溯
_last_action: Optional[dict] = None


def last_action() -> Optional[dict]:
    return _last_action


def _record(**kw):
    global _last_action
    kw["ts"] = time.time()
    _last_action = kw


def cursor_pos() -> Tuple[int, int]:
    p = ctypes.wintypes.POINT()
    w.user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def _send(flags: int, data: int = 0) -> bool:
    """发送一个鼠标事件（不含坐标，作用于当前光标位置）。"""
    inp = w.INPUT(type=0)
    inp.u.mi = w.MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flags,
                            time=0, dwExtraInfo=None)
    ctypes.set_last_error(0)
    n = w.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(w.INPUT))
    if n != 1:
        raise OSError(f"SendInput 失败 flags={flags:#x} err={ctypes.get_last_error()}")
    return True


def move(x: int, y: int, duration: float = 0.0, settle: float = 0.04) -> Tuple[int, int]:
    """把光标移到 (x, y)。duration > 0 时做平滑移动。返回实际落点。"""
    x, y = int(round(x)), int(round(y))
    if duration and duration > 0:
        x0, y0 = cursor_pos()
        steps = max(2, min(30, int(duration / 0.012)))
        for i in range(1, steps + 1):
            t = i / steps
            # ease-out，避免机械感的匀速
            t = 1 - (1 - t) ** 2
            w.user32.SetCursorPos(int(round(x0 + (x - x0) * t)),
                                  int(round(y0 + (y - y0) * t)))
            time.sleep(duration / steps)
    else:
        if not w.user32.SetCursorPos(x, y):
            raise OSError(f"SetCursorPos({x}, {y}) 失败 err={w.get_last_error()}")
    if settle:
        time.sleep(settle)
    actual = cursor_pos()
    _record(action="move", x=x, y=y, actual=list(actual))
    return actual


def click(x: int, y: int, button: str = LEFT, count: int = 1,
          duration: float = 0.0, down_up_gap: float = 0.03,
          between_gap: Optional[float] = None, dry_run: bool = False,
          keep_cursor: bool = False) -> dict:
    """在 (x, y) 点击。

    坐标必须先经调用方校验；本函数只保证「移动到位 → 按下 → 抬起」的时序。
    dry_run=True 时只移动光标并回报，**不发送按键**，且默认会把光标移回原位
    （keep_cursor=True 可保留在新位置）。
    between_gap=None 时按系统双击时间动态计算（见 _safe_gap）。
    """
    button = (button or LEFT).lower()
    if button not in _BTN_FLAGS:
        raise ValueError(f"不支持的按键: {button!r}，可选 left/right/middle")
    x, y = int(round(x)), int(round(y))

    if not dpi.in_virtual_desktop(x, y, margin=2):
        vd = dpi.virtual_desktop()
        raise ValueError(
            f"目标坐标 ({x},{y}) 不在虚拟桌面范围内 "
            f"[{vd['left']},{vd['top']} - {vd['left']+vd['width']},{vd['top']+vd['height']}]")

    if between_gap is None:
        between_gap = _safe_gap() if count > 1 else 0.09

    before = cursor_pos()
    actual = move(x, y, duration=duration)

    if dry_run:
        # 演练不该把用户的光标留在屏幕另一端 —— 除非明确要求保留
        if not keep_cursor:
            try:
                w.user32.SetCursorPos(before[0], before[1])
            except Exception:
                pass
        res = {"action": "click", "dry_run": True, "target": [x, y],
               "actual": list(actual), "button": button, "count": count,
               "cursor_before": list(before),
               "cursor_after": list(cursor_pos()),
               "cursor_restored": bool(not keep_cursor)}
        _record(**res)
        return res

    down, up = _BTN_FLAGS[button]
    sent = 0
    for i in range(max(1, count)):
        # 必须保证抬起：中途 SendInput 失败会把鼠标键遗留在按下状态，
        # 之后用户的每一次点击都会变成"拖不动也松不开"的死键。
        _send(down)
        try:
            time.sleep(down_up_gap)
        finally:
            _send(up)
        sent += 1
        if i < count - 1:
            # 双击间隔需小于系统双击时间，否则应用会识别为两次单击
            time.sleep(between_gap)

    res = {"action": "click", "dry_run": False, "target": [x, y],
           "actual": list(actual), "button": button, "count": sent,
           "cursor_before": list(before),
           "offset": [actual[0] - x, actual[1] - y]}
    _record(**res)
    return res


def double_click(x: int, y: int, button: str = LEFT, **kw) -> dict:
    return click(x, y, button=button, count=2, **kw)


def hover(x: int, y: int, duration: float = 0.0) -> dict:
    actual = move(x, y, duration=duration)
    res = {"action": "hover", "target": [x, y], "actual": list(actual)}
    _record(**res)
    return res


def scroll(amount: int, x: Optional[int] = None, y: Optional[int] = None) -> dict:
    """滚轮。amount > 0 向上，< 0 向下（单位：120 的倍数，一格=120）。"""
    if x is not None and y is not None:
        move(x, y)
    data = int(amount)
    _send(w.MOUSEEVENTF_WHEEL, data)
    res = {"action": "scroll", "amount": data, "at": list(cursor_pos())}
    _record(**res)
    return res


def double_click_time() -> int:
    return int(w.user32.GetDoubleClickTime())


def _safe_gap(default: float = 0.09) -> float:
    """双击间隔：必须短于系统双击时间，否则应用会识别成两次单击。

    用户可以在「鼠标设置」里把双击速度调快或调慢，写死间隔会静默失效
    （表现为「双击变成了单击」，极难排查）。
    """
    try:
        limit = double_click_time() / 1000.0
    except Exception:
        return default
    if limit <= 0:
        return default
    return max(0.02, min(default, limit * 0.4))


def _set_pos(x: int, y: int) -> None:
    if not w.user32.SetCursorPos(int(round(x)), int(round(y))):
        raise OSError(f"SetCursorPos({x},{y}) 失败 err={w.get_last_error()}")


def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.45,
         button: str = LEFT, steps: int = 0, hold_before: float = 0.14,
         hold_after: float = 0.16, ease: bool = True,
         dry_run: bool = False) -> dict:
    """按住起点拖到终点再松开。

    实现要点（都是踩过的坑）：
      · 按下后必须 **hold_before** 一段再移动 —— 很多应用（窗口拖动、
        文件拖放、滑块）要等一小会儿才进入拖拽态，立刻移动会被当成点击。
      · 移动被拆成 **多步**，且步间有延时 —— 只发一次瞬时位移，
        应用侧收到的是「瞬移」，识别不出拖拽。步数随距离自动估算。
      · 松手前 **hold_after** 让目标应用有机会完成 drop 判定。
      · 中间位移用 SetCursorPos 而非 SendInput 绝对坐标（后者在多显示器
        下归一化空间是主显示器，会导致横向位移减半，见 mouse.click 注释）。

    注意：本函数生成的是**程序化直线轨迹**，可能被图形验证码/风控
    判定为机器人。滑块类人机验证请交给用户手动完成（见 core/human.py）。
    """
    button = (button or LEFT).lower()
    if button not in _BTN_FLAGS:
        raise ValueError(f"不支持的按键: {button!r}")
    x1, y1, x2, y2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
    for (px, py) in ((x1, y1), (x2, y2)):
        if not dpi.in_virtual_desktop(px, py, margin=2):
            vd = dpi.virtual_desktop()
            raise ValueError(f"坐标 ({px},{py}) 不在虚拟桌面内 {vd}")

    dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if steps <= 0:
        steps = max(6, min(120, int(dist / 12) or 6))

    _set_pos(x1, y1)
    time.sleep(0.05)
    before = cursor_pos()

    if dry_run:
        res = {"action": "drag", "dry_run": True, "from": [x1, y1], "to": [x2, y2],
               "distance": round(dist, 1), "steps": steps, "button": button}
        _record(**res)
        return res

    down, up = _BTN_FLAGS[button]
    _send(down)
    try:
        time.sleep(hold_before)
        per = duration / steps
        for i in range(1, steps + 1):
            t = i / steps
            if ease:
                t = t * t * (3 - 2 * t)          # smoothstep，起止更自然
            _set_pos(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            time.sleep(per)
        time.sleep(hold_after)
    finally:
        # 拖拽中途失败若不抬起，鼠标键会永久卡住，用户整机无法正常点击
        try:
            _send(up)
        except Exception:
            pass
    actual = cursor_pos()
    res = {"action": "drag", "dry_run": False, "from": [x1, y1], "to": [x2, y2],
           "actual": list(actual), "distance": round(dist, 1), "steps": steps,
           "button": button, "cursor_before": list(before)}
    _record(**res)
    return res


def swipe(x1: int, y1: int, x2: int, y2: int, duration: float = 0.18,
          **kw) -> dict:
    """快速滑动（较短的 duration + 较少的保持时间），适合列表滚动、翻页。"""
    kw.setdefault("hold_before", 0.05)
    kw.setdefault("hold_after", 0.05)
    res = drag(x1, y1, x2, y2, duration=duration, **kw)
    res["action"] = "swipe"
    return res


def drag_between(src_elem: dict, dst_elem: dict, duration: float = 0.5,
                 **kw) -> dict:
    """在两个元素之间拖拽（按元素中心点）。"""
    if not src_elem or not dst_elem:
        raise ValueError("drag_between 需要两个元素")
    sx, sy = src_elem["center"]
    dx, dy = dst_elem["center"]
    res = drag(sx, sy, dx, dy, duration=duration, **kw)
    res["from_element"] = src_elem.get("id")
    res["to_element"] = dst_elem.get("id")
    return res


def save_cursor_position() -> Tuple[int, int]:
    return cursor_pos()


def restore_cursor_position(pos: Tuple[int, int]) -> None:
    try:
        w.user32.SetCursorPos(int(pos[0]), int(pos[1]))
    except Exception:
        pass
