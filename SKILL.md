---
name: screen-agent
display_name: 屏幕观察与鼠标键盘操作
display_name_en: Screen Agent
category: automation
version: 2.1.0
author: 夏世豪
agent_created: true
description: "让 Agent 真正「看见」电脑屏幕并动手操作。用 UI Automation 读取控件树精确定位按钮/输入框/链接（比纯截图识别更准更可解释），再用 SetCursorPos + SendInput 模拟鼠标点击、拖拽、滑动，以及键盘打字、组合键。支持多显示器混合 DPI、Edge 浏览器操作（标签页/地址栏/导航，复用已登录会话）、截图标注可视化、元素编号寻址与「陈旧坐标防护」。内置三重安全机制：不可逆操作（删除/支付/提交）默认拦截需 --yes 确认、锁屏时拒绝输入、全程写审计日志可查。遇到验证码/滑块/登录等需人工介入的环节会主动识别并停下来请用户接管，UIA 拿不到控件树时用 OCR 视觉兜底。触发词：看屏幕、截图识别、点击屏幕、模拟鼠标、自动点击、打字输入、拖拽、滑动、操作电脑、点按钮、操作浏览器、打开网页、Edge、桌面自动化、screen automation、click on screen、GUI 操作。"
description_zh: "让 Agent 观察屏幕并操作：UIA 定位可交互元素（按钮/输入框/链接），模拟鼠标左键/右键点击、双击、拖拽、滑动、滚轮，以及键盘打字（含中文）与组合键；支持 Edge 浏览器操作、多显示器混合 DPI、标注图可视化、元素编号寻址与点击前坐标复核；内置不可逆操作闸门（删除/支付/提交需 --yes）、锁屏拒绝输入、审计日志（audit 命令可查）；遇到验证码/登录等人工环节会自动识别并请用户接管。"
description_en: "Empowers the agent to observe the screen and drive mouse plus keyboard. Uses UI Automation to locate interactive elements precisely, then performs left/right clicks, double-clicks, drag, swipe, scroll, and text typing (including CJK) via SendInput. Supports Edge browser control reusing the signed-in session, multi-monitor mixed-DPI setups, annotated screenshots, element-ID addressing, and pre-click position re-validation. Automatically detects CAPTCHA / login / slider challenges and hands control back to the user instead of clicking blindly."
allowed-tools: Read, Bash, Glob, Grep
---

# 屏幕观察与鼠标键盘操作（screen-agent）

让 Agent 能"看见"屏幕、"认得出"控件，并用**真实的鼠标键盘事件**操作它们。

---

## 一、核心方案（为什么这样做）

| 环节 | 方案 | 为什么不用更简单的做法 |
|---|---|---|
| **看** | `mss` 抓物理像素截图 | — |
| **认** | **Windows UI Automation 控件树**：直接向应用索取控件类型/名称/边界框 | 纯视觉识别只能"猜"哪里像按钮；UIA 能拿到确切矩形和语义（这是按钮还是输入框、是否禁用、是不是密码框） |
| **点** | `SetCursorPos` 移动 + `SendInput` 发左右键 | **不能用 `MOUSEEVENTF_ABSOLUTE`**：它的归一化空间是主显示器而非虚拟桌面，本机双屏 2560+2560 会让 X 坐标**精确减半**（实测目标 300 → 实际 150），极隐蔽 |
| **拖** | 移动拆成多步 + 按下/松开各留保持时间 | 只发一次瞬时位移，应用收到的是"瞬移"，识别不出拖拽 |
| **打** | Unicode 直发 + 剪贴板双通道 | 虚拟键码无法表达中文；两条通道互为兜底 |
| **浏览器** | UIA + 快捷键 | 复用你**已登录**的 Edge；Playwright 起的新实例没有登录态，CDP 又要求先关掉现有浏览器 |

**坐标系铁律**：全程只用物理像素。进程启动即声明 Per-Monitor V2 DPI 感知，
因此 `UIA 坐标` ＝ `Win32 坐标` ＝ `截图像素` ＝ `点击坐标`，四者严格对齐（实测偏差 0px）。

---

## 二、快速开始

先定义两个变量（路径按你的实际安装位置调整，**不要把带空格/中文的路径裸露在命令行里**）：

```bash
# Python 解释器与技能入口。路径请整体加引号。
SA_PY='<你的 python 路径>/python.exe'          # 例如 .../binaries/python/envs/default/Scripts/python.exe
SA_SKILL='<skill 目录>/scripts/sa.py'          # 即本 SKILL.md 所在目录下的 scripts/sa.py
SA=("$SA_PY" "$SA_SKILL")                      # 用数组避免分词问题（推荐 bash）
```

在 PowerShell 中用：

```powershell
$SA_PY = '<你的 python 路径>\python.exe'
$SA    = @($SA_PY, '<skill 目录>\scripts\sa.py')
& $SA[0] $SA[1] info
```

```bash
$SA[0] $SA[1] info                              # 环境自检：DPI、显示器、光标、引擎状态
$SA[0] $SA[1] windows                           # 看有哪些窗口
$SA[0] $SA[1] scan --window "记事本" --activate  # 扫描元素 → 生成带编号的标注图
$SA[0] $SA[1] click --id E3 --dry-run           # 先演练（不会真的点击，光标会自动回到原位）
$SA[0] $SA[1] click --id E3                     # 按编号点击（推荐）
$SA[0] $SA[1] type "你好 world" --id E5         # 输入文本（支持中文）
$SA[0] $SA[1] guard                             # 检测是否遇到验证码/登录
$SA[0] $SA[1] audit --n 20                      # 查看最近操作记录
```

> 下文命令表里的 `$SA xxx` 是简写，实际请写成 `$SA[0] $SA[1] xxx`（或用你环境里的 `sa` 包装脚本）。

**强烈建议**：每次 `scan` 后先看一眼输出的标注图，确认框的位置符合预期再操作。

---

## 三、命令参考

### 观察

| 命令 | 说明 |
|---|---|
| `info` | DPI 模式、显示器列表、光标位置、OCR 引擎状态 |
| `windows [--all]` | 列出顶层窗口（句柄/标题/类名/位置） |
| `scan [--window T] [--all] [--activate]` | **核心**：扫描元素并建立会话（含标注图） |
| `scan --include-text` / `--role button` | 收录文本元素 / 只保留某类角色 |
| `show [--clickable]` | 显示上次会话的元素清单 |
| `find "登录"` | 按文字查找；UIA 未命中自动用 OCR 兜底 |
| `shot [--window T] [--grid] [--cursor]` | 截屏，可叠加坐标网格 |
| `tree [--window T]` | 打印原始控件树（调试） |
| `ocr [--find T] [--region l,t,w,h]` | 跑 OCR（**建议限定 region**，全屏较慢） |

### 鼠标

| 命令 | 说明 |
|---|---|
| `click --id E5` | **推荐**：按编号点击（点击前重新校验位置） |
| `click --text "提交"` / `--xy 800,600` | 按文字 / 按坐标点击 |
| `--button right` / `--count 2` | 右键 / 双击（间隔按系统双击速度自动计算） |
| `--dry-run` | 只定位，**不发按键**；默认把光标移回原位（`--keep-cursor` 可保留） |
| `--yes` | 确认执行不可逆操作（见下「安全约定」） |
| `--shot-after` | 点击后自动截图，便于验证 |
| `move --xy X,Y [--duration 0.5]` | 移动光标（`--duration` 平滑移动） |
| `scroll -240 --xy X,Y` | 滚轮（一格=120，正数向上） |
| `drag --from-xy X,Y --to-xy X,Y` | **拖拽**（也可用 `--from-id/--to-id`） |
| `drag --duration 0.6 --steps 60` | 拖拽更慢/更密（对拖放排序更友好） |
| `swipe --from-xy ... --to-xy ...` | **快速滑动**（短时拖拽，适合列表/翻页） |

### 键盘

| 命令 | 说明 |
|---|---|
| `type "文本"` | 输入到当前焦点（默认 unicode 通道，失败自动降级剪贴板） |
| `type "文本" --id E3` | 先点该元素取得焦点再输入 |
| `type "文本" --clear --enter` | 先清空原内容，输入后回车（`--clear` 属不可逆，需 `--yes`） |
| `type "中文" --method clipboard` | 强制用剪贴板通道（兼容性最好） |
| `type "文本" --method unicode` | 强制 unicode 通道（不自动降级） |
| `key enter` / `key f5` | 单键 |
| `key ctrl+s` | 组合键 |
| `key ctrl c ctrl v` | 依次发送多组按键 |
| `key tab --count 3` | 单键重复 |

### 浏览器（Edge / Chrome）

```bash
$SA browser status                       # 是否运行、窗口、标签列表
$SA browser launch [--url U]             # 启动浏览器
$SA browser open --url "https://..."     # 当前标签打开
$SA browser new-tab --url "https://..."  # 新标签打开
$SA browser tabs                         # 列出标签（▶ 标记当前）
$SA browser switch --index 2             # 切换标签（或 --title "标题片段"）
$SA browser close-tab                    # 关闭当前标签
$SA browser back|forward|refresh|stop    # 导航（还有 hard-refresh/home/top/bottom）
$SA browser find --text "关键词"          # 页内查找（Ctrl+F）
$SA browser scan                         # 扫描页面内可交互元素
```

### 人工介入（验证码 / 登录）

| 命令 | 说明 |
|---|---|
| `guard` | 检测当前是否需要人工介入，返回等级/证据/建议 |
| `guard --mode fast` | 只查窗口标题+UIA（约 2s） |
| `guard --mode deep` | 追加 OCR（对付自绘界面，慢） |
| `guard --keywords "工号,准考证"` | 自定义关键词 |
| `wait-human --timeout 300` | 轮询等待人工环节消失（用户做完自动继续） |

`guard` 的退出码：`0`=无需人工，`3`=需要人工。可直接用于脚本判断。

### 审计与护栏

| 命令 / 退出码 | 说明 |
|---|---|
| `audit [--n 20]` | 查看最近的自动化操作记录（时间/命令/目标/是否被拦截） |
| 退出码 `2` | 被「显示桌面」角落热区保护拦截 |
| 退出码 `3` | 检测到锁屏 / 安全桌面，命令被拒绝 |
| 退出码 `4` | 被「不可逆操作闸门」拦截，需加 `--yes` 显式确认 |

---

## 四、给 Agent 的推荐工作流

### 常规操作

```
1. info                     → 确认坐标系与屏幕
2. windows                  → 找到目标窗口
3. scan --window "XXX" --activate
4. show --clickable         → 或直接看 annotated 标注图
5. click --id EN --dry-run  → 高风险操作先演练（不会真的点，光标自动回原位）
6. click --id EN --shot-after → 执行并截图验证
7. 界面变了就回到第 3 步重新扫描（会话 180 秒过期）
8. 收工前 audit --n 20      → 确认这一轮都做了什么
```

### ★ 遇到需要人工介入的环节

```
1. guard                    → 退出码 3 / human_required=true 即表示"该用户出手了"
2. 停下来，把 evidence 里的关键词告诉用户：
   「检测到【请输入验证码】，请你手动完成，完成后告诉我」
3. 等用户完成 → 运行 wait-human 或重新 guard 复查
4. 确认恢复后继续后续自动化
```

**绝不要在验证码/滑块验证上尝试自动破解**：
程序化轨迹会被风控识别，可能触发账号风险控制。
`drag` 生成的是直线轨迹，**不适用于滑块验证** —— 这类环节一律交给人。

### 浏览器自动化

```
1. browser status / launch  → 确保 Edge 在运行
2. browser open --url ...   → 或 new-tab
3. browser scan             → 看页面有哪些可点的
4. click --xy ...           → 点击页面元素（browser scan 给的坐标）
5. browser tabs/switch      → 多标签协作
6. guard                    → 网页常弹登录/验证，操作前后都建议查一次
```

---

## 五、适用环境与限制

### ✅ 实测验证过的（`selftest --quick` 7/7 通过；完整套件需交互式桌面）

- **本机**：Windows 11 · 双显示器 **2560×1600@150% + 2560×1600@100% 混合 DPI** · 虚拟桌面 5120×1600
- UIA 坐标与 Win32 `GetWindowRect` 完全一致；截图像素与 GDI 逐点 Δ=0
- 跨显示器定位零偏差；左键/右键点击真实触发事件
- **键盘**：Unicode 与剪贴板双通道输入中文均正确落到控件（读回校验通过）
- **拖拽**：应用侧收到 30 次连续 mousemove、位移 370px
- **验证码识别**：`请输入验证码 - 安全验证` → level=high（score 28）；`请拖动滑块完成验证` → level=high
- **Edge**：启动/标签列表/切换/新建/关闭/后退/刷新/页内查找/页面元素扫描 全部通过

### ⚠️ 明确限制

| 限制 | 表现 | 应对 |
|---|---|---|
| **权限隔离（UIPI）** | 普通权限进程**点不动**以管理员运行的窗口（任务管理器、UAC 弹窗） | 需管理员权限运行 python；UAC 安全桌面上的按钮**任何程序都点不到** |
| **前台锁定** | 非前台进程直接调 `SetForegroundWindow` 会被拒（只闪任务栏） | 已用 `AttachThreadInput` 解决；若仍失败，`browser`/`type` 会**拒绝执行**而不是把按键发错窗口 |
| **反作弊 / 安全软件** | 部分游戏、银行客户端拦截模拟输入 | 无解，属对方设计 |
| **UIA 覆盖盲区** | 游戏（DirectX 全屏）、远程桌面画面、Canvas 自绘、**Qt 应用（实测微信 PC 版扫描返回 0 元素）** | 用 `ocr --find` 视觉兜底；纯图标按钮仍可能定位不到 |
| **WinUI/XAML 坐标异常** | 如 Win11 记事本，UIA 返回屏幕外负坐标 | 自动标记"坐标不可信"，`click` 会拒绝；改用 OCR |
| **Chromium 系预热** | Electron/Chrome 首次请求 UIA 时控件树为空（异步构建） | 已内置重试；页面刚加载完时元素可能不全，稍等再 scan |
| **子控件捕获鼠标** | 从按钮/输入框上起拖，父窗口收不到 `WM_LBUTTONDOWN` | 拖拽起点要落在**空白区**（真实存在的 Windows 行为） |
| **浏览器能力边界** | 能：标签/导航/地址栏/点击元素/读页面文字。**不能**：执行 JS、读 DOM 属性、等网络空闲 | 需要后者请改用 CDP/Playwright（另起实例，无登录态） |
| **OCR 速度** | 全屏 OCR 数十秒 | 用 `--region`：实测 700×900 区域约 **2.5 秒** |
| **锁屏 / 无人会话** | 锁屏下截图为黑屏 | 需已登录且桌面未锁 |

### 环境依赖

- Python：本机为 `~/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（3.13），
  请按你的实际路径替换（**整体加引号**，路径可能含中文或空格）
- 已装：`mss`（截图）、`uiautomation` + `comtypes`（控件树）、`pillow`（标注）、
  `rapidocr` + `onnxruntime` + `opencv-python`（视觉兜底，PP-OCRv6 中文模型已就位）
- 运行产物：`%TEMP%\screen-agent\`（截图、标注图、session.json、**audit.jsonl**）

---

## 六、安全约定

1. **先演练后执行**：不可逆操作（删除、提交、支付）务必先 `--dry-run` 确认落点。
   `--dry-run` 只定位不发按键，且默认把光标移回原位，不会打断你正在做的事。
2. **不可逆操作闸门**：目标元素名或所在窗口标题命中危险词（删除/移除/提交/支付/
   覆盖/重置/终止/授权…）时，`click` / `drag` / `type --clear` / `key` 会**默认拒绝执行**
   并返回退出码 `4`。确认无误后加 `--yes` 重新执行。
   - 中文按子串匹配；英文按**词边界**匹配，避免 `Skill`、`Remote` 之类误报。
3. **锁屏拒绝输入**：检测到锁屏 / 安全桌面时，所有会动鼠标键盘的命令直接拒绝
   （退出码 `3`）——此时截图是黑屏，操作毫无意义。
4. **角落热区保护**：默认拦截主显示器右下角"显示桌面"热区（误点会最小化所有窗口），
   确需点击时加 `--allow-corners`。
   判断用的是主显示器的**绝对矩形**，因此副屏放在主屏左侧等布局下同样正确。
5. **陈旧坐标防护**：`click --id` / `drag --from-id` 会复核该点下方的元素身份，
   能识别 `match` / `ancestor` / `occluded`（被遮挡）/ `mismatch`（界面已变）并拒绝危险点击。
   **快速路径与"重新定位"路径执行同样的 occluded 拦截** —— 界面移动时最容易被遮挡，
   慢路径同样不能放行。
6. **激活失败即中止**：所有发键盘的命令都要求窗口激活成功，否则拒绝执行 ——
   宁可报错也不能把按键发到错误窗口。
7. **按键必被抬起**：所有按下/抬起序列都带 `finally` 保证。目标窗口中途消失或
   `SendInput` 失败时，修饰键（Ctrl/Shift/Alt）也会被完整释放，不会把按键卡在你的机器上。
8. **会话时效**：默认 180 秒过期，防止用很久以前的坐标盲点。
9. **人工环节让人来做**：验证码/滑块/登录/支付密码一律交给用户。
10. **全程留痕**：每次 click / drag / type / key（含被拦截的）都追加写入
    `%TEMP%\screen-agent\audit.jsonl`，用 `audit --n 20` 查看。

---

## 七、故障排查

| 现象 | 原因与处理 |
|---|---|
| 点击/输入无反应 | 目标窗口是管理员权限（UIPI）；或元素被遮挡；加 `--shot-after` 看实际发生了什么 |
| "无法激活窗口" | 前台锁定。已内置 AttachThreadInput；若仍失败，先用鼠标手动点一下该窗口再重试 |
| 退出码 4 / "已拦截（不可逆操作）" | 命中危险词闸门。确认无误后加 `--yes`，或先用 `--dry-run` 看落点 |
| 退出码 3 / "已拦截：锁屏" | 工作站处于锁屏/安全桌面，解锁后重试 |
| 退出码 2 / "显示桌面热区" | 角落保护触发，确认要点就加 `--allow-corners` |
| Ctrl/Shift 好像卡住了 | 旧版本可能在异常时遗留按键；现版本已用 try/finally 保证释放。可运行任一按键命令自动清理 |
| 坐标差了 1.5 倍 | DPI 感知被破坏，确认通过 `sa.py` 入口调用（它保证在导入 UI 库前完成 DPI 设置） |
| 点到了旁边的位置 | 界面已变化。重新 `scan`，用标注图核对 |
| `scan` 返回 0 个元素 | 该应用不支持 UIA（如微信）。用 `tree` 确认；改用 `ocr` |
| 中文打不进去 | 换 `--method clipboard`（部分应用对 UNICODE 事件不友好）；`auto` 会在可校验时自动降级 |
| 双击变成了单击 | 间隔已按系统双击时间自适应；若仍异常，检查鼠标设置里的双击速度 |
| 拖拽没反应 | 起点落在子控件上了 —— 换成空白区起始 |
| 标签列表不准 | 页面刚加载，稍等重试；`--title` 比 `--index` 更稳 |
| 想验证技能是否完好 | `python -B scripts/tools/selftest.py`（含真实输入/拖拽/验证码检测；无交互桌面时可加 `--quick`） |

自检命令（路径按你的实际位置替换）：

```bash
"<python 路径>" -B "<skill 目录>/scripts/tools/selftest.py"
# 无交互桌面（如后台/服务会话）时，光标类用例无法运行，用 --quick 只跑非交互部分
"<python 路径>" -B "<skill 目录>/scripts/tools/selftest.py" --quick
```
