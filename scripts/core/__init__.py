"""screen-agent 核心包。

★ 导入本包会立即完成 DPI 感知设置（PMv2），此后第三方库（mss / uiautomation）
  才能在统一的物理像素坐标系下工作。请勿绕开本包直接 import 子模块。
"""
from __future__ import annotations

from . import dpi as _dpi

DPI_MODE = _dpi.setup()

from . import winapi  # noqa: E402,F401
from . import geom  # noqa: E402,F401
from . import capture  # noqa: E402,F401
from . import mouse  # noqa: E402,F401
from . import keyboard  # noqa: E402,F401
from . import session  # noqa: E402,F401
from . import annotate  # noqa: E402,F401
from . import uia  # noqa: E402,F401
from . import ocr  # noqa: E402,F401
from . import human  # noqa: E402,F401
from . import browser  # noqa: E402,F401

__all__ = ["DPI_MODE", "winapi", "geom", "capture", "mouse", "keyboard",
           "session", "annotate", "uia", "ocr", "human", "browser"]
