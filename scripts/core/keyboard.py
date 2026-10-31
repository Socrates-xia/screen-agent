"""键盘输入：打字、单键、组合键。

★ 中文/Unicode 的关键点
  虚拟键码（VK）只能表达 ASCII 范围内的按键，无法表示「中」「输」这类字符。
  因此文本输入有两条通道：
    1. unicode  —— SendInput + KEYEVENTF_UNICODE，直接投递字符码点，绕过输入法。
                   大多数应用（记事本、浏览器、Electron）可用，速度快。
    2. clipboard—— 写系统剪贴板 + Ctrl+V。兼容性最好，能应付对 UNICODE 事件
                   不友好的应用（部分游戏、老式控件、远程桌面）。
  默认 auto：先走 unicode；若启用读回校验且内容没落进去，
             自动降级重试一次剪贴板通道（真正的双通道兜底）。
"""
from __future__ import annotations

import ctypes
import time
from typing import Iterable, List, Optional, Sequence, Tuple

from . import winapi as w

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
_last_action: Optional[dict] = None


def last_action() -> Optional[dict]:
    return _last_action


def _record(**kw):
    global _last_action
    kw["ts"] = time.time()
    _last_action = kw


# ------------------------------------------------------------------ 底层
# 当前进程内"已按下但尚未抬起"的键。用于保证任何异常路径下都不会把修饰键
# 遗留在按下状态 —— 一次 Ctrl 卡住会让用户之后敲的每个字都变成快捷键。
_held_keys: set = set()
_MODIFIERS = ("ctrl", "shift", "alt", "win")


def release_all() -> List[int]:
    """抬起所有本进程按下的键（应急兜底）。返回实际尝试抬起的 vk 列表。

    正常路径不该需要它；它是 SendInput 失败 / 目标窗口中途消失时的保险。
    """
    released = []
    for vk in list(_held_keys):
        try:
            _key_up(vk)
            released.append(vk)
        except Exception:
            pass
        _held_keys.discard(vk)
    if released:
        _record(action="release_all", released=released)
    return released


def _send_ki(vk: int = 0, scan: int = 0, flags: int = 0) -> None:
    inp = w.INPUT(type=w.INPUT_KEYBOARD)
    inp.u.ki = w.KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)
    ctypes.set_last_error(0)
    n = w.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(w.INPUT))
    if n != 1:
        raise OSError(f"SendInput(键盘) 失败 vk={vk:#x} scan={scan:#x} "
                      f"flags={flags:#x} err={ctypes.get_last_error()}")


def _key_down(vk: int) -> None:
    flags = w.KEYEVENTF_EXTENDEDKEY if vk in w.EXTENDED_VKS else 0
    _send_ki(vk=vk, flags=flags)
    _held_keys.add(vk)


def _key_up(vk: int) -> None:
    flags = w.KEYEVENTF_KEYUP | (w.KEYEVENTF_EXTENDEDKEY if vk in w.EXTENDED_VKS else 0)
    _held_keys.discard(vk)          # 先摘除，保证 release_all 不会重复抬起
    _send_ki(vk=vk, flags=flags)


def resolve_key(name: str) -> int:
    """把键名解析成虚拟键码。支持 'enter' / 'f5' / 'a' / 'ctrl' 等。"""
    if not name:
        raise ValueError("键名为空")
    n = name.strip().lower()
    if n in w.VK:
        return w.VK[n]
    if len(n) == 1:
        # 交给系统解析（能正确处理符号键，如 , . / ; ' [ ] \ - = ` 等）
        vk_scan = w.user32.VkKeyScanW(ctypes.c_wchar(n))
        if vk_scan != -1:
            return vk_scan & 0xFF
    raise ValueError(f"无法识别的键名: {name!r}")


def _unicode_units(ch: str) -> List[int]:
    """把字符转成 UTF-16 码元序列（BMP 外字符会拆成代理对）。"""
    code = ord(ch)
    if code <= 0xFFFF:
        return [code]
    code -= 0x10000
    return [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]


def _type_unicode(text: str, interval: float = 0.004) -> None:
    for ch in text:
        for unit in _unicode_units(ch):
            _send_ki(scan=unit, flags=w.KEYEVENTF_UNICODE)
            _send_ki(scan=unit, flags=w.KEYEVENTF_UNICODE | w.KEYEVENTF_KEYUP)
        if interval:
            time.sleep(interval)


# ------------------------------------------------------------------ 剪贴板
def get_clipboard() -> str:
    user32, kernel32 = w.user32, w.kernel32
    if not user32.OpenClipboard(None):
        return ""
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard(text: str) -> bool:
    user32, kernel32 = w.user32, w.kernel32
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        size = (len(text) + 1) * 2
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        if not p:
            return False
        try:
            ctypes.memmove(p, text.encode("utf-16-le") + b"\x00\x00", size)
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            return False
        return True
    finally:
        user32.CloseClipboard()


# ------------------------------------------------------------------ 公开 API
def press(key: str, count: int = 1, interval: float = 0.04,
          duration: float = 0.02) -> dict:
    """按一次（或多次）单个键。key 可为 'enter' / 'f5' / 'a' / 'tab' 等。"""
    vk = resolve_key(key)
    try:
        for i in range(max(1, count)):
            _key_down(vk)
            try:
                time.sleep(duration)
            finally:
                _key_up(vk)
            if i < count - 1:
                time.sleep(interval)
    except Exception:
        # 绝不能把键遗留在按下状态
        _key_up(vk)
        raise
    res = {"action": "press", "key": key, "vk": vk, "count": max(1, count)}
    _record(**res)
    return res


def hotkey(*keys: str, gap: float = 0.02) -> dict:
    """组合键，如 hotkey('ctrl', 'shift', 's')。按顺序按下，逆序释放。"""
    flat: List[str] = []
    for k in keys:
        for part in str(k).split("+"):
            part = part.strip()
            if part:
                flat.append(part)
    if not flat:
        raise ValueError("组合键为空")
    vks = [resolve_key(k) for k in flat]
    pressed: List[int] = []
    try:
        for vk in vks:
            _key_down(vk)
            pressed.append(vk)
            time.sleep(gap)
    finally:
        # 无论是否抛异常，逆序全部抬起；抬不动的也交给 release_all 兜底
        for vk in reversed(pressed):
            try:
                _key_up(vk)
            except Exception:
                _held_keys.add(vk)
        release_all()
    res = {"action": "hotkey", "keys": flat}
    _record(**res)
    return res


def _paste_via_clipboard(text: str, restore_clipboard: bool = True,
                         old_clip: Optional[str] = None) -> Optional[str]:
    """走剪贴板通道粘贴文本。返回粘贴前保存的剪贴板内容（供恢复）。"""
    if old_clip is None and restore_clipboard:
        try:
            old_clip = get_clipboard()
        except Exception:
            old_clip = None
    if not set_clipboard(text):
        raise OSError("写入剪贴板失败")
    time.sleep(0.06)
    hotkey("ctrl", "v")
    time.sleep(max(0.06, len(text) * 0.006))
    return old_clip


def type_text(text: str, method: str = "auto", interval: float = 0.004,
              restore_clipboard: bool = True, press_enter: bool = False,
              verify_fn=None) -> dict:
    """输入文本。

    method:
      unicode   —— 直接投递字符码点（默认，快，绕过输入法）
      clipboard —— 写剪贴板 + Ctrl+V（兼容性最好）
      auto      —— 先 unicode，失败则降级重试 clipboard（真正的兜底）

    verify_fn: 可选的零参可调用对象，返回目标控件**当前**的文本内容。
      传入它即可把"双通道兜底"从口头承诺变成机制：
      unicode 通道最危险的失败模式是 SendInput 成功但应用根本不认 UNICODE
      事件（内容静默丢失），只看 SendInput 的返回值是发现不了的。
      校验不通过时自动降级到剪贴板重试一次。
    """
    if text is None:
        text = ""
    method = (method or "auto").lower()
    if method not in ("auto", "unicode", "clipboard"):
        raise ValueError(f"不支持的输入方式: {method!r}，可选 auto/unicode/clipboard")

    used = None
    old_clip = None
    verified = None

    def _readback_ok() -> bool:
        """校验内容是否真的落到控件里。校验器自身出错时按"通过"处理。"""
        nonlocal verified
        if verify_fn is None:
            return True
        try:
            got = verify_fn()
        except Exception:
            return True
        verified = got
        return isinstance(got, str) and text in got

    def _run_unicode() -> bool:
        try:
            _type_unicode(text, interval=interval)
        except OSError:
            return False          # 明确的通道失败 → 降级
        return _readback_ok()     # 静默失败 → 靠读回发现 → 降级

    if method == "clipboard":
        used = "clipboard"
        old_clip = _paste_via_clipboard(text, restore_clipboard, None)
    elif method == "unicode":
        used = "unicode"
        if not _run_unicode() and verify_fn is not None:
            # 只有开了校验才降级；没校验时保持旧行为（不重复输入，避免双份）
            used = "unicode(unverified)"
    else:  # auto
        if _run_unicode():
            used = "unicode"
        else:
            used = "clipboard(fallback)"
            old_clip = _paste_via_clipboard(text, restore_clipboard, None)

    if press_enter:
        time.sleep(0.05)
        press("enter")

    if old_clip is not None and restore_clipboard:
        try:
            time.sleep(0.15)
            set_clipboard(old_clip)
        except Exception:
            pass

    res = {"action": "type", "length": len(text), "method": used,
           "preview": text[:40] + ("…" if len(text) > 40 else ""),
           "enter": bool(press_enter)}
    if verified is not None:
        res["verified"] = (text in verified)
    _record(**res)
    return res


def clear_field(select_all: bool = True) -> dict:
    """清空当前输入框：全选后删除。"""
    if select_all:
        hotkey("ctrl", "a")
        time.sleep(0.04)
    press("delete")
    res = {"action": "clear_field"}
    _record(**res)
    return res


def type_into(text: str, element: dict, click_fn, method: str = "auto",
              clear: bool = False, press_enter: bool = False) -> dict:
    """先点击目标元素取得焦点，再输入文本。

    click_fn 由调用方注入（避免 keyboard 依赖 mouse，形成循环导入）。
    """
    if element:
        cx, cy = element["center"]
        click_fn(cx, cy)
        time.sleep(0.12)
    if clear:
        clear_field()
    return type_text(text, method=method, press_enter=press_enter)


def send_keys_sequence(seq: Sequence[str], gap: float = 0.06) -> dict:
    """依次发送多个按键/组合键。seq 元素可含 '+' 表示组合。"""
    for item in seq:
        if "+" in item:
            hotkey(item)
        else:
            press(item)
        time.sleep(gap)
    res = {"action": "send_sequence", "sequence": list(seq)}
    _record(**res)
    return res
