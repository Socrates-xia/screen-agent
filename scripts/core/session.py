"""扫描会话：元素编号索引 + 陈旧坐标防护。

设计意图：让调用方（Agent）永远用「元素编号」而不是「裸坐标」下点击指令。
  裸坐标是脆弱的 —— 界面一滚动、窗口一移动，同一个 (x,y) 就指向别的东西，
  在没有 undo 的界面上这可能造成真实破坏。
  编号则绑定到「元素身份 + 采集时刻」，点击前会做三重校验：
    ① 会话是否过期   ② 窗口是否移动   ③ 元素是否还在原地
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from . import capture, dpi, geom, uia

SESSION_FILE = "session.json"
DEFAULT_MAX_AGE = 180.0        # 会话超过 3 分钟视为过期
RELOCATE_TOLERANCE = 40        # 重新定位后允许的像素漂移


def _path() -> str:
    return os.path.join(capture.runtime_dir(), SESSION_FILE)


def save(elements: List[dict], scope: dict, windows: Optional[List[dict]] = None,
         screenshot: Optional[str] = None, annotated: Optional[str] = None,
         notes: Optional[dict] = None) -> dict:
    """写入新会话，为每个元素分配 E1/E2/... 编号。返回会话字典。"""
    vid = f"s-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    vd = dpi.virtual_desktop()
    out_elems = []
    for i, it in enumerate(elements, 1):
        e = dict(it)
        e["id"] = f"E{i}"
        out_elems.append(e)

    sess = {
        "session_id": vid,
        "created": time.time(),
        "created_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dpi_mode": dpi.mode(),
        "virtual_desktop": vd,
        "scope": scope,
        "windows": windows or [],
        "count": len(out_elems),
        "elements": out_elems,
        "screenshot": screenshot,
        "annotated": annotated,
        "notes": notes or {},
    }
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sess, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path())
    return sess


def load() -> Optional[dict]:
    p = _path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def age(sess: Optional[dict] = None) -> float:
    sess = sess or load()
    if not sess:
        return float("inf")
    return time.time() - sess.get("created", 0)


def get(element_id: str, sess: Optional[dict] = None) -> Optional[dict]:
    sess = sess or load()
    if not sess:
        return None
    eid = element_id.strip().upper()
    for e in sess["elements"]:
        if e["id"] == eid:
            return e
    return None


def find(sess: dict, text: str = "", role: str = "", exact: bool = False,
         clickable_only: bool = True) -> List[dict]:
    """在会话元素里按名称/角色筛选。"""
    tl = (text or "").lower()
    rl = (role or "").lower()
    hits = []
    for e in sess.get("elements", []):
        if clickable_only and not e.get("clickable"):
            continue
        if rl and e.get("role", "").lower() != rl and e.get("type", "").lower() != rl:
            continue
        if tl:
            nm = (e.get("name") or "").lower()
            aid = (e.get("automation_id") or "").lower()
            if exact:
                if nm != tl and aid != tl:
                    continue
            else:
                if tl not in nm and tl not in aid:
                    continue
        hits.append(e)
    # 面积小的优先（更像具体控件而非容器）
    hits.sort(key=lambda x: geom.width(x["rect"]) * geom.height(x["rect"]))
    return hits


def resolve_target(elem: dict, sess: dict, verify: bool = True,
                   tolerance: int = RELOCATE_TOLERANCE,
                   strict: bool = False) -> dict:
    """把会话元素解析成「现在可以点的坐标」，并给出可信度说明。

    校验层层递进，越靠后越慢、也越可靠：
      ① 屏幕布局是否变过      ② 窗口是否移动过
      ③ 该点下方的元素是否还是它（拦截「窗口没动但界面滚动/切页」这类最危险的错位）
      ④ 必要时整窗重扫按特征重新定位

    返回 {ok, x, y, reason, method, element_id, moved}
    strict=True 时，任何无法确认的情况都拒绝执行。
    """
    eid = elem.get("id")
    res = {"element_id": eid, "ok": False, "reason": "", "method": "cache",
           "x": None, "y": None, "moved": 0}

    vd = dpi.virtual_desktop()
    if vd != sess.get("virtual_desktop"):
        res["reason"] = (f"屏幕布局已变化（扫描时 {sess.get('virtual_desktop')}，"
                         f"现在 {vd}），请重新扫描")
        return res

    if not elem.get("plausible", True):
        res["reason"] = "该元素的 UIA 坐标不在屏幕范围内（可能是 WinUI/XAML 应用），请改用视觉方案"
        return res

    if not elem.get("clickable", True):
        res["reason"] = "该元素扫描时被判定为不可点击（可能已禁用，或是容器而非控件）"
        return res

    cx, cy = elem["center"]

    if not verify:
        res.update(ok=True, x=cx, y=cy, method="no-verify")
        return res

    # ---- ③ 点下元素身份复核（最快、最能拦住界面变化与遮挡）----
    verdict = uia.verify_point(cx, cy, elem)
    if verdict == "match":
        res.update(ok=True, x=cx, y=cy, method="point-match")
        return res
    if verdict == "ancestor":
        res.update(ok=True, x=cx, y=cy, method="point-ancestor",
                   reason="点下为目标的父级容器，已按原坐标执行")
        return res
    if verdict == "occluded":
        res["reason"] = ("目标已被其他窗口遮挡 —— 该点现在位于另一个窗口上，"
                         "点下去会打到遮挡窗口。请先用 activate 把目标窗口切到前台，"
                         "或重新 scan 当前可见的界面")
        return res

    # ---- ④ 复核不通过 → 尝试重新定位 ----
    rel = uia.relocate(elem, timeout=1.5)
    if rel is not None:
        nx, ny = rel["center"]
        res["moved"] = max(abs(nx - cx), abs(ny - cy))
        res.update(x=nx, y=ny, method=rel["method"])
        # 新位置再过一次点下复核。
        # 必须与快路径一样拦截 occluded —— 重定位恰恰发生在界面发生变化时，
        # 而界面变化正是"目标被别的窗口盖住"最容易出现的时刻。
        # 旧实现只拦 mismatch，occluded 会从这里漏过去并照常点击。
        v2 = uia.verify_point(nx, ny, {"rect": rel["rect"]})
        if v2 == "occluded":
            res["ok"] = False
            res["reason"] = ("重新定位后的位置仍被其他窗口遮挡 —— 点下去会打到遮挡窗口。"
                             "请先用 activate 把目标窗口切到前台，或重新 scan")
            return res
        if v2 == "mismatch":
            res["ok"] = False
            res["reason"] = "重新定位后的位置仍与其下方元素不符，界面可能正在变化"
            return res
        if v2 == "unknown" and strict:
            res["ok"] = False
            res["reason"] = "重新定位后的位置下方无 UIA 元素可核对，严格模式下拒绝执行"
            return res
        res["ok"] = True
        if res["moved"] > tolerance:
            res["reason"] = (f"元素已从 {elem['center']} 移动到 [{nx},{ny}]"
                             f"（位移 {res['moved']}px），已按最新位置执行")
        return res

    if verdict == "mismatch":
        res["ok"] = False
        res["reason"] = ("该点下方的元素与扫描时记录的不一致 —— 界面很可能已经变化"
                         "（滚动、切页或弹窗）。请重新 scan 后再点击")
        return res

    # verdict == "unknown"：该点没有 UIA 元素（纯绘制区域），无法确认
    if strict:
        res["reason"] = "无法确认该点下方的元素（该区域无 UIA 元素），严格模式下拒绝执行"
        return res
    res.update(ok=True, x=cx, y=cy, method="cache-unverified",
               reason="该点下方无 UIA 元素可核对，已按扫描时坐标执行")
    return res


def describe(sess: dict, limit: int = 0, clickable_only: bool = False) -> str:
    """生成给人和 Agent 看的文本清单。"""
    lines = []
    head = (f"会话 {sess['session_id']} | 采集于 {sess['created_str']} | "
            f"共 {sess['count']} 个元素")
    lines.append(head)
    sc = sess.get("scope", {})
    lines.append(f"范围: {sc.get('mode')} {sc.get('title') or sc.get('hwnd') or ''}")
    if sess.get("annotated"):
        lines.append(f"标注图: {sess['annotated']}")
    lines.append("-" * 68)
    elems = sess.get("elements", [])
    if clickable_only:
        elems = [e for e in elems if e.get("clickable")]
    if limit:
        elems = elems[:limit]
    for e in elems:
        flag = "" if e.get("plausible", True) else " [!坐标不可信]"
        if not e.get("enabled", True):
            flag += " [已禁用]"
        nm = (e.get("name") or "").replace("\n", " ")[:44]
        lines.append(f"{e['id']:>5} {e['role']:<10} {nm:<46}{flag} @{e['center']}")
    return "\n".join(lines)
