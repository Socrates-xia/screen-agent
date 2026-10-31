"""UI Automation 元素扫描 —— 屏幕「结构化理解」的主通道。

为什么不纯靠视觉识别：
  UIA 直接向应用索取控件树（类型 / 名称 / 边界框 / 启用状态），
  对原生 Windows 应用、WPF、WinUI、Electron（预热后）都能拿到精确坐标，
  远比截图 + 模板/OCR 猜测可靠，且能读懂「这是个输入框还是链接」。

实测结论（本机）：
  · 自建 Win32 窗口：UIA 坐标 == GetWindowRect == 截图采样，偏差 0px —— 完全可信。
  · Win11 记事本（WinUI/XAML）：UIA 给出 (-5616,791) 这类屏幕外负坐标 —— 不可信。
    → 因此每个元素都要过一遍 rect_in_virtual_desktop() 合理性校验，
      不合格的标记 plausible=False 并引导调用方走视觉兜底。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from typing import Dict, List, Optional

from . import dpi, geom, winapi as w

_INTERACTIVE = {
    "ButtonControl", "EditControl", "HyperlinkControl", "CheckBoxControl",
    "RadioButtonControl", "ComboBoxControl", "ListItemControl", "MenuItemControl",
    "TabItemControl", "TreeItemControl", "SliderControl", "SpinnerControl",
    "SplitButtonControl", "DataItemControl", "HeaderItemControl", "ThumbControl",
    "CustomControl", "GroupControl",
}
# 明确可点击的（CustomControl/GroupControl 需要额外判断，先单列）
_CLICKABLE = {
    "ButtonControl", "EditControl", "HyperlinkControl", "CheckBoxControl",
    "RadioButtonControl", "ComboBoxControl", "ListItemControl", "MenuItemControl",
    "TabItemControl", "TreeItemControl", "SplitButtonControl", "DataItemControl",
    "HeaderItemControl",
}
_TEXTUAL = {"TextControl", "DocumentControl"}

_ROLE = {
    "ButtonControl": "button", "EditControl": "input", "HyperlinkControl": "link",
    "CheckBoxControl": "checkbox", "RadioButtonControl": "radio",
    "ComboBoxControl": "dropdown", "ListItemControl": "listitem",
    "MenuItemControl": "menuitem", "TabItemControl": "tab",
    "TreeItemControl": "treeitem", "SliderControl": "slider",
    "SpinnerControl": "spinner", "SplitButtonControl": "splitbutton",
    "DataItemControl": "dataitem", "HeaderItemControl": "header",
    "ThumbControl": "scrollbar", "TextControl": "text",
    "DocumentControl": "document", "WindowControl": "window",
    "PaneControl": "pane", "GroupControl": "group", "CustomControl": "custom",
    "TitleBarControl": "titlebar", "MenuBarControl": "menubar",
    "ToolBarControl": "toolbar", "ScrollBarControl": "scrollbar",
}

_auto = None
_silenced = False


def _lazy():
    """延迟导入 uiautomation —— 必须发生在 core/__init__ 完成 DPI 设置之后。"""
    global _auto, _silenced
    if _auto is None:
        import uiautomation as auto
        # 静音库自身的日志，避免污染 stdout 的 JSON 输出
        try:
            auto.Logger.WriteLine = staticmethod(lambda *a, **k: None)
            auto.Logger.Write = staticmethod(lambda *a, **k: None)
        except Exception:
            pass
        try:
            auto.SetGlobalSearchTimeout(2.0)
        except Exception:
            pass
        _auto = auto
        _silenced = True
    return _auto


# ------------------------------------------------------------------ 窗口
def list_windows(visible_only: bool = True, titled_only: bool = True,
                 include_minimized: bool = True) -> List[dict]:
    """枚举顶层窗口。"""
    out: List[dict] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, lparam):
        h = int(hwnd) if hwnd else 0
        if not h:
            return True
        if visible_only and not w.user32.IsWindowVisible(h):
            return True
        n = w.user32.GetWindowTextLengthW(h)
        if titled_only and n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        w.user32.GetWindowTextW(h, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        w.user32.GetClassNameW(h, cls, 256)
        icon = bool(w.user32.IsIconic(h))
        if not include_minimized and icon:
            return True
        r = wt.RECT()
        w.user32.GetWindowRect(h, ctypes.byref(r))
        pid = ctypes.c_ulong()
        w.user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        out.append({
            "hwnd": h,
            "title": buf.value,
            "class_name": cls.value,
            "pid": pid.value,
            "minimized": icon,
            "rect": [r.left, r.top, r.right, r.bottom],
            "size": [max(0, r.right - r.left), max(0, r.bottom - r.top)],
            "foreground": h == int(w.user32.GetForegroundWindow() or 0),
        })
        return True

    w.user32.EnumWindows(WNDENUMPROC(_cb), None)
    return out


def find_window(title: Optional[str] = None, class_name: Optional[str] = None,
                hwnd: Optional[int] = None, exact: bool = False) -> Optional[int]:
    """按标题/类名/句柄定位窗口，模糊匹配优先取前台窗口。"""
    if hwnd:
        h = int(hwnd)
        return h if w.user32.IsWindow(h) else None
    wins = list_windows(visible_only=True, titled_only=True)
    cands = []
    for win in wins:
        if title is not None:
            t = win["title"]
            ok = (t == title) if exact else (title.lower() in t.lower())
            if not ok:
                continue
        if class_name is not None and class_name.lower() not in win["class_name"].lower():
            continue
        cands.append(win)
    if not cands:
        return None
    for c in cands:
        if c["foreground"]:
            return c["hwnd"]
    return cands[0]["hwnd"]


def foreground_window() -> Optional[int]:
    h = w.user32.GetForegroundWindow()
    return int(h) if h else None


def window_rect(hwnd: int) -> Optional[List[int]]:
    if not hwnd or not w.user32.IsWindow(int(hwnd)):
        return None
    r = wt.RECT()
    if not w.user32.GetWindowRect(int(hwnd), ctypes.byref(r)):
        return None
    return [r.left, r.top, r.right, r.bottom]


def _force_foreground(h: int) -> bool:
    """用 AttachThreadInput 抢前台。

    Windows 的前台锁定策略：非前台进程直接调 SetForegroundWindow 只会让任务栏
    图标闪烁，窗口不会被激活。把本线程的输入队列临时附加到当前前台窗口所在线程，
    就能取得设置前台的权限 —— 这是 Windows 官方文档认可的标准做法。
    """
    u, k = w.user32, w.kernel32
    fg = int(u.GetForegroundWindow() or 0)
    if fg == int(h):
        return True
    cur = int(k.GetCurrentThreadId())
    tgt = int(u.GetWindowThreadProcessId(fg, None) or 0) if fg else 0
    attached = False
    try:
        if tgt and tgt != cur:
            attached = bool(u.AttachThreadInput(cur, tgt, 1))
        u.BringWindowToTop(int(h))
        u.SetForegroundWindow(int(h))
        u.SetActiveWindow(int(h))
    except Exception:
        pass
    finally:
        if attached:
            try:
                u.AttachThreadInput(cur, tgt, 0)
            except Exception:
                pass
    time.sleep(0.12)
    return int(u.GetForegroundWindow() or 0) == int(h)


def activate(hwnd: int, restore_if_minimized: bool = True) -> bool:
    """把窗口切到前台。返回是否真的成为了前台窗口。

    失败时返回 False —— 调用方（尤其要发键盘的）必须据此中止，
    否则按键会落到别的窗口上。
    """
    h = int(hwnd)
    u, k = w.user32, w.kernel32
    if not u.IsWindow(h):
        return False
    if int(u.GetForegroundWindow() or 0) == h and not u.IsIconic(h):
        return True
    if u.IsIconic(h) and restore_if_minimized:
        u.ShowWindow(h, w.SW_RESTORE)
        time.sleep(0.3)

    if _force_foreground(h):
        return True

    # 兜底：最小化→还原能破除某些前台锁
    try:
        u.ShowWindow(h, 6)          # SW_MINIMIZE
        time.sleep(0.15)
        u.ShowWindow(h, w.SW_RESTORE)
        time.sleep(0.25)
    except Exception:
        pass
    if _force_foreground(h):
        return True
    return int(u.GetForegroundWindow() or 0) == h


# ------------------------------------------------------------------ 元素扫描
def _rect_of(ctrl):
    try:
        r = ctrl.BoundingRectangle
    except Exception:
        return None
    if r is None:
        return None
    try:
        return [int(r.left), int(r.top), int(r.right), int(r.bottom)]
    except Exception:
        return None


def scan(root_ctrl, max_nodes: int = 3000, max_depth: int = 14,
         include_text: bool = False, interactive_only: bool = True) -> List[dict]:
    """遍历控件树，返回元素描述列表（不含 id，id 由 session 层分配）。"""
    out: List[dict] = []
    visited = 0

    def walk(ctrl, depth: int, path: str):
        nonlocal visited
        if depth > max_depth or visited > max_nodes:
            return
        try:
            children = ctrl.GetChildren()
        except Exception:
            return
        for idx, c in enumerate(children):
            visited += 1
            if visited > max_nodes:
                return
            try:
                ctype = c.ControlTypeName or ""
            except Exception:
                ctype = ""
            want = (ctype in _INTERACTIVE) or (include_text and ctype in _TEXTUAL)
            if want:
                rect = _rect_of(c)
                if rect and rect[2] > rect[0] and rect[3] > rect[1]:
                    try:
                        name = c.Name or ""
                    except Exception:
                        name = ""
                    try:
                        aid = c.AutomationId or ""
                    except Exception:
                        aid = ""
                    try:
                        cls = c.ClassName or ""
                    except Exception:
                        cls = ""
                    try:
                        enabled = bool(c.IsEnabled)
                    except Exception:
                        enabled = True
                    try:
                        offscreen = bool(c.IsOffscreen)
                    except Exception:
                        offscreen = False
                    plausible = dpi.rect_in_virtual_desktop(*rect)
                    out.append({
                        "type": ctype,
                        "role": _ROLE.get(ctype, ctype.replace("Control", "").lower()),
                        "name": name.strip(),
                        "automation_id": aid,
                        "class_name": cls,
                        "rect": rect,
                        "center": list(geom.center(rect)),
                        "clickable": ctype in _CLICKABLE and enabled and plausible,
                        "enabled": enabled,
                        "offscreen": offscreen,
                        "plausible": plausible,
                        "depth": depth,
                        "path": f"{path}.{idx}",
                    })
            walk(c, depth + 1, f"{path}.{idx}")

    walk(root_ctrl, 0, "")
    return out


def scan_window(hwnd: int, **kw) -> List[dict]:
    """扫描单个窗口内的元素。自动处理 Electron/Chromium 的 accessibility 预热。"""
    auto = _lazy()
    h = int(hwnd)
    if not w.user32.IsWindow(h):
        return []
    root = None
    for attempt in range(3):
        try:
            root = auto.ControlFromHandle(h)
        except Exception:
            root = None
        if root is not None:
            break
        time.sleep(0.3)
    if root is None:
        return []

    items = scan(root, **kw)
    if items:
        return items

    # Chromium 系首次请求 UIA 时树是异步构建的，稍等再试一次
    for _ in range(2):
        time.sleep(0.6)
        items = scan(root, **kw)
        if items:
            return items
    return items


def scan_foreground(**kw) -> List[dict]:
    h = foreground_window()
    if not h:
        return []
    return scan_window(h, **kw)


def scan_all(max_windows: int = 12, **kw) -> List[dict]:
    """扫描所有可见窗口（前台优先），每个元素附带所属窗口信息。"""
    wins = list_windows(visible_only=True, titled_only=True, include_minimized=False)
    wins.sort(key=lambda x: (not x["foreground"], x["rect"][1], x["rect"][0]))
    out: List[dict] = []
    for win in wins[:max_windows]:
        for it in scan_window(win["hwnd"], **kw):
            it["window_hwnd"] = win["hwnd"]
            it["window_title"] = win["title"]
            it["pid"] = win["pid"]
            out.append(it)
    return out


def window_of_point(x: int, y: int) -> Optional[int]:
    """屏幕上某点下方的顶层窗口句柄。"""
    pt = wt.POINT(int(x), int(y))
    h = w.user32.WindowFromPoint(pt)
    if not h:
        return None
    root = w.user32.GetAncestor(h, w.GA_ROOT)
    return int(root or h)


def element_at(x: int, y: int) -> Optional[dict]:
    """用 UIA 取屏幕上某点下的元素（比整树扫描快得多，用于点击前复核）。

    注意：返回的是**屏幕最上层**该点处的元素。若目标被其他窗口遮挡，
    这里得到的是遮挡物的元素 —— 这正是我们要识别出来的情况。
    """
    auto = _lazy()
    try:
        el = auto.ControlFromPoint(int(x), int(y))
    except Exception:
        return None
    if el is None:
        return None
    rect = _rect_of(el)
    try:
        ctype = el.ControlTypeName or ""
    except Exception:
        ctype = ""
    try:
        nm = el.Name or ""
    except Exception:
        nm = ""
    try:
        cls = el.ClassName or ""
    except Exception:
        cls = ""
    return {
        "type": ctype,
        "name": nm.strip(),
        "class_name": cls,
        "rect": rect,
        "center": list(geom.center(rect)) if rect else None,
        "window_hwnd": window_of_point(x, y),
    }


def verify_point(x: int, y: int, expected: dict, tol: int = 2) -> str:
    """复核「该点下方现在是什么元素」，用于点击前的最后一道防线。

    返回值：
      match     —— 点下元素就是目标元素（或其子元素），可信
      ancestor  —— 点下是目标的父级容器，中性（UIA 粒度差异，不构成风险）
      occluded  —— 该点在**别的窗口**里了，目标已被遮挡，点下去会打到遮挡物
      unknown   —— 该点没有 UIA 元素（纯绘制区域），无法确认
      mismatch  —— 点下是同一窗口内的**另一个**元素，界面很可能已变化
    """
    exp_rect = expected.get("rect")
    if not exp_rect:
        return "unknown"
    got = element_at(x, y)
    if not got or not got.get("rect"):
        return "unknown"

    # 遮挡检查：该点已属于另一个顶层窗口 → 危险，必须拒绝
    exp_win = expected.get("window_hwnd")
    got_win = got.get("window_hwnd")
    if exp_win and got_win and int(exp_win) != int(got_win):
        return "occluded"

    gr = got["rect"]
    el, et, er, eb = exp_rect

    if gr[0] >= el - tol and gr[1] >= et - tol and gr[2] <= er + tol and gr[3] <= eb + tol:
        return "match"
    if gr[0] <= el and gr[1] <= et and gr[2] >= er and gr[3] >= eb:
        return "ancestor"
    # 无交集 = 明确错位
    if gr[2] <= el or er <= gr[0] or gr[3] <= et or eb <= gr[1]:
        return "mismatch"
    # 部分重叠：可能只是 UIA 粒度不同，按重叠面积判断
    ov_w = min(gr[2], er) - max(gr[0], el)
    ov_h = min(gr[3], eb) - max(gr[1], et)
    if ov_w > 0 and ov_h > 0:
        area_ratio = (ov_w * ov_h) / max(1, (gr[2] - gr[0]) * (gr[3] - gr[1]))
        return "match" if area_ratio > 0.5 else "mismatch"
    return "unknown"


def relocate(desc: dict, offset_in_window: Optional[List[int]] = None,
             timeout: float = 1.5) -> Optional[dict]:
    """点击前的「陈旧坐标防护」：按元素特征在窗口内重新查找，返回最新位置。

    desc 需要含 window_hwnd / type / name / automation_id / rect。
    返回 None 表示无法确认（调用方应据此拒绝点击或降级）。
    """
    hwnd = desc.get("window_hwnd")
    if not hwnd or not w.user32.IsWindow(int(hwnd)):
        return None
    want_type = desc.get("type")
    want_name = desc.get("name") or ""
    want_aid = desc.get("automation_id") or ""
    old_rect = desc.get("rect") or [0, 0, 0, 0]

    cur_win = window_rect(int(hwnd))
    if not cur_win:
        return None

    # 快路径：窗口没动 → 原坐标仍有效
    old_win = desc.get("window_rect")
    if old_win and cur_win == list(old_win):
        return {"rect": list(old_rect), "center": list(geom.center(old_rect)),
                "method": "window-unchanged"}

    # 中路径：窗口平移了 → 按位移换算（适合窗口内布局未变的情况）
    if old_win and offset_in_window is not None:
        dx = cur_win[0] - old_win[0]
        dy = cur_win[1] - old_win[1]
        moved = [old_rect[0] + dx, old_rect[1] + dy,
                 old_rect[2] + dx, old_rect[3] + dy]
        if dpi.rect_in_virtual_desktop(*moved):
            return {"rect": moved, "center": list(geom.center(moved)),
                    "method": "window-shift", "shift": [dx, dy]}

    # 慢路径：重新扫描该窗口，按特征匹配
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = scan_window(int(hwnd))
        best = None
        for it in items:
            if want_type and it["type"] != want_type:
                continue
            if want_aid and it.get("automation_id") == want_aid:
                best = it
                break
            if want_name and it.get("name") == want_name:
                # 名称可能重复，取离原位置最近的那个
                if best is None:
                    best = it
                else:
                    d_old = abs(it["center"][0] - old_rect[0]) + abs(it["center"][1] - old_rect[1])
                    d_bst = abs(best["center"][0] - old_rect[0]) + abs(best["center"][1] - old_rect[1])
                    if d_old < d_bst:
                        best = it
        if best:
            return {"rect": best["rect"], "center": best["center"],
                    "method": "uia-rescan", "matched": best}
        break
    return None


def tree_summary(hwnd: int, max_depth: int = 5) -> List[str]:
    """生成可读的控件树摘要（调试用）。"""
    auto = _lazy()
    root = auto.ControlFromHandle(int(hwnd))
    if root is None:
        return []
    lines: List[str] = []

    def walk(c, d=0):
        if d > max_depth:
            return
        try:
            ch = c.GetChildren()
        except Exception:
            return
        for k in ch:
            try:
                r = k.BoundingRectangle
                rs = f"({r.left},{r.top},{r.right},{r.bottom})"
            except Exception:
                rs = "(?)"
            lines.append(f"{'  '*d}{k.ControlTypeName} {k.Name[:40]!r} {rs}")
            walk(k, d + 1)

    walk(root)
    return lines
