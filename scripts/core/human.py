"""人工介入检测：判断当前界面是否出现「必须由人来做」的环节。

为什么需要它：
  自动化流程最怕的不是失败，而是**自以为成功**。遇到登录页、图形验证码、
  滑块验证时，如果脚本继续照旧点击，轻则点了空处，重则触发风控或误提交。
  正确的做法是：识别出来 → 停下来 → 明确告诉用户「该你了」→ 等你完成后继续。

三层检测（由快到慢，默认按需组合）：
  ① 窗口标题  —— 零成本，先排除「验证码 - Google Chrome」这类明显的
  ② UIA 文本  —— 约 0.7s，能读取控件名/按钮文字，还能发现密码输入框
  ③ OCR       —— 约数秒（区域越大越慢），对付自绘界面、Canvas、游戏

评分分级：命中强特征词直接判 high；只有弱特征才判 low（提示但不阻断）。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from . import keyboard, uia

# --------------------------------------------------------------- 特征词库
# weight: 10=几乎必然是人工环节  4=很可能  1=仅作参考
PATTERNS: Dict[str, Dict[str, int]] = {
    "captcha": {
        "验证码": 10, "图形验证码": 10, "短信验证码": 8, "请输入验证码": 10,
        "获取验证码": 8, "captcha": 10, "verification code": 10,
        "recaptcha": 10, "hcaptcha": 10, "点击验证": 10, "看不清": 4,
        "换一张": 4,
    },
    "human_verify": {
        "人机验证": 10, "安全验证": 8, "请完成验证": 10, "滑动验证": 10,
        "滑块验证": 10, "拖动滑块": 10, "请按住滑块": 10, "按住滑块": 10,
        "行为验证": 8, "智能验证": 8, "无感知验证": 6, "验证你是人类": 10,
        "verify you are human": 10, "滑动以完成验证": 10, "向右滑动": 8,
    },
    "login": {
        "登录": 4, "请登录": 6, "账号登录": 6, "扫码登录": 8, "手机号登录": 6,
        "登入": 4, "sign in": 6, "log in": 6, "login": 4, "password": 6,
        "密码": 4, "用户名": 1, "账号": 1, "手机号": 1, "记住我": 1,
    },
    "two_factor": {
        "双重验证": 8, "两步验证": 8, "二次验证": 8, "身份验证": 6,
        "手机验证": 6, "动态口令": 8, "验证器": 6, "authenticator": 8,
        "请输入收到的验证码": 10, "6位验证码": 10, "六位验证码": 10,
    },
    "risk": {
        "风险提示": 6, "账号异常": 6, "安全中心": 4, "正在验证": 6,
        "请稍候": 1, "操作过于频繁": 6, "环境异常": 8, "请完成安全校验": 10,
    },
    "payment": {
        "支付密码": 10, "请输入密码": 10, "指纹验证": 10, "面容验证": 10,
        "短信验证": 6, "确认支付": 6, "立即支付": 4,
    },
}

_STRONG = 10
_MEDIUM = 4
_WEAK = 1


def _classify(text: str) -> List[dict]:
    """对一段文字做特征匹配，返回命中项。"""
    if not text:
        return []
    low = text.lower()
    hits = []
    for cat, words in PATTERNS.items():
        for word, weight in words.items():
            if word.lower() in low:
                hits.append({"text": text.strip()[:80], "category": cat,
                             "keyword": word, "weight": weight})
    return hits


def _level(score: int) -> str:
    if score >= _STRONG:
        return "high"
    if score >= _MEDIUM:
        return "medium"
    if score >= _WEAK:
        return "low"
    return "none"


# --------------------------------------------------------------- 三层检测
def check_titles(extra_keywords: Optional[List[str]] = None) -> List[dict]:
    """检查所有可见窗口的标题。"""
    hits = []
    for win in uia.list_windows(visible_only=True, titled_only=True):
        t = win["title"]
        if not t:
            continue
        found = _classify(t)
        for f in found:
            f["source"] = "window-title"
            f["window"] = t[:60]
            f["hwnd"] = win["hwnd"]
            hits.append(f)
        for kw in (extra_keywords or []):
            if kw and kw in t:
                hits.append({"text": t[:80], "category": "custom", "keyword": kw,
                             "weight": 10, "source": "window-title",
                             "window": t[:60], "hwnd": win["hwnd"]})
    return hits


def check_uia(hwnd: Optional[int] = None, include_password_field: bool = True) -> List[dict]:
    """用 UIA 读取控件名称，并顺带发现密码输入框 / 验证码输入框。"""
    hits: List[dict] = []
    target = hwnd or uia.foreground_window()
    if not target:
        return hits

    items = uia.scan_window(target, include_text=True, interactive_only=False)
    for it in items:
        name = it.get("name") or ""
        for f in _classify(name):
            f["source"] = "uia"
            f["rect"] = it.get("rect")
            hits.append(f)
        # 输入框语义判断
        role = it.get("role")
        if role == "input":
            nm = name or ""
            if any(k in nm for k in ("密码", "password", "口令")):
                hits.append({"text": nm or "(密码输入框)", "category": "login",
                             "keyword": "密码输入框", "weight": 8,
                             "source": "uia", "rect": it.get("rect")})
            elif any(k in nm for k in ("验证码", "captcha", "验证")):
                hits.append({"text": nm or "(验证码输入框)", "category": "captcha",
                             "keyword": "验证码输入框", "weight": 10,
                             "source": "uia", "rect": it.get("rect")})

    if include_password_field:
        try:
            import uiautomation as auto
            root = auto.ControlFromHandle(target)
            if root is not None:
                stack = [root]
                seen = 0
                while stack and seen < 400:
                    c = stack.pop()
                    seen += 1
                    try:
                        if c.ControlTypeName == "EditControl" and c.IsPassword:
                            r = c.BoundingRectangle
                            hits.append({
                                "text": "(检测到密码输入框)", "category": "login",
                                "keyword": "IsPassword", "weight": 8,
                                "source": "uia-password",
                                "rect": [r.left, r.top, r.right, r.bottom] if r else None})
                    except Exception:
                        pass
                    try:
                        stack.extend(c.GetChildren())
                    except Exception:
                        pass
        except Exception:
            pass
    return hits


def check_ocr(region=None, min_score: float = 0.6,
              extra_keywords: Optional[List[str]] = None) -> List[dict]:
    """OCR 识别屏幕文字后做特征匹配（最慢，但能覆盖自绘界面）。"""
    from . import ocr as _ocr
    if not _ocr.available():
        return []
    hits: List[dict] = []
    blocks = _ocr.recognize(region=region, min_score=min_score, max_results=300)
    for b in blocks:
        if b.get("error"):
            continue
        txt = b.get("text") or ""
        for f in _classify(txt):
            f["source"] = "ocr"
            f["rect"] = b.get("rect")
            f["score"] = b.get("score")
            hits.append(f)
        for kw in (extra_keywords or []):
            if kw and kw in txt:
                hits.append({"text": txt[:80], "category": "custom", "keyword": kw,
                             "weight": 10, "source": "ocr", "rect": b.get("rect"),
                             "score": b.get("score")})
    return hits


# --------------------------------------------------------------- 汇总
def check(mode: str = "auto", region=None, hwnd: Optional[int] = None,
          extra_keywords: Optional[List[str]] = None,
          foreground_regions: bool = True) -> dict:
    """判断当前是否需要人工介入。

    mode:
      fast —— 只查窗口标题 + UIA（约 1s）
      deep —— 追加 OCR（数秒，取决于区域大小）
      auto —— fast 无结论时才跑 deep（默认）
    """
    t0 = time.time()
    mode = (mode or "auto").lower()
    hits: List[dict] = []
    layers: List[str] = []

    title_hits = check_titles(extra_keywords)
    hits.extend(title_hits)
    layers.append("window-title")

    uia_hits = check_uia(hwnd=hwnd)
    hits.extend(uia_hits)
    layers.append("uia")

    score = sum(h["weight"] for h in hits)
    need_more = mode == "deep" or (mode == "auto" and score < _MEDIUM)

    if need_more:
        # OCR 区域：默认只扫前台窗口，避免全屏数十秒
        ocr_region = region
        if ocr_region is None and foreground_regions:
            from . import capture
            fg = hwnd or uia.foreground_window()
            ocr_region = capture.window_region(fg, pad=0) if fg else None
        ocr_hits = check_ocr(region=ocr_region, extra_keywords=extra_keywords)
        hits.extend(ocr_hits)
        layers.append("ocr")

    score = sum(h["weight"] for h in hits)
    level = _level(score)

    # 去重（同一关键词在多层命中时只保留权重最高的一条）
    dedup: Dict[str, dict] = {}
    for h in hits:
        key = f"{h['category']}/{h['keyword']}"
        if key not in dedup or h["weight"] > dedup[key]["weight"]:
            dedup[key] = h
    merged = sorted(dedup.values(), key=lambda x: -x["weight"])

    need = level in ("high", "medium")
    advice = ""
    if need:
        top = [h["keyword"] for h in merged[:3]]
        advice = (f"检测到可能需人工完成的环节（{', '.join(top)}）。"
                  f"请暂停自动化：先让用户手动完成验证/登录，确认页面恢复正常后再继续。"
                  f"继续前可运行 `guard` 复查，或用 `wait-human` 等待其消失。")
    elif level == "low":
        advice = "仅命中弱特征（如页面存在“登录/账号”字样），一般无需人工介入，可继续但请留意。"

    return {
        "human_required": need,
        "level": level,
        "score": score,
        "categories": sorted({h["category"] for h in merged}),
        "layers": layers,
        "evidence": merged[:12],
        "advice": advice,
        "elapsed_ms": round((time.time() - t0) * 1000),
    }


def wait_until_clear(timeout: float = 300.0, poll: float = 5.0,
                     mode: str = "fast", region=None,
                     extra_keywords: Optional[List[str]] = None,
                     require_below: str = "medium") -> dict:
    """轮询直到不再需要人工介入（即用户已完成验证），或超时。

    轮询默认用 fast 模式以降低开销；如需更严格可用 deep。
    """
    t0 = time.time()
    rounds = 0
    last = None
    threshold = {"none": 0, "low": 1, "medium": 4, "high": 10}[require_below]
    while True:
        rounds += 1
        last = check(mode=mode, region=region, extra_keywords=extra_keywords)
        if last["score"] < threshold:
            return {"cleared": True, "rounds": rounds,
                    "elapsed_s": round(time.time() - t0, 1), "last": last}
        if time.time() - t0 >= timeout:
            return {"cleared": False, "rounds": rounds,
                    "elapsed_s": round(time.time() - t0, 1),
                    "reason": f"等待 {timeout:.0f}s 后仍未检测到人工环节消失",
                    "last": last}
        time.sleep(poll)
