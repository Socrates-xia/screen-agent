"""安全护栏：持久化审计日志、不可逆操作闸门、锁屏检测。

这三项都是**机制**而不是建议 —— 因为它们要对抗的是"模型可能忽略指示"
这件事本身。把安全寄托在提示词上，恰恰是自动化里最不可靠的一环。

1. 审计日志 `audit.log`（%TEMP%\\screen-agent\\audit.jsonl，append-only）
   每个动作用户鼠标/键盘的动作都留痕：时间、命令、解析后的目标、
   校验结论、是否被拦截。它让"这操作是不是 Agent 干的"事后可查。

2. 危险操作闸门
   目标元素名命中危险词表（删除/支付/提交/清空…）时，默认**拒绝执行**，
   必须显式加 --yes。这是不可逆界面上最有效的机械式保险。

3. 锁屏检测
   锁屏 / 安全桌面下截图是黑屏，此时点击与按键毫无意义且可能错投。
   通过 OpenInputDesktop 检测，锁屏时直接拒绝。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from . import capture

AUDIT_FILE = "audit.jsonl"
_MAX_AUDIT_BYTES = 8 * 1024 * 1024      # 超过 8MB 轮转，避免无限增长


# --------------------------------------------------------------- 审计日志
def audit_path() -> str:
    return os.path.join(capture.runtime_dir(), AUDIT_FILE)


def audit(action: str, **fields: Any) -> None:
    """追加一条审计记录。写日志失败绝不能影响主流程。"""
    try:
        row = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "action": action}
        row.update({k: v for k, v in fields.items() if v is not None})
        path = audit_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.exists(path) and os.path.getsize(path) > _MAX_AUDIT_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def tail(n: int = 20) -> List[dict]:
    """读取最近 n 条审计记录（新的在后）。"""
    path = audit_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    rows = []
    for ln in lines[-max(1, n):]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    return rows


def audit_text(n: int = 20) -> str:
    rows = tail(n)
    if not rows:
        return "（暂无审计记录）"
    lines = [f"最近 {len(rows)} 条操作记录（{audit_path()}）", "-" * 72]
    for r in rows:
        tgt = r.get("element_name") or r.get("target_text") or ""
        role = r.get("element_role") or ""
        flag = " [已拦截]" if r.get("blocked") else ""
        lines.append(f"{r.get('iso', '')}  {r.get('action', '?'):<16} "
                     f"{(role + '/' + str(tgt)) if tgt else ''}{flag}")
    return "\n".join(lines)


# --------------------------------------------------------- 危险操作闸门
# 命中即默认拦截。宁可误报（用户加 --yes 即可）也不能漏掉一次误删/误支付。
# 匹配的是"控件名/窗口标题"，所以对中文界面同样有效。
DANGEROUS_PATTERNS = [
    # 删除 / 移除
    "删除", "移除", "清空", "抹掉", "移除全部", "delete", "remove", "destroy",
    "erase", "trash", "discard", "wipe",
    # 提交 / 生效（不可撤销地改变服务端状态）
    "提交", "确认提交", "确定提交", "send", "submit", "publish", "post ",
    "commit", "confirm",
    # 支付 / 金额
    "支付", "付款", "结算", "确认支付", "立即支付", "下单", "转账", "充值",
    "pay", "payment", "checkout", "purchase", "buy", "order", "transfer",
    # 覆盖 / 重置（不可逆）
    "覆盖", "重置", "恢复出厂", "格式化", "清除所有", "overwrite", "reset",
    "restore", "format", "factory",
    # 关闭 / 终止
    "结束进程", "强制结束", "终止", "强制关闭", "terminate", "kill", "force quit",
    # 权限 / 安全
    "授权", "允许访问", "授予管理员", "trust", "allow", "grant",
]
# 这些词出现得太频繁，单独命中不作为高危信号（避免"确认"二字满屏都是）
_WEAK = {"确定", "确认", "取消", "返回", "是", "no", "ok", "yes", "back", "cancel"}


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _matches(pat: str, s: str) -> bool:
    """中文按子串匹配；ASCII 词按**词边界**匹配。

    词边界是必须的：否则 'kill' 会命中 "Skill"、'send' 会命中 "Send to"、
    'remove' 会命中 "Remote" —— 这类误报会让闸门迅速被用户用 --yes 绕过，
    反而失去意义。
    """
    if not pat.isascii():
        return pat in s
    import re
    return re.search(r"(?<![a-z0-9])" + re.escape(pat) + r"(?![a-z0-9])", s) is not None


def dangerous_hits(*texts: Any) -> List[dict]:
    """返回命中的危险词列表；texts 为空则不判定（无文本可依据时不拦）。"""
    hits: List[dict] = []
    seen = set()
    for t in texts:
        s = _norm(t)
        if not s:
            continue
        for pat in DANGEROUS_PATTERNS:
            p = _norm(pat)
            if p in _WEAK:
                continue
            if _matches(p, s) and (pat, t) not in seen:
                seen.add((pat, t))
                hits.append({"pattern": pat, "source": str(t)[:80]})
    return hits


def check_dangerous(*texts: Any) -> Optional[dict]:
    """返回拦截信息 dict（需确认），未命中返回 None。"""
    hits = dangerous_hits(*texts)
    if not hits:
        return None
    return {
        "level": "high",
        "reason": "目标疑似不可逆操作：" +
                  "、".join(f"{h['pattern']}(来自{h['source']})" for h in hits[:5]),
        "hits": hits,
        "advice": "确认无误后加 --yes 重新执行；或改用 --dry-run 先看落点",
    }


# ------------------------------------------------------------- 锁屏检测
def is_locked() -> Optional[bool]:
    """检测工作站是否处于锁屏/断开状态。

    返回 True=已锁定，False=正常，None=无法判断（调用方应放行而不是误拦）。
    """
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        # OpenInputDesktop: 失败且 ERROR_ACCESS_DENIED(5) 通常意味着已锁屏
        DESKTOP_SWITCHDESKTOP = 0x0100
        h = u32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not h:
            err = ctypes.get_last_error()
            return True if err in (5, 0x80040110) else None
        u32.CloseDesktop(h)
        return False
    except Exception:
        return None


def lock_warning() -> str:
    st = is_locked()
    if st:
        return ("检测到工作站处于锁屏/安全桌面 —— 此时截图为黑屏，"
                "点击与按键都不会作用于你的桌面。请先解锁后再操作。")
    return ""


__all__ = ["AUDIT_FILE", "audit_path", "audit", "tail", "audit_text",
           "DANGEROUS_PATTERNS", "dangerous_hits", "check_dangerous",
           "is_locked", "lock_warning"]
