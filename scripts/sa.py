#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""screen-agent —— 屏幕观察与鼠标操作技能的统一入口。

用法见 `python sa.py --help`，或参考同目录上级的 SKILL.md。

设计约束：
  · 所有坐标都是「物理像素」，与 UIA / 截图 / Win32 三者严格同一坐标系。
  · 输出默认是人类可读文本，加 --json 则只输出机器可解析的 JSON。
  · 点击类命令默认做「陈旧坐标防护」，可被 --no-verify 关闭。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from core import (DPI_MODE, annotate, browser, capture, dpi, geom,  # noqa: E402
                  human, keyboard, mouse, ocr, safety, session, uia,
                  winapi as w)

# 主显示器右下角的「显示桌面」热区，误点会最小化全部窗口
_CORNER_GUARD = 24


def out(data, as_json: bool = False, text: str = ""):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        if text:
            print(text)
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))


def _corner_guard(x: int, y: int, allow: bool) -> str:
    """检查是否落在「显示桌面」热区。返回警告文本，空串表示安全。

    必须用主显示器的**绝对**矩形（dpi.primary_rect()）。
    旧实现把 SM_CXSCREEN/CYSCREEN 的 width/height 当绝对坐标用，
    在「副屏位于主屏左侧」等非零原点布局下会守错矩形。
    """
    if allow:
        return ""
    vd = dpi.virtual_desktop()
    pr = dpi.primary_rect()
    # 主显示器右下角（Windows 的显示桌面按钮）
    if (pr["right"] - _CORNER_GUARD <= x < pr["right"]
            and pr["bottom"] - _CORNER_GUARD <= y < pr["bottom"]):
        return (f"目标 ({x},{y}) 位于主显示器右下角「显示桌面」热区，"
                f"点击会最小化所有窗口。若确实需要，请加 --allow-corners")
    # 屏幕最底部一条（任务栏区域容易点到开始菜单）
    if y >= vd["top"] + vd["height"] - 2:
        return f"目标 ({x},{y}) 贴在屏幕最底边，可能命中任务栏。若确实需要请加 --allow-corners"
    return ""


def _window_title_at(x: int, y: int) -> str:
    """取该点所在窗口的标题（拿不到就返回空串）。"""
    try:
        hwnd = uia.window_of_point(x, y)
        if not hwnd:
            return ""
        for win in uia.list_windows(visible_only=True, titled_only=True):
            if win.get("hwnd") == hwnd:
                return win.get("title") or ""
    except Exception:
        pass
    return ""


def _locked_guard(args, action: str) -> Optional[str]:
    """锁屏时拒绝一切会动鼠标/键盘的命令。返回错误文本，安全则 None。"""
    msg = safety.lock_warning()
    if not msg:
        return None
    safety.audit(action, blocked=True, reason="locked")
    if getattr(args, "json", False):
        out({"error": msg, "blocked": True, "reason": "workstation_locked"}, True)
    else:
        print(f"✗ 已拦截：{msg}")
    return msg


def _danger_gate(args, action: str, *texts) -> Optional[str]:
    """不可逆操作闸门：命中危险词且未加 --yes 则拒绝执行。

    返回错误文本（表示已拦截），未拦截返回 None。
    """
    if getattr(args, "yes", False) or getattr(args, "dry_run", False):
        return None          # 演练不算执行；用户已显式确认
    hit = safety.check_dangerous(*texts)
    if not hit:
        return None
    msg = (hit["reason"] + "。\n  " + hit["advice"] +
           f"\n  （已记入审计日志 {safety.audit_path()}）")
    safety.audit(action, blocked=True, reason=hit["reason"],
                 hits=hit["hits"], element_name=next((t for t in texts if t), None))
    if getattr(args, "json", False):
        out({"error": msg, "blocked": True, "level": hit["level"],
             "hits": hit["hits"], "advice": hit["advice"]}, True)
    else:
        print(f"✗ 已拦截（不可逆操作）：{msg}")
    return msg


# ------------------------------------------------------------------ 命令
def cmd_info(args):
    info = {
        "dpi_mode": DPI_MODE,
        "screen": dpi.describe(),
        "python": sys.version.split()[0],
        "ocr": ocr.status(),
        "runtime_dir": capture.runtime_dir(),
        "session_file": os.path.join(capture.runtime_dir(), session.SESSION_FILE),
        "cursor": list(mouse.cursor_pos()),
        "admin": bool(w.user32.IsUserAnAdmin()) if hasattr(w.user32, "IsUserAnAdmin") else None,
    }
    lines = [
        f"DPI 感知模式   : {DPI_MODE}",
        f"虚拟桌面       : {info['screen']['virtual_desktop']['width']}x"
        f"{info['screen']['virtual_desktop']['height']} @"
        f"({info['screen']['virtual_desktop']['left']},{info['screen']['virtual_desktop']['top']})",
        f"主显示器       : {info['screen']['primary']['width']}x{info['screen']['primary']['height']}",
        f"显示器数量     : {info['screen']['monitor_count']}",
    ]
    for m in info["screen"]["monitors"]:
        lines.append(f"  · {m['rect']} primary={m['primary']} dpi={m['dpi']} ({m['scale']}%)")
    lines += [
        f"光标当前位置   : {info['cursor']}",
        f"OCR 引擎       : {info['ocr']['engine']}"
        + ("" if info["ocr"]["available"] else "  (UIA 不可用时将无兜底)"),
        f"运行目录       : {info['runtime_dir']}",
    ]
    out(info, args.json, "\n".join(lines))


def cmd_windows(args):
    wins = uia.list_windows(visible_only=not args.all, titled_only=not args.untitled,
                            include_minimized=not args.no_minimized)
    vd = dpi.virtual_desktop()
    for x in wins:
        x["on_screen"] = dpi.rect_in_virtual_desktop(*x["rect"])
    if args.json:
        out(wins, True)
        return
    lines = [f"共 {len(wins)} 个窗口（虚拟桌面 {vd['width']}x{vd['height']}）", "-" * 92]
    lines.append(f"{'HWND':>10} {'PID':>7}  {'标题':<40} {'类名':<26} 位置/大小")
    for x in wins:
        flag = "★" if x["foreground"] else " "
        mn = "[最小化]" if x["minimized"] else ""
        lines.append(f"{x['hwnd']:>10} {x['pid']:>7}{flag} {x['title'][:38]:<40} "
                     f"{x['class_name'][:24]:<26} {x['rect']} {mn}")
    out(wins, False, "\n".join(lines))


def cmd_shot(args):
    region = None
    if args.region:
        parts = [int(v) for v in args.region.replace(" ", "").split(",")]
        if len(parts) == 4:
            region = {"left": parts[0], "top": parts[1], "width": parts[2], "height": parts[3]}
    if args.window:
        h = uia.find_window(title=args.window, hwnd=args.hwnd)
        if not h:
            out({"error": f"找不到窗口: {args.window}"}, args.json, f"找不到窗口: {args.window}")
            return 1
        region = capture.window_region(h)
        if region is None:
            out({"error": "窗口不可见或已最小化"}, args.json, "窗口不可见或已最小化")
            return 1

    path, rect = capture.save(region=region, path=args.out, prefix="shot")

    if args.grid or args.cursor:
        from PIL import Image
        img = Image.open(path)
        if args.grid:
            img = annotate.draw_grid(img, rect, step=args.grid_step)
        if args.cursor:
            img = annotate.draw_cursor(img, rect, mouse.cursor_pos())
        img.save(path)

    res = {"path": path, "rect": list(rect),
           "size": [rect[2] - rect[0], rect[3] - rect[1]]}
    out(res, args.json, f"截图已保存: {path}\n对应屏幕矩形: {list(rect)}")


def _do_scan(args):
    """执行扫描并落盘会话。返回 (sess, elements)。"""
    scope = {"mode": "foreground"}
    windows = []
    if args.all:
        scope = {"mode": "all-windows"}
        elems = uia.scan_all(include_text=args.include_text, max_windows=args.max_windows)
        windows = uia.list_windows(visible_only=True, titled_only=True, include_minimized=False)
    else:
        hwnd = None
        if args.hwnd:
            hwnd = int(args.hwnd)
        elif args.window:
            hwnd = uia.find_window(title=args.window, exact=args.exact_title)
            if not hwnd:
                return None, [], {"error": f"找不到窗口: {args.window}"}
        else:
            hwnd = uia.foreground_window()
        if not hwnd:
            return None, [], {"error": "没有前台窗口"}
        if args.activate:
            uia.activate(hwnd)
        title = ""
        for x in uia.list_windows():
            if x["hwnd"] == hwnd:
                title = x["title"]
                break
        scope = {"mode": "window", "hwnd": hwnd, "title": title}
        win_rect = uia.window_rect(hwnd)
        elems = uia.scan_window(hwnd, include_text=args.include_text)
        for e in elems:
            e["window_hwnd"] = hwnd
            e["window_title"] = title
            e["window_rect"] = win_rect

    if args.role:
        want = args.role.lower()
        elems = [e for e in elems if e.get("role", "").lower() == want
                 or e.get("type", "").lower() == want]
    if args.clickable_only:
        elems = [e for e in elems if e.get("clickable")]
    if args.limit:
        elems = elems[:args.limit]

    # 截图 + 标注
    shot_path = annotated_path = None
    if not args.no_shot:
        region = None
        if scope["mode"] == "window" and scope.get("hwnd"):
            region = capture.window_region(scope["hwnd"], pad=8)
        from PIL import Image
        path, rect = capture.save(region=region, prefix="scan")
        shot_path = path
        if not args.no_annotate:
            img = Image.open(path)
            marked = annotate.annotate(img, rect, elems, only_clickable=False)
            annotated_path = path.replace(".png", "_annotated.png")
            marked.save(annotated_path)

    sess = session.save(elems, scope, windows=windows,
                        screenshot=shot_path, annotated=annotated_path,
                        notes={"filter_role": args.role, "include_text": args.include_text})
    return sess, elems, None


def cmd_scan(args):
    sess, elems, err = _do_scan(args)
    if err:
        out(err, args.json, err["error"])
        return 1
    if args.json:
        out({"session_id": sess["session_id"], "scope": sess["scope"],
             "count": sess["count"], "annotated": sess["annotated"],
             "screenshot": sess["screenshot"], "elements": sess["elements"]}, True)
        return
    print(session.describe(sess))
    if sess.get("annotated"):
        print(f"\n标注图（建议直接查看）: {sess['annotated']}")


def cmd_show(args):
    sess = session.load()
    if not sess:
        out({"error": "尚无扫描会话，请先运行 scan"}, args.json, "尚无扫描会话，请先运行 scan")
        return 1
    a = session.age(sess)
    if args.json:
        out({"age_seconds": round(a, 1), "expired": a > session.DEFAULT_MAX_AGE, **sess}, True)
        return
    print(session.describe(sess, limit=args.limit, clickable_only=args.clickable))
    print(f"\n会话年龄: {a:.1f}s" + ("  ⚠ 已过期，建议重新扫描" if a > session.DEFAULT_MAX_AGE else ""))


def cmd_find(args):
    sess = session.load()
    if not sess or session.age(sess) > session.DEFAULT_MAX_AGE:
        print("(会话缺失或过期，自动重新扫描前台窗口)")
        ns = argparse.Namespace(**vars(args))
        ns.all = False; ns.window = None; ns.hwnd = None; ns.activate = False
        ns.role = ""; ns.limit = 0; ns.clickable_only = False
        ns.include_text = True; ns.max_windows = 8
        ns.no_shot = False; ns.no_annotate = False; ns.exact_title = False
        sess, _elems, err = _do_scan(ns)
        if err:
            out(err, args.json, err["error"])
            return 1
    hits = session.find(sess, text=args.text, role=args.role, exact=args.exact,
                        clickable_only=not args.include_disabled)
    # UIA 没命中 → 视觉兜底
    vision = []
    if not hits and args.vision:
        if ocr.available():
            vision = ocr.find_text(args.text, exact=args.exact)
        if not vision:
            print(f"UIA 与 OCR 均未找到 {args.text!r}")
    res = {"query": args.text, "uia_hits": hits, "ocr_hits": vision}
    if args.json:
        out(res, True)
        return
    print(f"查询 {args.text!r} → UIA {len(hits)} 个，OCR {len(vision)} 个")
    for h in hits:
        print(f"  {h['id']:>5} {h['role']:<10} {(h.get('name') or '')[:40]:<42} @{h['center']}"
              + ("" if h.get("plausible", True) else "  [!坐标不可信]"))
    for v in vision:
        print(f"  OCR   {v['text'][:40]:<42} @{v['center']}")


def _resolve_click_target(args):
    """把 --id / --text / --xy 三种寻址方式统一解析成 {x, y, element, note}。"""
    if args.xy:
        parts = [int(v) for v in args.xy.replace(" ", "").split(",")]
        if len(parts) != 2:
            return None, "坐标格式应为 x,y"
        return {"x": parts[0], "y": parts[1], "element": None,
                "note": "直接坐标，未做元素校验"}, ""

    sess = session.load()
    if not sess:
        return None, "尚无扫描会话。请先运行 scan，或用 --xy 直接给坐标"

    a = session.age(sess)
    if a > session.DEFAULT_MAX_AGE and not args.force:
        return None, (f"会话已过期（{a:.0f}s > {session.DEFAULT_MAX_AGE:.0f}s）。"
                      f"请重新 scan，或加 --force 强制使用")

    if args.id:
        e = session.get(args.id, sess)
        if not e:
            return None, f"会话中不存在元素 {args.id}"
    else:
        hits = session.find(sess, text=args.text, exact=args.exact,
                            clickable_only=True)
        if not hits:
            return None, f"会话中找不到可点击元素：{args.text!r}（可先 find 或重新 scan）"
        e = hits[0]
        if len(hits) > 1:
            pass  # 取面积最小者，已在 find 中排序

    if not args.no_verify:
        r = session.resolve_target(e, sess, verify=True)
        if not r["ok"]:
            return None, f"元素 {e.get('id')} 校验未通过：{r['reason']}"
        return {"x": r["x"], "y": r["y"], "element": e,
                "note": f"校验方式={r['method']}" + (f"；{r['reason']}" if r["reason"] else ""),
                "moved": r.get("moved", 0)}, ""

    return {"x": e["center"][0], "y": e["center"][1], "element": e,
            "note": "已跳过元素校验（--no-verify）"}, ""


def cmd_click(args):
    target, err = _resolve_click_target(args)
    if err:
        out({"error": err}, args.json, f"✗ {err}")
        return 1

    x, y, elem = target["x"], target["y"], target["element"]

    if _locked_guard(args, "click"):
        return 3
    # 危险操作闸门：看元素名与所在窗口标题（坐标直指时只能看窗口标题）
    win_title = _window_title_at(x, y)
    if _danger_gate(args, "click", (elem or {}).get("name"), win_title):
        return 4

    warn = _corner_guard(x, y, args.allow_corners)
    if warn and not args.dry_run:
        safety.audit("click", blocked=True, reason=warn,
                     x=x, y=y, element_name=(elem or {}).get("name"))
        out({"error": warn}, args.json, f"✗ 已拦截：{warn}")
        return 2

    before = mouse.cursor_pos()
    res = mouse.click(x, y, button=args.button, count=args.count,
                      duration=args.duration, dry_run=args.dry_run,
                      keep_cursor=args.keep_cursor)
    res.update({
        "element_id": elem.get("id") if elem else None,
        "element_name": (elem.get("name") if elem else None),
        "element_role": (elem.get("role") if elem else None),
        "note": target["note"],
        "screen_rect": (elem.get("rect") if elem else None),
    })

    safety.audit("click", x=x, y=y, actual=res.get("actual"),
                 element_id=elem.get("id") if elem else None,
                 element_name=elem.get("name") if elem else None,
                 element_role=elem.get("role") if elem else None,
                 button=args.button, count=args.count,
                 dry_run=bool(args.dry_run), note=target["note"])

    shot_after = None
    if args.shot_after and not args.dry_run:
        time.sleep(args.shot_delay)
        shot_after = capture.save(prefix="afterclick")[0]
        res["screenshot_after"] = shot_after

    if args.json:
        out(res, True)
        return
    tag = "模拟" if args.dry_run else "已执行"
    desc = (f"{elem.get('id')} {elem.get('role')}/{elem.get('name')}"
            if elem else "坐标直指")
    print(f"{tag} {args.button} 点击 x{args.count} → ({res['actual'][0]},{res['actual'][1]})")
    print(f"  目标: {desc}")
    print(f"  说明: {target['note']}")
    if res.get("offset") and any(res["offset"]):
        print(f"  注意: 光标落点与目标偏移 {res['offset']}")
    if before != tuple(res["actual"]):
        print(f"  光标: {before} → {tuple(res['actual'])}")
    if shot_after:
        print(f"  点击后截图: {shot_after}")


def cmd_move(args):
    parts = [int(v) for v in args.xy.replace(" ", "").split(",")]
    warn = _corner_guard(parts[0], parts[1], args.allow_corners)
    if warn:
        print(f"提示: {warn}")
    actual = mouse.move(parts[0], parts[1], duration=args.duration)
    res = {"target": parts, "actual": list(actual)}
    out(res, args.json, f"光标已移动 {parts} → {list(actual)}")


def cmd_scroll(args):
    xy = None
    if args.xy:
        p = [int(v) for v in args.xy.replace(" ", "").split(",")]
        xy = (p[0], p[1])
    res = mouse.scroll(args.amount, *(xy or (None, None)))
    out(res, args.json, f"滚轮 {args.amount} 于 {res['at']}")


def cmd_ocr(args):
    if not ocr.available():
        st = ocr.status()
        out(st, args.json, f"OCR 不可用：{st['hint']}")
        return 1
    region = None
    if args.region:
        p = [int(v) for v in args.region.replace(" ", "").split(",")]
        if len(p) == 4:
            region = {"left": p[0], "top": p[1], "width": p[2], "height": p[3]}
    if args.find:
        blocks = ocr.find_text(args.find, region=region, exact=args.exact)
    else:
        blocks = ocr.recognize(region=region)
    if args.annotate and blocks:
        from PIL import Image
        path, rect = capture.save(region=region, prefix="ocr")
        img = Image.open(path)
        marked = annotate.annotate(img, rect, [], ocr_blocks=blocks)
        ap = path.replace(".png", "_annotated.png")
        marked.save(ap)
        for b in blocks:
            pass
    if args.json:
        out(blocks, True)
        return
    print(f"识别到 {len(blocks)} 个文字块（引擎 {ocr.engine_name()}）")
    for b in blocks:
        print(f"  {b['rect']}  {b['text']}")


def cmd_activate(args):
    h = uia.find_window(title=args.window, hwnd=args.hwnd, exact=args.exact)
    if not h:
        out({"error": "找不到窗口"}, args.json, "找不到窗口")
        return 1
    ok = uia.activate(h)
    res = {"hwnd": h, "activated": ok}
    out(res, args.json, f"窗口 {h} 激活{'成功' if ok else '失败（可能被系统阻止）'}")


def cmd_tree(args):
    h = uia.find_window(title=args.window, hwnd=args.hwnd) if (args.window or args.hwnd) \
        else uia.foreground_window()
    if not h:
        print("找不到窗口")
        return 1
    lines = uia.tree_summary(h, max_depth=args.depth)
    print("\n".join(lines) if lines else "(空控件树 —— 该应用可能不支持 UIA)")


# ------------------------------------------------------------------ 键盘
def _parse_xy(s: str):
    p = [int(v) for v in str(s).replace(" ", "").split(",")]
    if len(p) != 2:
        raise ValueError(f"坐标格式应为 x,y，收到 {s!r}")
    return p[0], p[1]


def _locate_for_focus(args):
    """为键盘输入定位一个「先点一下取得焦点」的目标。"""
    want_id = getattr(args, "id", None)
    want_text = getattr(args, "target_text", None)
    if not want_id and not want_text:
        return None, None, None, ""
    sess = session.load()
    if not sess:
        return None, None, None, "尚无扫描会话（请先 scan，或省略 --id 直接输入到当前焦点）"
    if want_id:
        e = session.get(want_id, sess)
    else:
        hits = session.find(sess, text=want_text, clickable_only=True)
        e = hits[0] if hits else None
    if not e:
        return None, None, None, f"会话中找不到目标元素（{want_id or want_text!r}）"
    r = session.resolve_target(e, sess, verify=not getattr(args, "no_verify", False))
    if not r["ok"]:
        return None, None, None, f"目标校验未通过：{r['reason']}"
    return r["x"], r["y"], e, r.get("reason", "")


def cmd_type(args):
    if getattr(args, "window", None):
        h = uia.find_window(title=args.window)
        if h:
            uia.activate(h)
            time.sleep(0.25)

    x, y, elem, note = _locate_for_focus(args)
    if note and (getattr(args, "id", None) or getattr(args, "target_text", None)):
        out({"error": note}, args.json, f"✗ {note}")
        return 1

    if _locked_guard(args, "type"):
        return 3
    # --clear 会清空原内容，属于不可逆动作之一
    if args.clear and _danger_gate(args, "type-clear", "清空",
                                   (elem or {}).get("name"),
                                   _window_title_at(*(elem["center"] if elem else (0, 0)))):
        return 4

    if x is not None:
        mouse.click(x, y, dry_run=args.dry_run)
        time.sleep(0.14)

    if args.dry_run:
        res = {"action": "type", "dry_run": True, "would_type": args.text[:80],
               "focus_point": [x, y] if x is not None else None,
               "focus_element": elem.get("id") if elem else None,
               "method": args.method}
        if x is None:
            where = "当前焦点"
        elif elem:
            where = f"元素 {elem.get('id')}"
        else:
            where = f"坐标 {[x, y]}"
        out(res, args.json,
            f"（演练）将向 {where} 输入 {len(args.text)} 个字符：{args.text[:40]!r}")
        return

    if args.clear:
        keyboard.clear_field()
        time.sleep(0.08)

    res = keyboard.type_text(args.text, method=args.method, press_enter=args.enter,
                             restore_clipboard=not args.keep_clipboard)
    res["focus_point"] = [x, y] if x is not None else None
    res["focus_element"] = elem.get("id") if elem else None
    safety.audit("type", length=res["length"], method=res["method"],
                 preview=res.get("preview"), element_id=res["focus_element"],
                 element_name=(elem or {}).get("name"), cleared=bool(args.clear),
                 enter=bool(args.enter))
    if args.json:
        out(res, True)
        return
    print(f"已输入 {res['length']} 个字符（通道 {res['method']}）"
          + (f"  焦点=元素 {elem.get('id')}" if elem else "  焦点=当前")
          + ("  并回车" if args.enter else ""))
    if args.clear:
        print("  （已先清空原内容）")


def cmd_key(args):
    seq = [k for k in (args.keys or []) if k]
    if not seq:
        out({"error": "未给出按键"}, args.json, "✗ 未给出按键")
        return 1
    if _locked_guard(args, "key"):
        return 3
    if _danger_gate(args, "key", *seq):
        return 4
    if len(seq) == 1:
        if "+" in seq[0]:
            res = keyboard.hotkey(seq[0])
        else:
            res = keyboard.press(seq[0], count=args.count)
    else:
        res = keyboard.send_keys_sequence(seq)
    safety.audit("key", sequence=seq)
    if args.json:
        out(res, True)
        return
    if res["action"] == "hotkey":
        print(f"已发送组合键: {'+'.join(res['keys'])}")
    elif res["action"] == "press":
        print(f"已按键: {res['key']} ×{res['count']}")
    else:
        print(f"已依次发送: {' '.join(res['sequence'])}")


# ------------------------------------------------------------------ 拖拽 / 滑动
def cmd_drag(args):
    src = dst = None
    if args.from_xy and args.to_xy:
        x1, y1 = _parse_xy(args.from_xy)
        x2, y2 = _parse_xy(args.to_xy)
    elif args.from_id or args.to_id:
        sess = session.load()
        if not sess:
            out({"error": "使用 --from-id/--to-id 需要先 scan"}, args.json,
                "✗ 使用 --from-id/--to-id 需要先 scan")
            return 1
        def _one(eid, xy):
            if xy:
                a, b = _parse_xy(xy)
                return {"id": "xy", "center": [a, b]}
            e = session.get(eid, sess)
            if not e:
                return None
            r = session.resolve_target(e, sess, verify=not args.no_verify)
            if not r["ok"]:
                return {"__error": f"{eid} 校验未通过：{r['reason']}"}
            ee = dict(e)
            ee["center"] = [r["x"], r["y"]]
            return ee
        src = _one(args.from_id, args.from_xy)
        dst = _one(args.to_id, args.to_xy)
        if (src and src.get("__error")) or (dst and dst.get("__error")):
            msg = (src or dst).get("__error")
            out({"error": msg}, args.json, f"✗ {msg}")
            return 1
        if not src or not dst:
            out({"error": "起点或终点无法解析"}, args.json, "✗ 起点或终点无法解析")
            return 1
        x1, y1 = src["center"]
        x2, y2 = dst["center"]
    else:
        out({"error": "需要 --from-xy/--to-xy 或 --from-id/--to-id"}, args.json,
            "✗ 需要 --from-xy/--to-xy 或 --from-id/--to-id")
        return 1

    if _locked_guard(args, "drag"):
        return 3
    if _danger_gate(args, "drag", (src or {}).get("name"), (dst or {}).get("name"),
                    _window_title_at(x2, y2)):
        return 4

    warn = _corner_guard(x2, y2, args.allow_corners)
    if warn and not args.dry_run:
        safety.audit("drag", blocked=True, reason=warn, x1=x1, y1=y1, x2=x2, y2=y2)
        out({"error": warn}, args.json, f"✗ 已拦截：{warn}")
        return 2

    saved = mouse.cursor_pos()
    res = mouse.drag(x1, y1, x2, y2, duration=args.duration, button=args.button,
                     steps=args.steps, hold_before=args.hold_before,
                     hold_after=args.hold_after, dry_run=args.dry_run)
    safety.audit("drag", x1=x1, y1=y1, x2=x2, y2=y2, dry_run=bool(args.dry_run),
                 from_element=(src or {}).get("id"), to_element=(dst or {}).get("id"),
                 distance=res.get("distance"))
    if src and src.get("id") not in (None, "xy"):
        res["from_element"] = src.get("id")
    if dst and dst.get("id") not in (None, "xy"):
        res["to_element"] = dst.get("id")
    if args.restore_cursor and not args.dry_run:
        mouse.restore_cursor_position(saved)

    if args.shot_after and not args.dry_run:
        time.sleep(0.5)
        res["screenshot_after"] = capture.save(prefix="afterdrag")[0]

    if args.json:
        out(res, True)
        return
    tag = "模拟" if args.dry_run else "已执行"
    print(f"{tag} {args.button} 拖拽 ({x1},{y1}) → ({x2},{y2})")
    print(f"  距离 {res['distance']}px，分 {res['steps']} 步，用时约 {args.duration}s")
    if res.get("actual"):
        print(f"  光标落点 {res['actual']}")
    if res.get("screenshot_after"):
        print(f"  拖拽后截图: {res['screenshot_after']}")


def cmd_swipe(args):
    """快速滑动：等价于短时长、短保持的拖拽。"""
    if args.duration is None:
        args.duration = 0.18
    args.hold_before = 0.05
    args.hold_after = 0.05
    return cmd_drag(args)


# ------------------------------------------------------------------ 人工介入
def cmd_guard(args):
    region = None
    if args.region:
        p = [int(v) for v in args.region.replace(" ", "").split(",")]
        if len(p) == 4:
            region = {"left": p[0], "top": p[1], "width": p[2], "height": p[3]}
    kw = [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
    hwnd = None
    if getattr(args, "hwnd", None):
        hwnd = int(args.hwnd)
    elif getattr(args, "window", None):
        hwnd = uia.find_window(title=args.window)

    res = human.check(mode=args.mode, region=region, hwnd=hwnd,
                      extra_keywords=kw or None)
    if args.json:
        out(res, True)
        return 0 if not res["human_required"] else 3

    icon = {"high": "🔴", "medium": "🟠", "low": "🟡", "none": "🟢"}[res["level"]]
    print(f"{icon} 需人工介入: {'是' if res['human_required'] else '否'}"
          f"  (等级 {res['level']}, 得分 {res['score']}, 耗时 {res['elapsed_ms']}ms)")
    print(f"   检测层: {', '.join(res['layers'])}")
    if res["categories"]:
        print(f"   类别: {', '.join(res['categories'])}")
    for e in res["evidence"][:8]:
        src = e.get("source", "")
        print(f"   · [{src}] {e['keyword']}  ←  {e['text'][:52]}")
    if res["advice"]:
        print(f"\n   {res['advice']}")
    return 0 if not res["human_required"] else 3


def cmd_wait_human(args):
    kw = [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
    print(f"正在等待人工环节消失（最长 {args.timeout:.0f}s，每 {args.poll}s 复查一次）…")
    res = human.wait_until_clear(timeout=args.timeout, poll=args.poll,
                                 mode=args.mode, extra_keywords=kw or None)
    if args.json:
        out(res, True)
        return 0 if res["cleared"] else 4
    if res["cleared"]:
        print(f"✅ 已恢复正常（{res['elapsed_s']}s，复查 {res['rounds']} 次）")
    else:
        print(f"⏱ {res['reason']}")
        last = res.get("last") or {}
        for e in (last.get("evidence") or [])[:5]:
            print(f"   · 仍检测到 [{e.get('source')}] {e['keyword']}")
    return 0 if res["cleared"] else 4


# ------------------------------------------------------------------ 浏览器
def cmd_browser(args):
    action = (args.action or "status").lower()
    kind = args.browser or "edge"

    if action in ("status", "info"):
        st = browser.status(kind)
        if args.json:
            out(st, True)
            return 0 if st["running"] else 5
        if not st["running"]:
            print(f"{kind} 未运行（可用 `browser launch` 启动）")
            return 5
        print(f"{st['browser']} 运行中  hwnd={st['hwnd']}")
        print(f"窗口标题: {st['window_title'][:70]}")
        print(f"标签页 {st['tab_count']} 个:")
        for t in st["tabs"]:
            print(f"  [{t['index']}] {t['title'][:60] if t['title'] else '(无标题)'}")
        return 0

    if action == "launch":
        r = browser.open_browser(kind, args.url)
        if not r.get("ok"):
            out(r, args.json, f"✗ {r['error']}")
            return 1
        for _ in range(30):
            if browser.find_browser(kind):
                break
            time.sleep(0.3)
        h = browser.find_browser(kind)
        out(r, args.json, f"已启动 {kind}" + (f" 并打开 {args.url}" if args.url else "")
            + (f"  hwnd={h}" if h else "（等待窗口出现）"))
        return 0

    hwnd = int(args.hwnd) if args.hwnd else browser.find_browser(kind)
    if not hwnd:
        msg = f"找不到已运行的 {kind} 窗口（可先 `browser launch`）"
        out({"error": msg}, args.json, f"✗ {msg}")
        return 5

    if action == "tabs":
        tabs = browser.list_tabs(hwnd)
        data = [{"index": t["index"], "title": t.get("tab_title"),
                 "center": t["center"], "selected": bool(t.get("selected"))}
                for t in tabs]
        if args.json:
            out({"hwnd": hwnd, "tabs": data}, True)
            return 0
        print(f"标签页 {len(data)} 个（hwnd={hwnd}）:")
        for t in data:
            mark = "▶" if t["selected"] else " "
            print(f" {mark}[{t['index']}] {t['title'][:64] if t['title'] else '(无标题)'}")
        return 0

    if action == "scan":
        items = browser.scan_page(hwnd)
        if args.json:
            out({"hwnd": hwnd, "count": len(items), "elements": items}, True)
            return 0
        print(f"页面内可交互元素 {len(items)} 个:")
        for it in items[:args.limit or 40]:
            print(f"   {it['role']:<10} {(it.get('name') or '')[:46]:<48} @{it['center']}")
        return 0

    if action == "open":
        if not args.url:
            out({"error": "需要 URL"}, args.json, "✗ 需要 URL")
            return 1
        r = browser.open_url(args.url, hwnd, new_tab=False)
        out(r, args.json, f"已打开 {args.url}" if r.get("ok") else f"✗ {r.get('error')}")
        return 0 if r.get("ok") else 1

    if action in ("new-tab", "newtab"):
        r = browser.open_url(args.url or "", hwnd, new_tab=True)
        out(r, args.json, f"已新建标签" + (f" 并打开 {args.url}" if args.url else "")
            if r.get("ok") else f"✗ {r.get('error')}")
        return 0 if r.get("ok") else 1

    if action in ("back", "forward", "refresh", "reload", "hard-refresh", "stop",
                  "home", "top", "bottom"):
        r = browser.navigate(hwnd, action)
        out(r, args.json, f"已执行 {action}" + (f"  → {r.get('title_after','')[:60]}" if r.get("ok") else f"  ✗ {r.get('error')}")
            if r.get("ok") else f"✗ {r.get('error')}")
        return 0 if r.get("ok") else 1

    if action in ("close-tab", "closetab"):
        r = browser.close_tab(hwnd)
        out(r, args.json, "已关闭当前标签" if r.get("ok") else f"✗ {r.get('error')}")
        return 0 if r.get("ok") else 1

    if action in ("switch", "switch-tab"):
        if args.title:
            r = browser.switch_tab(hwnd, title=args.title)
        elif args.index:
            r = browser.switch_tab(hwnd, index=int(args.index))
        else:
            out({"error": "需要 --index 或 --title"}, args.json, "✗ 需要 --index 或 --title")
            return 1
        out(r, args.json, f"已切换 → {r.get('switched_to') or r.get('title_after','')[:60]}"
            if r.get("ok") else f"✗ {r.get('error')}")
        return 0 if r.get("ok") else 1

    if action == "find":
        if not args.text:
            out({"error": "需要查找文字"}, args.json, "✗ 需要查找文字")
            return 1
        r = browser.find_in_page(hwnd, args.text)
        out(r, args.json, f"已在页面内查找 {args.text!r}" if r.get("ok") else f"✗ {r.get('error')}")
        return 0 if r.get("ok") else 1

    out({"error": f"未知动作 {action!r}"}, args.json,
        f"✗ 未知动作 {action!r}\n可用: status / tabs / scan / launch / open / new-tab / "
        f"back / forward / refresh / stop / home / top / bottom / close-tab / switch / find")
    return 1


def cmd_selftest(args):
    sys.argv = [sys.argv[0]]
    from tools import selftest  # type: ignore
    return selftest.main([])


# ------------------------------------------------------------------ 入口
def build_parser():
    p = argparse.ArgumentParser(
        prog="sa.py", description="screen-agent：观察屏幕并模拟鼠标操作",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  sa.py info
  sa.py windows
  sa.py scan --window "记事本" --activate
  sa.py show --clickable
  sa.py click --id E3
  sa.py click --text "确定" --button left
  sa.py click --xy 1280,800 --dry-run
  sa.py find "登录"
  sa.py ocr --find "提交"
""")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("info", help="环境与坐标系信息")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("windows", help="列出顶层窗口")
    sp.add_argument("--all", action="store_true", help="包含不可见窗口")
    sp.add_argument("--untitled", action="store_true", help="包含无标题窗口")
    sp.add_argument("--no-minimized", action="store_true", help="排除最小化窗口")
    sp.set_defaults(func=cmd_windows)

    sp = sub.add_parser("shot", help="截屏")
    sp.add_argument("--out", help="输出路径")
    sp.add_argument("--region", help="区域 l,t,w,h")
    sp.add_argument("--window", help="按窗口标题截取")
    sp.add_argument("--hwnd", type=int, help="按窗口句柄截取")
    sp.add_argument("--grid", action="store_true", help="叠加坐标网格")
    sp.add_argument("--grid-step", type=int, default=200)
    sp.add_argument("--cursor", action="store_true", help="标记光标位置")
    sp.set_defaults(func=cmd_shot)

    sp = sub.add_parser("scan", help="扫描可交互元素并建立会话")
    sp.add_argument("--window", help="目标窗口标题（模糊匹配）")
    sp.add_argument("--hwnd", type=int, help="目标窗口句柄")
    sp.add_argument("--exact-title", action="store_true")
    sp.add_argument("--foreground", action="store_true", help="前台窗口（默认）")
    sp.add_argument("--all", action="store_true", help="扫描所有可见窗口")
    sp.add_argument("--max-windows", type=int, default=8)
    sp.add_argument("--activate", action="store_true", help="扫描前把窗口切到前台")
    sp.add_argument("--include-text", action="store_true", help="同时收录文本元素")
    sp.add_argument("--role", default="", help="只保留指定角色，如 button/input/link")
    sp.add_argument("--clickable-only", action="store_true")
    sp.add_argument("--limit", type=int, default=0, help="最多返回 N 个元素")
    sp.add_argument("--no-shot", action="store_true", help="不截图")
    sp.add_argument("--no-annotate", action="store_true", help="不生成标注图")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("show", help="显示上次扫描的会话")
    sp.add_argument("--limit", type=int, default=0)
    sp.add_argument("--clickable", action="store_true")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("find", help="在会话中按文字/角色查找")
    sp.add_argument("text")
    sp.add_argument("--role", default="")
    sp.add_argument("--exact", action="store_true")
    sp.add_argument("--include-disabled", action="store_true")
    sp.add_argument("--vision", action="store_true", default=True,
                    help="UIA 未命中时用 OCR 兜底（默认开）")
    sp.set_defaults(func=cmd_find)

    sp = sub.add_parser("click", help="点击（元素编号 / 文字 / 坐标）")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="会话中的元素编号，如 E3")
    g.add_argument("--text", help="按元素名称文字定位")
    g.add_argument("--xy", help="直接坐标 x,y")
    sp.add_argument("--button", default="left", choices=["left", "right", "middle"])
    sp.add_argument("--count", type=int, default=1, help="点击次数，2=双击")
    sp.add_argument("--exact", action="store_true", help="文字精确匹配")
    sp.add_argument("--duration", type=float, default=0.0, help="移动耗时（平滑移动）")
    sp.add_argument("--dry-run", action="store_true", help="只定位并移动，不真正点击")
    sp.add_argument("--no-verify", action="store_true", help="跳过陈旧坐标校验（不推荐）")
    sp.add_argument("--force", action="store_true", help="允许使用过期会话")
    sp.add_argument("--allow-corners", action="store_true", help="允许点击屏幕角落热区")
    sp.add_argument("--shot-after", action="store_true", help="点击后自动截图")
    sp.add_argument("--shot-delay", type=float, default=0.8)
    sp.set_defaults(func=cmd_click)

    sp = sub.add_parser("move", help="移动光标")
    sp.add_argument("--xy", required=True)
    sp.add_argument("--duration", type=float, default=0.0)
    sp.add_argument("--allow-corners", action="store_true")
    sp.set_defaults(func=cmd_move)

    sp = sub.add_parser("scroll", help="滚轮")
    sp.add_argument("amount", type=int, help="正数向上，负数向下（一格=120）")
    sp.add_argument("--xy", help="先移动到该点再滚")
    sp.set_defaults(func=cmd_scroll)

    sp = sub.add_parser("ocr", help="OCR 识别文字（视觉兜底）")
    sp.add_argument("--region", help="区域 l,t,w,h")
    sp.add_argument("--find", help="只显示包含该文字的结果")
    sp.add_argument("--exact", action="store_true")
    sp.add_argument("--annotate", action="store_true", help="生成标注图")
    sp.set_defaults(func=cmd_ocr)

    sp = sub.add_parser("activate", help="把窗口切到前台")
    sp.add_argument("--window")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--exact", action="store_true")
    sp.set_defaults(func=cmd_activate)

    sp = sub.add_parser("tree", help="打印窗口控件树（调试用）")
    sp.add_argument("--window")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--depth", type=int, default=4)
    sp.set_defaults(func=cmd_tree)

    # ---------------- 键盘 ----------------
    sp = sub.add_parser("type", help="输入文本（支持中文）")
    sp.add_argument("text", help="要输入的文本")
    sp.add_argument("--id", help="先点击该元素取得焦点（需先 scan）")
    sp.add_argument("--target-text", help="按元素名称定位焦点（模糊匹配）")
    sp.add_argument("--window", help="先激活该标题的窗口")
    sp.add_argument("--method", default="auto",
                    choices=["auto", "unicode", "clipboard"],
                    help="unicode=直接投递字符码点（默认）；clipboard=剪贴板粘贴（兼容性最好）")
    sp.add_argument("--clear", action="store_true", help="先清空输入框内容")
    sp.add_argument("--enter", action="store_true", help="输入后按回车")
    sp.add_argument("--keep-clipboard", action="store_true", help="不恢复剪贴板原内容")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-verify", action="store_true")
    sp.set_defaults(func=cmd_type)

    sp = sub.add_parser("key", help="按键 / 组合键")
    sp.add_argument("keys", nargs="+",
                    help="如 enter / f5 / ctrl+s ；多个参数则依次发送（如 ctrl c ctrl v）")
    sp.add_argument("--count", type=int, default=1, help="单键重复次数")
    sp.set_defaults(func=cmd_key)

    # ---------------- 拖拽 / 滑动 ----------------
    sp = sub.add_parser("drag", help="拖拽（按住起点拖到终点）")
    sp.add_argument("--from-xy", help="起点 x,y")
    sp.add_argument("--to-xy", help="终点 x,y")
    sp.add_argument("--from-id", help="起点元素编号（需先 scan）")
    sp.add_argument("--to-id", help="终点元素编号（需先 scan）")
    sp.add_argument("--duration", type=float, default=0.45, help="拖拽耗时（秒）")
    sp.add_argument("--steps", type=int, default=0, help="插值步数（0=按距离自动）")
    sp.add_argument("--hold-before", type=float, default=0.14, help="按下后停留多久再移动")
    sp.add_argument("--hold-after", type=float, default=0.16, help="到达后停留多久再松开")
    sp.add_argument("--button", default="left", choices=["left", "right", "middle"])
    sp.add_argument("--dry-run", action="store_true", help="只演示不实际操作")
    sp.add_argument("--no-verify", action="store_true")
    sp.add_argument("--restore-cursor", action="store_true", help="完成后把光标放回原处")
    sp.add_argument("--allow-corners", action="store_true")
    sp.add_argument("--shot-after", action="store_true")
    sp.set_defaults(func=cmd_drag)

    sp = sub.add_parser("swipe", help="快速滑动（短时拖拽）")
    sp.add_argument("--from-xy"); sp.add_argument("--to-xy")
    sp.add_argument("--from-id"); sp.add_argument("--to-id")
    sp.add_argument("--duration", type=float, default=None)
    sp.add_argument("--steps", type=int, default=0)
    sp.add_argument("--button", default="left", choices=["left", "right", "middle"])
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-verify", action="store_true")
    sp.add_argument("--restore-cursor", action="store_true")
    sp.add_argument("--allow-corners", action="store_true")
    sp.add_argument("--shot-after", action="store_true")
    sp.set_defaults(func=cmd_swipe)

    # ---------------- 人工介入 ----------------
    sp = sub.add_parser("guard", help="检测当前是否出现验证码/登录等需人工介入的环节")
    sp.add_argument("--mode", default="auto", choices=["auto", "fast", "deep"],
                    help="fast=标题+UIA；deep=额外跑 OCR；auto=fast 无结论时再 OCR")
    sp.add_argument("--region", help="OCR 区域 l,t,w,h（默认前台窗口）")
    sp.add_argument("--keywords", help="额外的自定义关键词，逗号分隔")
    sp.add_argument("--window", help="指定窗口标题")
    sp.add_argument("--hwnd", type=int)
    sp.set_defaults(func=cmd_guard)

    sp = sub.add_parser("wait-human", help="等待人工环节消失（用户完成验证后自动继续）")
    sp.add_argument("--timeout", type=float, default=300.0)
    sp.add_argument("--poll", type=float, default=5.0)
    sp.add_argument("--mode", default="fast", choices=["auto", "fast", "deep"])
    sp.add_argument("--keywords", help="自定义关键词，逗号分隔")
    sp.set_defaults(func=cmd_wait_human)

    # ---------------- 浏览器 ----------------
    sp = sub.add_parser("browser", help="Edge/Chrome 操作（复用你已登录的浏览器）")
    sp.add_argument("action", nargs="?", default="status",
                    help="status|tabs|scan|launch|open|new-tab|back|forward|refresh|"
                         "hard-refresh|stop|home|top|bottom|close-tab|switch|find")
    sp.add_argument("--url", help="open / new-tab / launch 的 URL")
    sp.add_argument("--index", help="switch 的标签序号 1-9")
    sp.add_argument("--title", help="switch 的标签标题（模糊匹配）")
    sp.add_argument("--text", help="find 要查找的文字")
    sp.add_argument("--browser", default="edge", help="edge / chrome")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--limit", type=int, default=40)
    sp.set_defaults(func=cmd_browser)

    sp = sub.add_parser("selftest", help="自检：验证整条链路")
    sp.set_defaults(func=cmd_selftest)

    sp = sub.add_parser("audit", help="查看最近的自动化操作记录")
    sp.add_argument("--n", type=int, default=20, help="显示条数")
    sp.set_defaults(func=cmd_audit)

    # ---- 统一给"会动鼠标/键盘"的子命令补上安全开关 ----
    # 用遍历而不是逐个写，避免以后新增命令漏掉护栏。
    for name in ("click", "drag", "swipe", "type", "key", "scroll", "move"):
        try:
            s = sub.choices[name]
        except KeyError:
            continue
        s.add_argument("--yes", action="store_true",
                       help="确认执行不可逆操作（删除/支付/提交等），跳过危险闸门")
        s.add_argument("--keep-cursor", action="store_true",
                       help="--dry-run 后把光标留在目标点（默认移回原位）")

    return p


def cmd_audit(args):
    rows = safety.tail(args.n)
    out(rows, args.json, safety.audit_text(args.n))


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = args.func(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except Exception as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        else:
            import traceback
            print(f"✗ {type(e).__name__}: {e}")
            if os.environ.get("SA_DEBUG"):
                traceback.print_exc()
        return 1
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
