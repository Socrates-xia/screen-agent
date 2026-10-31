"""浏览器（Edge / Chrome 等 Chromium 系）操作。

方案选择：**UIA + 快捷键**，而不是拉起一个全新的自动化浏览器实例。

为什么：用户要操作的是**自己那个已经登录了各种账号的 Edge**。
用 Playwright 启动的干净实例没有登录态，等于从零开始；
而 CDP 接管又要求 Edge 必须以调试端口启动（得先关掉现有浏览器）。
UIA + 快捷键这条路：零配置、不动用户现有会话、登录态原样保留。

能力边界：能做的 —— 开/关/切标签、导航、地址栏输入、页内查找、
            扫描并点击页面元素、读取页面文字。
          做不到的 —— 执行 JS、精确读 DOM 属性、等待网络空闲。
          需要后者时应改用 CDP/Playwright（见 references/design-notes.md）。
"""
from __future__ import annotations

import ctypes
import os
import time
from typing import Dict, List, Optional

from . import keyboard, uia, winapi as w

BROWSER_PROCS = {"msedge.exe": "Edge", "chrome.exe": "Chrome",
                 "brave.exe": "Brave", "vivaldi.exe": "Vivaldi",
                 "opera.exe": "Opera", "msedgewebview2.exe": "WebView2"}


def process_name(pid: int) -> str:
    """取进程可执行文件名（小写）。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = w.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if w.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        w.kernel32.CloseHandle(h)
    return ""


def _window_proc(hwnd: int) -> str:
    pid = ctypes.c_ulong()
    w.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return process_name(pid.value)


def find_browsers(kind: Optional[str] = None) -> List[dict]:
    """找出所有浏览器顶层窗口。kind 可为 'edge' / 'chrome' / None(全部)。"""
    out = []
    for win in uia.list_windows(visible_only=True, titled_only=True):
        cls = win["class_name"]
        if not cls.startswith("Chrome_WidgetWin"):
            continue
        proc = _window_proc(win["hwnd"])
        if proc not in BROWSER_PROCS:
            continue
        label = BROWSER_PROCS[proc]
        if kind and kind.lower() not in (label.lower(), proc.replace(".exe", "")):
            continue
        win = dict(win)
        win["browser"] = label
        win["process"] = proc
        out.append(win)
    return out


def find_browser(kind: str = "edge", title: Optional[str] = None) -> Optional[int]:
    """定位浏览器主窗口。多个窗口时优先取前台的。"""
    cands = find_browsers(kind)
    if title:
        cands = [c for c in cands if title.lower() in c["title"].lower()]
    if not cands:
        return None
    for c in cands:
        if c["foreground"]:
            return c["hwnd"]
    # 没有前台的，取面积最大的（通常是主窗口）
    cands.sort(key=lambda c: -(c["size"][0] * c["size"][1]))
    return cands[0]["hwnd"]


def selected_tab_rect(hwnd: int) -> Optional[List[int]]:
    """用 UIA 的 SelectionItemPattern 找出**当前选中**标签的矩形。

    比「拿窗口标题去猜」可靠 —— 窗口标题在多个标签时会被改写成
    “某标题 和另外 N 个页面”，靠字符串匹配容易认错。
    """
    try:
        import uiautomation as auto
        root = auto.ControlFromHandle(int(hwnd))
        if root is None:
            return None
        stack = [root]
        seen = 0
        while stack and seen < 800:
            c = stack.pop()
            seen += 1
            try:
                if c.ControlTypeName == "TabItemControl":
                    p = c.GetSelectionItemPattern()
                    if p is not None and p.IsSelected:
                        r = c.BoundingRectangle
                        if r:
                            return [r.left, r.top, r.right, r.bottom]
            except Exception:
                pass
            try:
                stack.extend(c.GetChildren())
            except Exception:
                pass
    except Exception:
        pass
    return None


def list_tabs(hwnd: int, warmup: bool = True) -> List[dict]:
    """列出标签页。取窗口顶部区域的 TabItem，过滤掉页面内部的 tab 控件。"""
    items = uia.scan_window(hwnd)
    if warmup and not items:
        time.sleep(0.6)
        items = uia.scan_window(hwnd)

    win_rect = uia.window_rect(hwnd) or [0, 0, 0, 0]
    win_top = win_rect[1]
    # 标签栏一定在窗口最上方的一小条里：用「高度不超过窗口 9%，且位于顶部」筛
    win_h = max(1, win_rect[3] - win_rect[1])
    cutoff = win_top + max(60, int(win_h * 0.09))

    tabs = []
    for it in items:
        if it.get("type") != "TabItemControl":
            continue
        r = it.get("rect") or [0, 0, 0, 0]
        cy = (r[1] + r[3]) // 2
        if cy <= cutoff:
            tabs.append(it)
    tabs.sort(key=lambda x: x["rect"][0])

    sel_rect = selected_tab_rect(hwnd)
    for i, t in enumerate(tabs, 1):
        t["index"] = i
        t["selected"] = bool(sel_rect and t["rect"] == sel_rect)
        nm = (t.get("name") or "").strip()
        for suffix in ("关闭标签页", "关闭", "Close tab", "close"):
            if nm.endswith(suffix):
                nm = nm[: -len(suffix)].strip()
        t["tab_title"] = nm
    # 兜底：UIA 取不到选中态时，用窗口标题做一次粗略匹配
    if sel_rect is None and tabs:
        wtitle = window_title(hwnd)
        for t in tabs:
            tt = (t.get("tab_title") or "").strip()
            if tt and tt in wtitle:
                t["selected"] = True
                break
    return tabs


def active_tab(hwnd: int) -> Optional[dict]:
    """当前标签页：优先用 UIA 选中态，取不到则退化为第一个。"""
    tabs = list_tabs(hwnd)
    if not tabs:
        return None
    for t in tabs:
        if t.get("selected"):
            return t
    return tabs[0]


def window_title(hwnd: int) -> str:
    n = w.user32.GetWindowTextLengthW(int(hwnd))
    buf = ctypes.create_unicode_buffer(n + 1)
    w.user32.GetWindowTextW(int(hwnd), buf, n + 1)
    return buf.value


def _focus(hwnd: int, settle: float = 0.35) -> bool:
    ok = uia.activate(hwnd)
    time.sleep(settle)
    return ok


def open_url(url: str, hwnd: int, new_tab: bool = False,
             settle: float = 0.6) -> dict:
    """在浏览器里打开 URL。

    new_tab=False：Ctrl+L 聚焦地址栏（会全选旧内容，直接输入即替换）
    new_tab=True ：Ctrl+T 新建标签，地址栏自动聚焦
    """
    if not _focus(hwnd):
        return {"ok": False, "error": "无法激活浏览器窗口（可能被系统阻止）"}
    if new_tab:
        keyboard.hotkey("ctrl", "t")
        time.sleep(0.45)
    else:
        keyboard.hotkey("ctrl", "l")
        time.sleep(0.25)
    keyboard.type_text(url, method="unicode")
    time.sleep(0.12)
    keyboard.press("enter")
    time.sleep(settle)
    return {"ok": True, "url": url, "new_tab": bool(new_tab),
            "hwnd": hwnd, "title_after": window_title(hwnd)}


def navigate(hwnd: int, action: str, settle: float = 0.7) -> dict:
    """导航动作：back / forward / refresh / stop / home / top / bottom。"""
    action = (action or "").lower()
    if not _focus(hwnd):
        return {"ok": False, "error": "无法激活浏览器窗口"}
    table = {
        "back": ("alt", "left"),
        "forward": ("alt", "right"),
        "refresh": ("f5",),
        "reload": ("f5",),
        "hard-refresh": ("ctrl", "f5"),
        "stop": ("esc",),
        "home": ("alt", "home"),
        "top": ("ctrl", "home"),
        "bottom": ("ctrl", "end"),
    }
    keys = table.get(action)
    if not keys:
        return {"ok": False, "error": f"未知动作 {action!r}，可选: {sorted(table)}"}
    keyboard.hotkey(*keys) if len(keys) > 1 else keyboard.press(keys[0])
    time.sleep(settle)
    return {"ok": True, "action": action, "hwnd": hwnd,
            "title_after": window_title(hwnd)}


def close_tab(hwnd: int, settle: float = 0.45) -> dict:
    if not _focus(hwnd):
        return {"ok": False, "error": "无法激活浏览器窗口"}
    keyboard.hotkey("ctrl", "w")
    time.sleep(settle)
    return {"ok": True, "hwnd": hwnd, "title_after": window_title(hwnd)}


def switch_tab(hwnd: int, index: int = 0, title: Optional[str] = None,
               settle: float = 0.5) -> dict:
    """切换标签：index 1-8 用 Ctrl+N；index 9 或 title 用 UIA 点击。"""
    if title:
        tabs = list_tabs(hwnd)
        hit = None
        for t in tabs:
            if title.lower() in (t.get("tab_title") or "").lower():
                hit = t
                break
        if not hit:
            return {"ok": False, "error": f"找不到标题含 {title!r} 的标签页",
                    "tabs": [t.get("tab_title") for t in tabs]}
        if not _focus(hwnd):
            return {"ok": False, "error": "无法激活浏览器窗口"}
        cx, cy = hit["center"]
        from . import mouse as _m
        _m.click(cx, cy)
        time.sleep(settle)
        return {"ok": True, "switched_to": hit.get("tab_title"), "hwnd": hwnd}

    if not _focus(hwnd):
        return {"ok": False, "error": "无法激活浏览器窗口"}
    idx = int(index)
    if 1 <= idx <= 8:
        keyboard.hotkey("ctrl", str(idx))
    elif idx == 9:
        keyboard.hotkey("ctrl", "9")
    else:
        return {"ok": False, "error": "index 需在 1-9 之间，或用 title 指定"}
    time.sleep(settle)
    return {"ok": True, "index": idx, "hwnd": hwnd,
            "title_after": window_title(hwnd)}


def find_in_page(hwnd: int, text: str, settle: float = 0.4) -> dict:
    """Ctrl+F 页内查找。再次调用会沿用到同一个查找框。"""
    if not _focus(hwnd):
        return {"ok": False, "error": "无法激活浏览器窗口"}
    keyboard.hotkey("ctrl", "f")
    time.sleep(0.3)
    keyboard.type_text(text, method="unicode")
    time.sleep(settle)
    return {"ok": True, "query": text, "hwnd": hwnd}


def close_find_bar(hwnd: int) -> dict:
    keyboard.press("esc")
    return {"ok": True, "action": "close_find_bar"}


def scan_page(hwnd: Optional[int] = None, kind: str = "edge",
              include_text: bool = True) -> List[dict]:
    """扫描当前页面里的可交互元素（链接、按钮、输入框）。"""
    h = hwnd or find_browser(kind)
    if not h:
        return []
    win_rect = uia.window_rect(h) or [0, 0, 0, 0]
    items = uia.scan_window(h, include_text=include_text)
    out = []
    for it in items:
        r = it.get("rect") or [0, 0, 0, 0]
        # 页面区域：去掉顶部标签栏/地址栏与底部状态栏
        if r[1] > win_rect[1] + 90 and r[3] < win_rect[3] - 8:
            it = dict(it)
            it["window_hwnd"] = h
            out.append(it)
    return out


def open_browser(kind: str = "edge", url: Optional[str] = None) -> dict:
    """启动浏览器（若未运行）。"""
    exe_map = {
        "edge": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"],
        "chrome": [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
    }
    exes = exe_map.get(kind.lower())
    if not exes:
        return {"ok": False, "error": f"不支持的浏览器 {kind!r}"}
    exe = next((p for p in exes if os.path.exists(p)), None)
    if not exe:
        return {"ok": False, "error": f"未找到 {kind} 的可执行文件"}
    import subprocess
    args = [exe] + ([url] if url else [])
    subprocess.Popen(args)
    return {"ok": True, "launched": exe, "url": url}


def status(kind: str = "edge") -> dict:
    wins = find_browsers(kind)
    if not wins:
        return {"running": False, "browser": kind, "windows": []}
    hwnd = find_browser(kind)
    tabs = list_tabs(hwnd) if hwnd else []
    return {
        "running": True,
        "browser": kind,
        "hwnd": hwnd,
        "window_title": window_title(hwnd) if hwnd else "",
        "windows": [{"hwnd": x["hwnd"], "title": x["title"]} for x in wins],
        "tab_count": len(tabs),
        "tabs": [{"index": t["index"], "title": t.get("tab_title"),
                  "selected": bool(t.get("selected")),
                  "center": t["center"]} for t in tabs],
    }
