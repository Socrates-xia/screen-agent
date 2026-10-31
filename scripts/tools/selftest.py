#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""screen-agent 端到端自检。

创建一个完全可控的 Win32 测试窗口（不碰用户任何真实数据），
验证：截图像素 / UIA 坐标 / Win32 坐标三者一致，以及左键、右键点击真实生效。

用法：
    python -B tools/selftest.py            # 完整自检
    python -B tools/selftest.py --quick    # 只做环境与静态检查
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core import (DPI_MODE, annotate, capture, dpi, geom, human,  # noqa: E402
                  keyboard, mouse, ocr, session, uia, winapi as w)

WND_CLASS = "ScreenAgentSelfTest"
WND_COVER = "ScreenAgentSelfTestCover"
WND_TITLE = "ScreenAgent SelfTest Window"
COVER_TITLE = "ScreenAgent Cover Window"
BTNS = [("btn-alpha", 2001), ("btn-beta", 2002), ("btn-gamma", 2003)]

_results = []


def check(name: str, ok: bool, detail: str = ""):
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)
    return ok


# ------------------------------------------------------------------ host 角色
def host_main():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.SetProcessDPIAware()
    for fn, rt in [("RegisterClassW", ctypes.c_ushort), ("CreateWindowExW", ctypes.c_void_p),
                   ("DefWindowProcW", ctypes.c_longlong), ("SetTimer", ctypes.c_void_p),
                   ("LoadCursorW", ctypes.c_void_p), ("GetSysColorBrush", ctypes.c_void_p)]:
        getattr(user32, fn).restype = rt
    user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                      ctypes.c_ulonglong, ctypes.c_longlong]
    user32.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                       ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetSysColorBrush.argtypes = [ctypes.c_int]
    user32.SetTimer.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
    user32.SetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_ulonglong, ctypes.c_longlong)
    WM_DESTROY, WM_TIMER, WM_COMMAND = 0x0002, 0x0113, 0x0111
    WM_MOUSEMOVE, WM_LBUTTONDOWN = 0x0200, 0x0201
    WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
    EDIT_ID = 3001

    # 拖拽轨迹统计：验证器用「收到了多少条移动事件」「累计位移多少」
    # 来判断拖拽是否真的被应用识别（只发一次瞬移的话 events 会接近 0）
    _drag = {"active": False, "events": 0, "dist": 0, "lx": 0, "ly": 0}

    def _lo(v):
        return ctypes.c_short(v & 0xFFFF).value

    def _hi(v):
        return ctypes.c_short((v >> 16) & 0xFFFF).value

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                    ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]

    @WNDPROC
    def wndproc(hwnd, msg, wp, lp):
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_TIMER:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_COMMAND:
            cid = wp & 0xFFFF
            names = {v: k for k, v in BTNS}
            if cid in names:
                user32.SetWindowTextW(hwnd, f"EVT:LEFT:{names[cid]}")
            return 0
        if msg == WM_RBUTTONUP:
            user32.SetWindowTextW(hwnd, "EVT:RIGHT")
            return 0
        if msg == WM_LBUTTONDOWN:
            _drag.update(active=True, events=0, dist=0, lx=_lo(lp), ly=_hi(lp))
            return user32.DefWindowProcW(hwnd, msg, wp, lp)
        if msg == WM_MOUSEMOVE and _drag["active"]:
            x, y = _lo(lp), _hi(lp)
            _drag["dist"] += abs(x - _drag["lx"]) + abs(y - _drag["ly"])
            _drag["events"] += 1
            _drag["lx"], _drag["ly"] = x, y
            return user32.DefWindowProcW(hwnd, msg, wp, lp)
        if msg == WM_LBUTTONUP:
            if _drag["active"]:
                _drag["active"] = False
                user32.SetWindowTextW(
                    hwnd, f"DRAG:events={_drag['events']},dist={_drag['dist']}")
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
    wc.hInstance = hinst
    wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
    wc.hbrBackground = user32.GetSysColorBrush(5)
    wc.lpszClassName = WND_CLASS
    user32.RegisterClassW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(0, WND_CLASS, WND_TITLE,
                                  0x00CF0000 | 0x10000000, 120, 120, 520, 360,
                                  None, None, hinst, None)
    for i, (label, cid) in enumerate(BTNS):
        user32.CreateWindowExW(0, "Button", label, 0x40000000 | 0x10000000 | 0x00010000,
                               40, 50 + i * 80, 190, 54, hwnd, ctypes.c_void_p(cid), hinst, None)
    # 文本输入框：验证键盘输入真的落到了控件里（WS_CHILD|WS_VISIBLE|WS_BORDER|WS_TABSTOP）
    user32.CreateWindowExW(0x00000200, "Edit", "",
                           0x40000000 | 0x10000000 | 0x00800000 | 0x00010000,
                           260, 50, 230, 44, hwnd, ctypes.c_void_p(EDIT_ID), hinst, None)

    # 第二个窗口：用来遮挡 btn-beta / btn-gamma，验证遮挡检测
    wc2 = WNDCLASSW()
    wc2.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
    wc2.hInstance = hinst
    wc2.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
    wc2.hbrBackground = user32.GetSysColorBrush(5)
    wc2.lpszClassName = WND_COVER
    user32.RegisterClassW(ctypes.byref(wc2))
    cover = user32.CreateWindowExW(0, WND_COVER, COVER_TITLE,
                                   0x80000000 | 0x10000000, 1400, 900, 300, 220,
                                   None, None, hinst, None)

    print(f"[host] hwnd={hwnd} cover={cover}", flush=True)
    user32.SetForegroundWindow(hwnd)
    user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0002 | 0x0001)
    user32.SetWindowPos(cover, ctypes.c_void_p(-1), 1400, 900, 0, 0, 0x0001 | 0x0040)
    user32.SetTimer(hwnd, 1, 70000, None)
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    print("[host] exit", flush=True)


# ------------------------------------------------------------------ 检查项
def run_checks(quick: bool = False) -> int:
    print("=" * 74)
    print("screen-agent 自检")
    print("=" * 74)

    print("\n[1] 环境与坐标系")
    check("DPI 感知已启用", DPI_MODE in ("per-monitor-v2", "per-monitor"),
          f"mode={DPI_MODE}")
    vd = dpi.virtual_desktop()
    check("虚拟桌面尺寸可用", vd["width"] > 0 and vd["height"] > 0, str(vd))
    mons = dpi.monitors()
    check("显示器枚举", len(mons) >= 1,
          f"{len(mons)} 台: " + ", ".join(f"{m['rect']}@{m['scale']}%" for m in mons))

    print("\n[2] 截图")
    try:
        path, rect = capture.save(prefix="selftest")
        from PIL import Image
        img = Image.open(path)
        check("截屏并落盘", os.path.exists(path) and img.width > 0,
              f"{img.width}x{img.height} → {os.path.basename(path)}")
        # 与 GDI 逐点比对
        px_mss = img.convert("RGB").getpixel((min(37, img.width - 1), min(53, img.height - 1)))
        px_gdi = capture.pixel(rect[0] + min(37, img.width - 1), rect[1] + min(53, img.height - 1))
        diff = sum(abs(a - b) for a, b in zip(px_mss, px_gdi)) if px_gdi else 999
        check("截图与 GDI 像素一致", diff == 0, f"mss={px_mss} gdi={px_gdi} Δ={diff}")
    except Exception as e:
        check("截屏", False, f"{type(e).__name__}: {e}")
        return 1

    print("\n[3] 窗口与 UIA")
    wins = uia.list_windows()
    check("枚举顶层窗口", len(wins) > 0, f"{len(wins)} 个可见窗口")
    fg = uia.foreground_window()
    check("取得前台窗口", bool(fg), f"hwnd={fg}")

    if quick:
        return _summary()

    print("\n[4] 自建窗口端到端验证（不触碰任何真实数据）")
    host = subprocess.Popen([sys.executable, "-B", os.path.abspath(__file__), "--role", "host"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    try:
        hwnd = None
        for _ in range(50):
            hwnd = uia.find_window(title=WND_TITLE)
            if hwnd:
                break
            time.sleep(0.2)
        if not check("测试窗口已创建", bool(hwnd), f"hwnd={hwnd}"):
            return _summary()
        time.sleep(1.0)

        win_rect = uia.window_rect(hwnd)
        r = wt.RECT()
        w.user32.GetWindowRect(hwnd, ctypes.byref(r))
        win32_rect = [r.left, r.top, r.right, r.bottom]
        check("UIA 窗口坐标 == Win32 坐标", win_rect == win32_rect,
              f"uia={win_rect} win32={win32_rect}")

        items = uia.scan_window(hwnd)
        for _it in items:
            _it["window_hwnd"] = hwnd          # 遮挡检测依赖此字段
            _it["window_title"] = WND_TITLE
        check("UIA 扫到子控件", len(items) >= 3,
              f"{len(items)} 个: " + ", ".join(f"{i['id'] if 'id' in i else i['name']}"
                                               for i in items[:6]))
        btns = {i["name"]: i for i in items if i["name"].startswith("btn-")}
        check("定位到全部 3 个测试按钮", len(btns) == 3, str(sorted(btns)))
        check("元素坐标通过合理性校验", all(b["plausible"] for b in btns.values()),
              "plausible=True")

        # 按钮中心点是否落在窗口范围内
        inside = all(geom.contains_point(win_rect, *b["center"]) for b in btns.values())
        check("按钮中心点位于窗口内", inside,
              ", ".join(f"{k}={v['center']}" for k, v in btns.items()))

        print("\n[5] 光标定位精度（SetCursorPos）")
        vd_ = dpi.virtual_desktop()
        probe_pts = [(vd_["left"] + 60, vd_["top"] + 60),
                     (vd_["left"] + vd_["width"] // 2, vd_["top"] + vd_["height"] // 2),
                     (vd_["left"] + vd_["width"] - 60, vd_["top"] + vd_["height"] // 2)]
        allok, details = True, []
        saved = mouse.cursor_pos()
        for (tx, ty) in probe_pts:
            ax, ay = mouse.move(tx, ty)
            ok = abs(ax - tx) <= 1 and abs(ay - ty) <= 1
            allok &= ok
            details.append(f"({tx},{ty})→({ax},{ay})")
        check("多点定位零偏差", allok, "; ".join(details))

        print("\n[6] 真实点击验证")
        def win_title():
            buf = ctypes.create_unicode_buffer(160)
            w.user32.GetWindowTextW(hwnd, buf, 160)
            return buf.value

        for label in ("btn-alpha", "btn-beta"):
            b = btns[label]
            mouse.click(b["center"][0], b["center"][1])
            time.sleep(0.45)
            t = win_title()
            check(f"左键点击 {label} 触发事件", t == f"EVT:LEFT:{label}", f"标题={t!r}")

        mouse.click(win_rect[0] + 430, win_rect[1] + 300, button="right")
        time.sleep(0.45)
        t = win_title()
        check("右键点击触发事件", t == "EVT:RIGHT", f"标题={t!r}")

        mouse.restore_cursor_position(saved)

        print("\n[6b] 遮挡检测（安全关键项）")
        cover = uia.find_window(title=COVER_TITLE)
        if check("遮挡窗口存在", bool(cover), f"hwnd={cover}"):
            # 把遮挡窗口移到 btn-beta 上方
            b = btns["btn-beta"]
            w.user32.SetWindowPos(cover, ctypes.c_void_p(-1),
                                  b["rect"][0] - 40, b["rect"][1] - 40, 340, 220,
                                  0x0040)
            time.sleep(0.6)
            v_free = uia.verify_point(btns["btn-alpha"]["center"][0],
                                      btns["btn-alpha"]["center"][1],
                                      btns["btn-alpha"])
            check("未被遮挡的元素 → match", v_free == "match", f"verdict={v_free}")

            v_occ = uia.verify_point(b["center"][0], b["center"][1], b)
            check("被遮挡的元素 → occluded", v_occ == "occluded", f"verdict={v_occ}")

            # 端到端：resolve_target 应拒绝点击这个被遮挡的元素
            sess2 = session.save(items, {"mode": "window", "hwnd": hwnd, "title": WND_TITLE})
            e_beta = None
            for e in sess2["elements"]:
                if e.get("name") == "btn-beta":
                    e_beta = e
                    break
            if e_beta:
                rt = session.resolve_target(e_beta, sess2, verify=True)
                check("resolve_target 拒绝点击被遮挡元素", not rt["ok"],
                      f"ok={rt['ok']} reason={rt['reason'][:56]}")
                mouse.restore_cursor_position(saved)
            # 移走遮挡窗口
            w.user32.SetWindowPos(cover, ctypes.c_void_p(-1), 1400, 900, 0, 0, 0x0001 | 0x0040)
            time.sleep(0.3)

        print("\n[7] 会话与标注")
        sess = session.save(items, {"mode": "window", "hwnd": hwnd, "title": WND_TITLE},
                            screenshot=path)
        check("会话落盘", session.load() is not None,
              f"id={sess['session_id']} count={sess['count']}")
        e1 = session.get("E1", sess)
        check("按编号取元素", e1 is not None, f"E1={e1.get('name') if e1 else None}")
        if e1:
            rt = session.resolve_target(e1, sess, verify=True)
            check("陈旧坐标校验可执行", isinstance(rt, dict) and "method" in rt,
                  f"method={rt.get('method')} moved={rt.get('moved')}")

        from PIL import Image
        img = Image.open(path)
        marked = annotate.annotate(img, capture.region_rect(None), items)
        ap = os.path.join(capture.runtime_dir(), "selftest_annotated.png")
        marked.save(ap)
        check("标注图生成", os.path.exists(ap), os.path.basename(ap))

        print("\n[8] OCR 引擎")
        st = ocr.status()
        check("OCR 兜底引擎", st["available"],
              st["engine"] if st["available"] else "未安装（可选，UIA 已足够覆盖常规场景）")

        # ---------------------------------------------------------- 键盘
        print("\n[9] 键盘输入（真实落到控件里才算通过）")
        sm = w.user32.SendMessageW
        sm.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        sm.restype = ctypes.c_void_p
        WM_GETTEXT = 0x000D

        def edit_value():
            eh = w.user32.FindWindowExW(hwnd, None, "Edit", None)
            if not eh:
                return None
            buf = ctypes.create_unicode_buffer(1024)
            sm(eh, WM_GETTEXT, 1024, ctypes.byref(buf))
            return buf.value

        edit_hwnd = w.user32.FindWindowExW(hwnd, None, "Edit", None)
        if check("找到测试输入框", bool(edit_hwnd), f"hwnd={edit_hwnd}"):
            er = wt.RECT()
            w.user32.GetWindowRect(edit_hwnd, ctypes.byref(er))
            uia.activate(hwnd)
            time.sleep(0.3)
            mouse.click((er.left + er.right) // 2, (er.top + er.bottom) // 2)
            time.sleep(0.25)

            payload = "Hello 中文输入 123"
            keyboard.type_text(payload)
            time.sleep(0.35)
            got = edit_value()
            check("Unicode 直发通道", got == payload, f"读回={got!r}")

            keyboard.clear_field()
            time.sleep(0.2)
            check("清空输入框（Ctrl+A + Del）", edit_value() == "",
                  f"读回={edit_value()!r}")

            keyboard.type_text("剪贴板通道", method="clipboard")
            time.sleep(0.4)
            got2 = edit_value()
            check("剪贴板通道", got2 == "剪贴板通道", f"读回={got2!r}")

            keyboard.press("a")
            keyboard.press("b")
            keyboard.hotkey("ctrl", "a")
            time.sleep(0.2)
            check("单键与组合键不报错", True, "press(a) press(b) hotkey(ctrl+a)")

        # ---------------------------------------------------------- 拖拽
        print("\n[10] 拖拽 / 滑动（需应用侧真的收到连续移动）")
        wr2 = uia.window_rect(hwnd)
        if wr2:
            uia.activate(hwnd)
            time.sleep(0.3)
            # ★ 必须落在窗口**空白区**：子控件（按钮/输入框）会捕获鼠标消息，
            #   从控件上起拖父窗口收不到 WM_LBUTTONDOWN —— 这是真实存在的坑，
            #   所以这里的 y 要低于所有子控件的底部。
            sx, sy = wr2[0] + 60, wr2[3] - 20
            ex, ey = wr2[0] + 430, wr2[3] - 20
            w.user32.SetWindowTextW(hwnd, "drag-armed")
            time.sleep(0.2)
            mouse.drag(sx, sy, ex, ey, duration=0.4)
            time.sleep(0.4)
            t = win_title()
            events = dist = -1
            if t.startswith("DRAG:"):
                try:
                    kv = dict(p.split("=") for p in t[5:].split(","))
                    events, dist = int(kv["events"]), int(kv["dist"])
                except Exception:
                    pass
            check("拖拽被识别为连续移动", events >= 5,
                  f"应用收到 mousemove {events} 次（1 次=瞬移，会被当成点击）")
            check("拖拽位移被接收", dist > 100, f"累计位移={dist}px")

        # ---------------------------------------------------------- 人工介入检测
        print("\n[11] 人工介入检测（验证码 / 登录）")
        w.user32.SetWindowTextW(hwnd, "请输入验证码 - 安全验证")
        time.sleep(0.3)
        r1 = human.check(mode="fast")
        kws = [e["keyword"] for e in r1["evidence"][:4]]
        check("能识别「验证码」类界面", r1["human_required"],
              f"level={r1['level']} score={r1['score']} 命中={kws}")

        w.user32.SetWindowTextW(hwnd, "请拖动滑块完成验证")
        time.sleep(0.3)
        r2 = human.check(mode="fast")
        check("能识别「滑块验证」类界面", r2["human_required"],
              f"level={r2['level']} 命中={[e['keyword'] for e in r2['evidence'][:3]]}")

        w.user32.SetWindowTextW(hwnd, WND_TITLE)
        time.sleep(0.3)
        r3 = human.check(mode="fast", hwnd=hwnd)
        check("正常窗口不误判为 high", r3["level"] != "high",
              f"level={r3['level']} score={r3['score']}")

        print("\n[12] 浏览器模块")
        try:
            from core import browser as _br
        except Exception as e:
            check("浏览器模块可导入", False, f"{type(e).__name__}: {e}")
        else:
            check("浏览器模块可导入", True, "core.browser")
            st = _br.status("edge")
            check("能探测浏览器运行状态", isinstance(st, dict) and "running" in st,
                  f"edge running={st['running']}")

    finally:
        try:
            host.terminate()
            host.wait(timeout=5)
        except Exception:
            pass

    return _summary()


def _summary() -> int:
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 74)
    print(f"结果：{passed}/{total} 通过")
    if failed:
        print("失败项：" + ", ".join(failed))
    print("=" * 74)
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="screen-agent 自检")
    ap.add_argument("--role", default="probe", choices=["probe", "host"])
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args(argv)
    if args.role == "host":
        host_main()
        return 0
    return run_checks(quick=args.quick)


if __name__ == "__main__":
    sys.exit(main())
