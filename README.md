# screen-agent · 让 AI 真正会用鼠标键盘

> 一个给 AI Agent 用的「眼睛 + 手」：读得懂屏幕上的控件，点得中、打得进、拖得动。

现有的大多数 GUI 自动化方案都在做同一件事——**截个图，然后猜哪里像个按钮**。
猜错的代价很大：点偏了轻则白做，重则点到旁边的「删除」。

这个项目走另一条路：**直接向 Windows 索取控件树**（UI Automation），
拿到按钮的**确切矩形、类型、名称、是否禁用、是不是密码框**，再动手。
截图 + OCR 只在控件树拿不到的时候兜底。

---

## 它解决过什么问题

开发过程中踩到的几个坑，代码里都留了处理，也都写进了 `references/design-notes.md`：

| 坑 | 表现 | 处理 |
|---|---|---|
| **`MOUSEEVENTF_ABSOLUTE` 只认主显示器** | 双屏 2560+2560 下，X 坐标**精确减半**（目标 300 → 实际 150），Y 却完全正常 | 改用 `SetCursorPos` 直接给物理像素，实测跨屏零偏差 |
| **WinUI/XAML 应用的 UIA 坐标是错的** | Win11 记事本的标签页返回 `(-5616,791)` 这种屏幕外负坐标 | 每个元素过一遍「是否落在桌面内」校验，不可信就拒点 |
| **Chromium 系的控件树是异步建的** | 首次请求 Electron/Chrome 的 UIA 返回空树 | 内置重试，实测 WorkBuddy（Electron）能拿到 40 个元素 |
| **拖拽起点落在子控件上会失效** | 从按钮上起拖，父窗口收不到 `WM_LBUTTONDOWN` | 起点必须落在空白区；且 `GetWindowRect` 是**外框**，客户区要用 `GetClientRect` 算 |
| **`SetForegroundWindow` 会被静默拒绝** | 调用成功返回，窗口却不到前台，只闪一下任务栏 | `AttachThreadInput` 抢前台；失败就**拒绝执行**，绝不把 `Ctrl+W` 发到错误窗口 |
| **浏览器"当前标签"不能靠标题猜** | 多标签时标题会变成「某标题 和另外 N 个页面」，全部匹配失败 | 改用 UIA 的 `SelectionItemPattern.IsSelected` |

---

## 核心能力

### 看
- 截屏（物理像素，与 UIA 坐标严格同一坐标系）
- **UIA 控件树扫描**：按钮 / 输入框 / 链接 / 菜单项 / 标签页…
- 生成**带编号的标注图**——一眼就能看出识别对不对
- OCR 视觉兜底（RapidOCR + PP-OCRv6 中文模型，离线）

### 动
- 鼠标：左/右键点击、双击、悬停、滚轮、**拖拽**、**滑动**
- 键盘：**中文/Unicode 直发 + 剪贴板双通道**、单键、组合键、按键序列
- 浏览器：Edge/Chrome 的标签管理、导航、地址栏、页内查找、页面元素扫描

### 保命
- **陈旧坐标防护**：点击前用 `ControlFromPoint` 复核该点下方还是不是它（仅 **5ms**），
  能识别 `match` / `occluded`（被遮挡）/ `mismatch`（界面已变）并拒绝危险点击
- **不可逆操作闸门**：目标名命中「删除/支付/提交/清空」等词时默认拦截，需 `--yes` 放行
- **锁屏检测**：锁屏/安全桌面下直接拒绝输入
- **审计日志**：每个动作留痕（时间 / 命令 / 目标 / 校验结论 / 是否拦截）
- **人机验证识别**：遇到验证码/滑块/登录会**主动停下来交还给人**，不尝试自动破解

---

## 安装

需要 **Windows 10/11** + **Python 3.10+**（开发环境为 3.13）。

```bash
pip install mss uiautomation comtypes pillow
pip install rapidocr onnxruntime          # 可选：OCR 视觉兜底
```

把整个目录放到 WorkBuddy 的技能目录（或任意位置直接调 `scripts/sa.py`）：

```
~/.workbuddy/skills/screen-agent/
```

---

## 快速开始

```bash
# 定义入口（路径按你的实际位置改，整体加引号）
SA_PY='python'
SA_SKILL='/path/to/screen-agent/scripts/sa.py'

python "$SA_SKILL" info                     # 环境自检：DPI、显示器、光标、引擎
python "$SA_SKILL" windows                  # 看有哪些窗口
python "$SA_SKILL" scan --window "记事本" --activate
python "$SA_SKILL" show --clickable          # 看识别结果（含标注图路径）
python "$SA_SKILL" click --id E3             # 按编号点击
python "$SA_SKILL" type "你好 world" --id E5  # 输入中文
python "$SA_SKILL" guard                     # 检测是否遇到验证码/登录
```

**每次 `scan` 后先看一眼标注图**，确认框的位置对了再动。

---

## 命令一览

| 类别 | 命令 |
|---|---|
| 观察 | `info` / `windows` / `scan` / `show` / `find` / `shot` / `tree` / `ocr` |
| 鼠标 | `click` / `move` / `scroll` / `drag` / `swipe` |
| 键盘 | `type` / `key` |
| 浏览器 | `browser status\|tabs\|open\|new-tab\|back\|forward\|refresh\|close-tab\|switch\|find\|scan` |
| 安全 | `guard` / `wait-human` / `audit` |
| 自检 | `selftest` |

完整说明见 [`SKILL.md`](SKILL.md)，技术设计与实测数据见 [`references/design-notes.md`](references/design-notes.md)。

---

## 自检

不依赖任何外部测试框架，自带可重复执行的端到端验证（会临时创建一个测试窗口，
**不碰你的任何真实数据**）：

```bash
python -B scripts/tools/selftest.py
```

覆盖：DPI 一致性、截图像素与 GDI 对齐、UIA 坐标与 Win32 对齐、
真实左/右键点击、遮挡检测、键盘双通道读回校验、拖拽事件计数、
验证码识别正反例、浏览器模块…

---

## 适用环境与限制

**已验证**：Windows 11 · 双显示器**混合 DPI**（2560×1600@150% + 2560×1600@100%）
· 虚拟桌面 5120×1600。

**明确的边界**（不是 bug）：

- **权限隔离（UIPI）**：普通权限进程点不动以管理员运行的窗口（任务管理器、UAC 弹窗）
- **UIA 盲区**：游戏（DirectX 全屏）、远程桌面画面、Canvas 自绘、**Qt 应用**
  （实测微信 PC 版 UIA 返回 0 元素）→ 只能走 OCR，纯图标按钮仍定位不到
- **浏览器 UIA 轨**：能点元素、读文字，但**不能执行 JS / 读 DOM / 等网络空闲**
  （需要这些得改走 CDP）
- **反作弊**：部分游戏、银行客户端会拦截模拟输入
- **锁屏**：锁屏下截图为黑屏
- **OCR 速度**：全屏数十秒，建议用 `--region` 限定区域

---

## 项目结构

```
screen-agent/
├── SKILL.md                 使用说明（面向 Agent）
├── references/
│   └── design-notes.md      技术决策与实测数据（改动前必读）
└── scripts/
    ├── sa.py                统一 CLI 入口
    ├── core/
    │   ├── dpi.py           DPI 感知（必须最先执行）
    │   ├── winapi.py        Win32 声明（集中管理 argtypes）
    │   ├── capture.py       截图
    │   ├── uia.py           UIA 控件树
    │   ├── ocr.py           OCR 兜底
    │   ├── mouse.py         鼠标（含拖拽）
    │   ├── keyboard.py      键盘（双通道）
    │   ├── browser.py       浏览器
    │   ├── human.py         人机验证识别
    │   ├── safety.py        审计 / 闸门 / 锁屏
    │   ├── session.py       元素会话与坐标校验
    │   └── annotate.py      标注图
    └── tools/
        └── selftest.py      端到端自检
```

---

## 一点说明

这个项目的定位是**能操作真实电脑**，所以误操作会有真实后果。
设计上把所有"不可逆"的环节都做成了需要显式确认的闸门——
不是不信任使用者，而是**自动化最不可靠的一环永远是"模型会不会忽略指示"**。
把安全寄托在提示词上不如做成机制。
