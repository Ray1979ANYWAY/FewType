# VoxEcho 项目架构说明

> 本文档基于对项目源码的完整阅读整理，覆盖 `VoxEcho-extension`（Chrome 扩展）与 `VoxEcho-bridge`（本地桥接）两部分的文件职责、数据流与运行机制。

---

## 1. 项目概览

VoxEcho 是一款**本地语音工具集**，包含三大场景：

1. **电子书朗读**（Chrome 扩展）：支持 Google Play Books、Koodo Reader（网页版）、微信读书（weread.qq.com）三个平台，中/英/西/日/韩五语言语音朗读。
2. **长文本转语音**（本地桥接 GUI）：粘贴长文本 → 可选 Native 翻译润色（LLM）→ 微软 TTS 合成音频文件。
3. **语音输入（STT）**（本地桥接 GUI）：按住快捷键说话 → 云端 Whisper 实时转写 → 可选风格化润色/翻译（LLM）→ 自动粘贴到任意文本框。

整体采用**「浏览器扩展 + 本地桥接服务」两段式架构**：

```
┌─────────────────────────────┐        HTTP POST /speak        ┌─────────────────────────────┐
│   VoxEcho-extension         │  ────────────────────────────►  │   VoxEcho-bridge            │
│   (Chrome 扩展, 浏览器内)   │        文本 → MP3 音频          │   (本地 127.0.0.1:5005)     │
│   提取正文 / 切块 / 播放     │  ◄────────────────────────────  │   edge_tts 合成音频         │
└─────────────────────────────┘        GET /health 心跳         └─────────────────────────────┘
                                                                        │
                                                                        ▼
                                                              微软 Edge TTS 云服务
```

- **浏览器侧**：在网页中提取正文 → 按句子切块 → 请求本地服务合成音频 → 播放并高亮。
- **本地侧**：一个小型 Flask 服务（仅监听本机 `127.0.0.1:5005`），把扩展送来的文本交给微软 Edge TTS 合成 MP3 返回，带失败重试。

两者通过 `http://127.0.0.1:5005/speak` 通信。Chrome 扩展的 `host_permissions` 已包含该地址。

---

## 2. 目录结构总览

```
D:\Documents\VoxEcho\
├── README.md                       # 项目简介（对外）
├── ARCHITECTURE.md                 # 本文档
│
├── VoxEcho-bridge\                 # 【本地桥接】Python 工程（三大场景 GUI + TTS 服务）
│   ├── launcher.py                 # 主程序：Tk GUI（三场景卡片式切换）+ pystray 托盘 + 服务管理
│   ├── server.py                   # Flask TTS 服务（edge_tts 合成，127.0.0.1:5005）
│   ├── hotkey_hook.py              # Windows WH_KEYBOARD_LL 低层键盘钩子（热键触发 + 吞键防开始菜单）
│   ├── stt_engine.py               # STT 引擎：录音/实时 RMS/OGG 压缩/Whisper 转写/风格+翻译积木
│   ├── provider.py                 # 模型平台抽象：Groq/火山引擎/硅基流动/自定义，双 Key（ASR+LLM），测试连接
│   ├── volcengine_asr.py           # 火山引擎流式 ASR 2.0（WebSocket 协议，首帧8字节/结果帧12字节头）
│   ├── gen_icon.py                 # SVG→ICO/PNG 多尺寸图标生成
│   ├── build.bat / build.ps1       # onefile 打包脚本
│   ├── build_onedir.bat            # onedir 打包脚本（备用）
│   ├── VoxEcho-bridge.spec         # PyInstaller 配置
│   ├── requirements.txt            # Python 依赖
│   ├── VoxEcho.ico + icon\         # 多尺寸图标（16~256）
│   ├── tools\                      # 图标修复工具
│   ├── ICON.md                     # 图标问题专项文档
│   └── DEVELOPER.md                # 发布清单备忘
│
└── VoxEcho-extension\              # 【Chrome 扩展】浏览器侧
    ├── manifest.json               # MV3 清单
    ├── background.js               # 唯一 service worker 入口（路由）
    ├── background-playbooks.js     # Play Books 朗读逻辑
    ├── background-koodo.js         # Koodo 朗读逻辑
    ├── background-weread.js        # 微信读书朗读逻辑（空页翻页/标题页识别/书尾判断）
    ├── content-playbooks.js        # Play Books 正文提取
    ├── content-koodo.js            # Koodo 正文提取
    ├── content-weread.js           # 微信读书 isolated world（消息转发/空页上报/翻页指令）
    ├── content-weread-main.js      # 微信读书 main world（fillText hook 逐字采集/文本重建/高亮绘制）
    ├── chunking.js                 # 文本分块算法（平台无关）
    ├── offscreen.js                # 音频播放器
    ├── offscreen-client.js         # offscreen 生命周期管理
    ├── popup.html / popup.js       # 弹窗 UI
    ├── diagnostics.js              # 诊断日志汇总
    ├── _locales\                   # 多语言（zh_CN / en）
    ├── server\                     # 开发期备用 server（独立版本）
    └── icon\                       # 扩展图标
```

---

## 3. 浏览器侧（VoxEcho-extension）文件职责

### 3.1 入口与路由

| 文件 | 职责 |
|------|------|
| `manifest.json` | MV3 配置。声明 `storage`/`offscreen` 权限；`host_permissions` 覆盖 Google Play Books、books.googleusercontent.com、web.koodoreader.com/.cn 与 `127.0.0.1:5005`；注册唯一 service worker（`background.js`）；按平台注入 content 脚本 |
| `background.js` | 唯一的 service worker 入口（MV3 限制一个扩展只能有一个后台脚本）。本身很薄，只做两件事：① 处理平台无关的诊断日志消息；② 把其余消息按平台分发给 `background-playbooks.js` / `background-koodo.js`。维护 `ACTIVE_PLATFORM_KEY`（记录"当前正在朗读哪个平台"，供暂停/继续等不查标签页的操作使用） |

### 3.2 平台朗读逻辑

| 文件 | 职责 |
|------|------|
| `background-playbooks.js` | Play Books 专属：文本提取结果缓存（`chrome.storage.session`）、朗读状态机、翻页等待/重试、内容对齐校验（防止缩水循环重播）、跨页残句（X 区）拼接、自动翻页上限控制 |
| `background-koodo.js` | Koodo 专属：整章内容一次性提取完成，直接切块→发 offscreen 播放。已实现：整章缓存、从当前可见段开始朗读、暂停/继续/停止/语速/进度、高亮跟随、自动翻章（章末检测下一章链接并跳转）、空页/插图页自动跳过 |
| `background-weread.js` | 微信读书专属：canvas 渲染无 DOM 语义标签，通过 fillText hook 逐字采集文本。支持双栏/滚动两种排版模式。核心功能：文本重建与去重、翻页锚点搜索（片段搜索接续位置）、视口定位（跳过标题从正文第一句起读）、标题页识别（<30字无标点→不朗读继续翻页）、空页/插图页自动翻页（指纹比较判断书尾）、高亮状态机、静音占位（标题后450ms停顿） |
| `content-playbooks.js` | 注入 Play Books 顶层页面与正文 iframe。从 DOM 按标签（`p, h1~h6`）提取正文段落，过滤分页延续箭头，合并被硬切的碎片段落；响应起点查询时优先用「用户鼠标选中的文字」定位朗读起点，无选中再回退到视口内第一个完整句子 |
| `content-koodo.js` | 注入 Koodo 页面。因正文装在 `iframe#kookit-iframe`（sandbox 未开 allow-scripts，不能注入），只能从顶层页面跨边界读 `contentDocument`。检测整章刷新后重新提取，并定位视口内当前段作为朗读起点；同样支持「选中文字优先作为朗读起点」 |
| `content-weread.js` | 注入微信读书页面（isolated world，document_start）。负责：main world 与 background 之间的消息转发、空页周期上报（每1s检测视口内有无可见字符）、翻页指令执行（PageDown/ArrowRight）、划选起点查询转发、高亮消息转发 |
| `content-weread-main.js` | 注入微信读书页面（MAIN world，document_start）。核心：hook CanvasRenderingContext2D.fillText 逐字采集字符（记录x/y/size/font/transform/canvas元素引用），文本重建（排序/去重/标题行检测/虚拟句号插入），高亮绘制（离屏canvas measureText测实际字符宽度，应用ctx.transform的tx/ty，textBaseline=middle补偿），空页检测（视口内可见字符数），视口定位（正文字号众数基准识别标题行） |

### 3.3 平台无关共享模块

| 文件 | 职责 |
|------|------|
| `chunking.js` | 文本分块算法。核心：中日韩文字符每字计 1 单位，拉丁连续字母串计 1，连续数字串计 1（小数点在数字中不算分隔符）。攒够 `MIN_CHUNK_WORDS=30` 后再遇标点切块，避免切碎；引号/括号等收尾符并入上一句，避免孤零零甩到句首 |
| `offscreen.js` | offscreen 音频播放器：管理播放队列、预取后 5 块（`PREFETCH_WINDOW=5`）、单块合成失败重试 3 次（间隔递增）、连续失败 3 次才停播；用 `AbortController` 处理会话切换/停止时的取消 |
| `offscreen-client.js` | offscreen 文档生命周期管理：发送前确保 offscreen 存在（Chrome 会回收长时间不发声的 offscreen），发送失败重建后重试 |
| `diagnostics.js` | 诊断日志汇总。content / offscreen / background 各自 console 分散，统一收拢成一条时间线，最多保留 4000 条；`textPreview` 截断长文本避免日志过大 |

### 3.4 UI 与杂项

| 文件 | 职责 |
|------|------|
| `popup.html` / `popup.js` | 扩展弹窗：开始/暂停/继续/停止按钮（状态联动禁用）、语速调整、显示当前标签页提取内容预览、导出/清空诊断日志。界面按浏览器系统语言自动选择 6 套语言包（简中/繁中/英/西/日/韩），其余语言兜底英文；朗读起点注释「从选定文本开始朗读，或者从页首开始朗读」随语言切换 |
| `_locales/` | `zh_CN` / `en` 多语言（扩展名称、描述） |
| `server/` | 开发期未打包时使用的备用独立 server（功能与 bridge 的 server.py 相同，代码更简，无 rate 支持） |

---

## 4. 本地侧（VoxEcho-bridge）文件职责

| 文件 | 职责 |
|------|------|
| `launcher.py` | 主程序。`run_gui()`：Tk 窗口（三场景卡片式切换：语音输入/电子书朗读/长文本TTS）+ pystray 系统托盘 + 服务管理 + 开机自启 + 运行日志右缘抽屉。场景3含：热键管理（`_start_hotkey`/`_poll_hotkey`/`_hotkey_timeout`）、录音/转写（`_stt_begin`/`_stt_finish`/`_stt_commit`）、自动上屏（`_simulate_ctrl_v`：卸载钩子→Ctrl-down→Win-KEYUP→模拟→重装钩子）、风格配置面板（5固定槽位+行内编辑）、API配置面板（Groq/火山/硅基/自定义，双Key，模型下拉可粘贴）。`main()` 支持 `--run-server`。含多语言翻译、图标加载、AppUserModelID |
| `server.py` | Flask 服务：`POST /speak` 接收 `{text, voice, rate}` → edge_tts 合成 MP3 返回（`MAX_ATTEMPTS=3` 重试，间隔 1s）；`GET /health` 心跳。监听 `127.0.0.1:5005`，默认音色 `zh-CN-XiaoxiaoNeural` |
| `hotkey_hook.py` | Windows WH_KEYBOARD_LL 低层键盘钩子。`WinHotkey(mods, trigger, on_begin, on_end)`：状态机 `_on_event`（左右修饰键 VK 归一、组合全按下→begin、任一松开→end）、吞键逻辑（`_swallow_keys` 集合防两键同松漏吞、`_ignore_all` 模拟期间忽略一切）、`_callback`（LLKHF_INJECTED 注入忽略、ctypes argtypes 显式声明防64位句柄截断）。回调零 Tk 操作（只设标志），主线程 `root.after(50)` 轮询执行真实动作 |
| `stt_engine.py` | STT 引擎。`Recorder`：pyaudio 录音、实时 RMS（声浪波形）、OGG 压缩上传。`build_whisper_prompt`：ASR 层标点引导 Prompt（全角/半角标点按语言自适应）。`process_result`：风格积木（极速记录/智能润色/严肃文档/自定义）+ 翻译积木动态拼接为单次 LLM 请求（不二次调用）。热词文档（TXT，ASR 纠偏）。Text Normalization（数字/百分号/机构简称大写） |
| `provider.py` | 模型平台抽象。平台配置：Groq（推荐，needs_proxy=True）、火山引擎（仅 zh-CN 界面展示，needs_proxy=False，推荐国内使用）、硅基流动（needs_proxy=False）、自定义（ASR/LLM 分离 base_url+key）。双 Key 架构（语音 Key + 方舟 LLM Key）。`sanitize_api_key`（剔除非 ASCII/隐藏字符）。`test_connection`（内联绿 badge 显示延迟）。模型下拉可粘贴自定义 model 名 |
| `volcengine_asr.py` | 火山引擎流式 ASR 2.0。WebSocket 协议封装：首帧 8 字节头（appid/token/cluster）、结果帧 12 字节头。实时音频帧发送 + 中间结果/最终结果解析。已知坑：首帧与结果帧头结构不同，需分别处理 |
| `gen_icon.py` | SVG→ICO/PNG 多尺寸图标生成。渲染用户设计的 SVG（毛玻璃风格+声波+回声环）→ 输出 7 尺寸 ICO（16/24/32/48/64/128/256）+ 全套 PNG，三处输出（bridge 根、bridge/icon/、extension/icon/） |
| `build.bat` | onefile 打包：装依赖 → PyInstaller `--onefile --windowed --icon VoxEcho.ico --add-data VoxEcho.ico;.` → copy ico 到 dist。**不要**对 onefile 跑 rcedit（会毁 PKG） |
| `build.ps1` | build.bat 的 PowerShell 版 |
| `build_onedir.bat` | onedir 打包（文件夹分发），可安全对文件夹内 exe 跑 rcedit 换图标 |
| `VoxEcho-bridge.spec` | PyInstaller 配置 |
| `requirements.txt` | `edge-tts` / `flask` / `flask-cors` / `pystray` / `Pillow` / `pyinstaller` |
| `VoxEcho.ico` + `icon/` | 多尺寸图标（16/24/32/48/64/128/256） |
| `tools/` | `fix_exe_icon.py`（rcedit 版，仅 onedir 使用）；`fix_exe_icon_safe.py`（onefile 安全换图标：注入后原样拼回 PKG）；`rcedit-x64.exe` |
| `ICON.md` | 图标问题专项说明（含"onefile 不可用 rcedit"警告） |
| `DEVELOPER.md` | 发布清单备忘 |

---

## 5. 一次完整朗读的运作流程

### 5.1 启动阶段

1. 用户运行 `VoxEcho-bridge.exe` → launcher 启动 GUI + 托盘 → 自动拉起 server.py → 本地 `127.0.0.1:5005` 就绪。
2. 用户在 Chrome 加载扩展（开发者模式 → Load unpacked → 选 `VoxEcho-extension` 文件夹）。

### 5.2 朗读阶段（以 Play Books 为例）

```
打开书页
   │
   ▼
content-playbooks.js 从 DOM 提取正文段落
   │  （PAGE_TEXT_UPDATED 消息）
   ▼
background.js 按 sender.tab.url 路由到 background-playbooks.js
   │  文本缓存到 chrome.storage.session
   ▼
用户点 popup「开始」 → content 先查「鼠标选中文字」再查「视口内完整句子」
   │  确定朗读起点（起点之前的正文作为 skipPrefix 交给后台裁掉）
   ▼
chunking.js 按字数/标点切块
   │
   ▼
background-playbooks.js 把文本块发给 offscreen.js
   │
   ▼
offscreen.js 请求 http://127.0.0.1:5005/speak（预取后 5 块）
   │
   ▼
server.py 调 edge_tts 合成 MP3 返回
   │
   ▼
offscreen.js 播放 MP3；同时上报 HIGHLIGHT_CHUNK 高亮当前句
   │
   ▼
读完当前页 → background 自动翻页 → content 提取下一页 → 循环
```

### 5.3 Koodo 平台差异

整章内容通过 `content-koodo.js` 一次性提取完成，不存在"这一页没读完、下一页还没来"的等待场景。因此：
- 直接切块、一次性发给 offscreen 播放；
- **没有** Play Books 那套翻页等待/重试/内容对齐/防缩水循环的状态机；
- 章节读完后自动翻章：检测页面底部"下一章"链接并点击跳转，跳转后重新提取整章内容继续朗读；连续空页/插图页自动跳过。

---

### 5.4 微信读书平台差异

微信读书是三个平台中唯一使用 **canvas 渲染**的平台，无 DOM 语义标签（p/h1 等），因此架构与另外两个平台完全不同：

- **文本提取**：MAIN world 注入 `content-weread-main.js`（document_start），hook `CanvasRenderingContext2D.fillText` 逐字采集字符（记录 x/y/size/font/transform/canvas 元素引用），再按坐标排序重建文本。isolated world 的 `content-weread.js` 仅负责消息转发。
- **双栏/滚动两种排版**：微信读书支持双栏（左右两页）和单栏滚动两种模式，采集逻辑统一按文档坐标排序，不依赖具体排版。
- **翻页机制**：微信读书是**整页替换**（不是 DOM 追加），翻页时 canvas 元素被替换，旧字符必须清理（否则文本污染）。通过 `canvasObserver` 监听 canvas 移除事件，按 `el: canvas` 引用同步过滤字符。
- **标题识别**：无 heading 语义标签，通过正文字号基准（前 200 字符字号众数）识别标题行（字号 > 正文字号 × 1.2），支持多级标题（章标题 + 小节号）。标题后插入静音占位（450ms 停顿）。
- **空页/插图页**：连续插图页无文本可读，通过视口内可见字符数判断空页，连续 4 次空页自动翻页；书尾判断用指纹比较（必须包含 URL，因为翻页模式 URL 会变）。
- **高亮绘制**：在 canvas 上方叠加绝对定位 div，用离屏 canvas `measureText` 测每个字符实际宽度（西语/英语字符宽度不统一），应用 `ctx.transform()` 的 tx/ty 平移，`textBaseline=middle` 补偿。

---

## 5.5 长文本转语音（TTS）场景运作流程

### 5.5.1 启动与配置

1. 用户在 launcher GUI 切换到「长文本转语音」卡片。
2. 粘贴长文本到输入框。
3. **可选**：勾选「Native 翻译润色」→ 首次使用弹出 API 配置面板（Groq/火山引擎/硅基流动/自定义），用户粘贴 API Key → 选择目标语言 → 选择音色（音色列表实时从微软 TTS 拉取，按目标语言联动过滤）。
4. 不勾选翻译则跳过 LLM 步骤，直接用微软 TTS 合成。

### 5.5.2 翻译润色管线（勾选翻译时）

```
用户粘贴文本
   │
   ▼
provider.py 根据平台配置创建 LLM 客户端
   │  （Groq/火山/硅基/自定义，双 Key 架构：ASR Key + LLM Key）
   ▼
动态组装 System Prompt：风格积木 + 翻译积木（单次 LLM 请求，不二次调用）
   │  风格积木：流畅化/严肃文档/自定义（用户可编辑 Prompt）
   │  翻译积木：Translate to native [目标语言]
   ▼
LLM 返回翻译润色后的文本
   │
   ▼
用户预览翻译文本（可编辑）
   │
   ▼
点击「生成音频」→ 调微软 TTS（edge_tts）合成 MP3/WAV
```

### 5.5.3 关键设计

- **翻译与风格积木动态拼接**：用户在 UI 上勾选翻译+选择风格，后台把翻译 Prompt 拼接到风格 Prompt 里，组装成一条指令发给 LLM，只调一次 API（延迟 ~500ms）。
- **音色联动**：选择目标语言后，音色下拉自动过滤为该语言的微软 TTS 音色（实时从 edge_tts 拉取，可能需要等待几秒）。
- **API Key 清洗**：`sanitize_api_key` 剔除非 ASCII 字符、隐藏空格、换行符（用户从网页复制 Key 时常带不可见字符，导致 401）。
- **平台代理**：Groq/自定义走系统代理（被墙必需），火山/硅基直连（国内服务走代理反而不稳定）。`PlatformDef.needs_proxy` 标志控制。

---

## 5.6 语音输入（STT）场景运作流程

### 5.6.1 启动与配置

1. 用户在 launcher GUI 切换到「语音输入」卡片。
2. 首次使用或勾选「风格化/翻译」时弹出 API 配置面板：选择托管平台（Groq 推荐/火山引擎 国内推荐/硅基流动/自定义）→ 粘贴 API Key → 选择 ASR 模型和 LLM 模型（下拉可粘贴自定义 model 名）。
3. 配置保存到本地 JSON，后续使用直接读取，无需重复配置。
4. 设置快捷键（默认 CTRL+Win，可自定义；Mac 版默认长按 FN）。

### 5.6.2 极速记录模式（单通道，仅 Whisper，~200ms）

```
用户按住快捷键说话
   │
   ▼
hotkey_hook.py WH_KEYBOARD_LL 钩子检测组合键 → begin
   │  （回调零 Tk 操作，只设标志；主线程 root.after(50) 轮询）
   ▼
_stt_begin：清理所有修饰键（ALT/Win/Ctrl）→ 启动 Recorder 录音
   │  （pyaudio 录音、实时 RMS 声浪波形、OGG 压缩）
   ▼
用户松开快捷键 → end
   │
   ▼
stt_engine.py：压缩音频 → 调 Whisper ASR（带标点引导 Prompt）
   │  （火山引擎走流式 WebSocket，Groq/硅基走一次性 HTTP）
   ▼
返回转写文本 → 写入剪贴板 → _simulate_ctrl_v 自动粘贴到当前文本框
   │
   ▼
状态条显示「完成」→ 3 秒后淡出
```

### 5.6.3 风格化/翻译模式（双通道，Whisper + LLM，~500ms）

```
用户按住快捷键说话 → 录音 → 松开
   │
   ▼
Whisper ASR 转写（带标点引导 Prompt）
   │
   ▼
动态组装 System Prompt：风格积木 + 翻译积木（单次 LLM 请求）
   │  风格积木：极速记录(仅Whisper)/智能润色/严肃文档/自定义(最多5个)
   │  翻译积木：勾选翻译时叠加，目标语言由用户选择
   ▼
LLM 返回风格化/翻译后的文本
   │
   ▼
写入剪贴板 → _simulate_ctrl_v 自动粘贴
```

### 5.6.4 自动上屏（_simulate_ctrl_v）确定性方案

```
1. hook_obj.stop() — 完全卸载 LL 钩子（UnhookWindowsHookEx）
   此时系统中无任何东西能吞模拟按键
2. ALT KEYUP + ESC — 清理可能卡住的 ALT、关闭可能弹出的菜单
   （ALT KEYUP 和 ESC 不会触发开始菜单，可以在 Ctrl down 前做）
3. AttachThreadInput + BringWindowToTop + SetForegroundWindow
   把焦点恢复到说话前的前台窗口（_stt_target["hwnd"]）
   验证：打印目标窗口/当前前台窗口标题，确认 match=True
4. Ctrl down — 系统知道 Ctrl 按下
5. Win KEYUP（左右）— 此时 Ctrl 按着，Windows 不弹开始菜单
6. V down → 20ms → V up → Ctrl up — 正常粘贴
7. hook_obj.start() — 重装钩子 + 80ms _ignore_all 保险
```

### 5.6.5 关键设计

- **单通道 vs 双通道**：极速记录模式只调 Whisper（~200ms，最省资源）；风格化/翻译模式 Whisper + LLM（~500ms，一次 LLM 请求）。用户通过 UI 选择模式，底层自动决定管线。
- **Prompt 模块化**：风格积木和翻译积木独立存储，用户勾选时动态拼接为单次 System Prompt，不二次调用 LLM。自定义风格最多 5 个（预制「王家卫风」打样），用户可编辑 Prompt 和命名。
- **ASR 标点引导**：Whisper 原生不加标点，通过固定 Prompt（含全角/半角标点示范，按语言自适应）引导模型输出带标点的文本。
- **热词文档**：用户可维护一个 TXT 热词表，ASR 转写时用于纠偏（如专业术语、人名）。
- **Text Normalization**：轻量规则后处理（数字→阿拉伯数字、百分号、机构简称大写如 WHO/NASA），不调 LLM。
- **10 秒超时**：松开快捷键后 10 秒未转写完成则中止，状态条显示失败原因（网络/额度/模型不可用）。
- **杀进程按钮**：卡住时可中止当前转写/润色任务（置 _stt_cancel 事件，work 线程关键点检查）。

---

## 6. 关键设计决策与坑位记录

| 主题 | 结论 |
|------|------|
| MV3 单 service worker | 一个扩展只能注册一个后台脚本，故 `background.js` 做路由，平台逻辑拆到独立模块（`background-*.js`），彼此零共享 |
| Play Books DOM 适配 | 不同书渲染引擎不同，class 名每本书动态分配不可依赖 → 改为按标签（`p`/`h1~h6`）识别，过滤分页箭头用内容正则而非 class |
| Koodo iframe 限制 | `iframe#kookit-iframe` sandbox 未开 allow-scripts，无法注入 → 顶层页面跨边界读 `contentDocument`（sandbox 开了 allow-same-origin，允许读） |
| 文本切块粒度 | 纯按标点切会把中文对话切太碎 → 改成"先攒 30 字，够字再找标点收尾"；引号类收尾符并入上一句 |
| 朗读起点优先级 | 用户点「开始」时：① 若书页里有鼠标选中的文字（非空选区、起点落在正文段落里），从选中的第一个字开始朗读（选中可能跨段，只取起点）；② 无选中则按视口内第一个完整句子起读。两个平台共用这套规则，各自用 `skipPrefix`（Play Books）/ `segmentIndex+charOffset`（Koodo）实现 |
| 弹窗多语言 | 手动 JS 字典方案（不用 chrome.i18n）：`popup.js` 里按浏览器系统语言（`chrome.i18n.getUILanguage()`）选语言包，简中/繁中/英文为精修基准文案，西/日/韩以英文为源文本翻译；命中 es/ja/ko 及其地区变体用对应语言包，其余语言兜底英文。加语言 = 往 `I18N` 加一个字典块 |
| 合成失败处理 | server 侧 edge_tts 重试 3 次（连接不稳时大概率一两次即过）；offscreen 侧单块再重试 3 次；**不加** asyncio 强制超时（实测在 Windows 上与 edge-tts WebSocket 不兼容，反而全超时），极端卡顿由扩展 background 监控兜底 |
| offscreen 被回收 | Chrome 会关闭长时间无声的 offscreen → 每次发送前重新确认存在，失败重建重试 |
| onefile 图标 | PyInstaller `--icon` 会把完整 7 尺寸嵌入 exe（无"只嵌 16/32"限制）。**对 onefile 跑 rcedit/UpdateResource 会毁 PKG**；换图标唯一安全路径是 `tools/fix_exe_icon_safe.py`（注入后拼回 PKG） |
| 任务栏发糊 | 曾因 launcher 的 `iconbitmap` 用 16px 小帧覆盖 `iconphoto` 高清帧导致；已修复为 iconbitmap 仅兜底 |
| 微信读书 canvas 渲染 | 微信读书正文用 canvas 绘制，无 DOM 语义标签（p/h1等），无法用常规 DOM 提取。方案：MAIN world 注入 hook CanvasRenderingContext2D.fillText，逐字采集字符坐标/字号/字体/transform，重建文本。需注入两次：MAIN world（content-weread-main.js，document_start）hook fillText；isolated world（content-weread.js，document_start）转发消息 |
| 文本污染修复 | 切章时微信读书替换 canvas 元素，旧 canvas 采集的字符未清理，canvasIdx 回收指向新 canvas，导致新旧字符混排（上一章句子被插到新章中间）。修复：fillText 采集记录 `el: canvas` 元素引用；canvasObserver 移除 canvas 时按 `c.el !== removedEl` 同步过滤字符；重建前过滤过期字符；排序 comparator 防御 |
| 高亮坐标校准 | fillText 的 y 是 textBaseline=middle（文字中点），文字顶部 = y - size*0.5。必须应用 ctx.transform() 的 tx/ty 平移（之前忽略导致换文章后高亮错位）。高亮 cssY = (logicalY - size*0.5) * ratio，logicalY = c.y + c.ty/c.scaleY |
| 西语/英语高亮重叠 | 中文等宽字符用 size 估算宽度准确，但西语/英语字符宽度不统一（i窄w宽），统一用 size 导致窄字符框太宽重叠。修复：离屏 canvas measureText 测每个字符实际宽度（带缓存，key=font+char），font 为空时回退到 size |
| 空页检测：视口内可见字符 | 不能用全局 charIndex.length 判断空页——charIndex 是整章采集的（含视口外正文），即使当前页是纯插图，全局 charIndex 也可能 >0。改成统计当前视口内的可见字符数（应用 canvas transform + ratio 计算 absTop），只要视口内有一个可见字符就不是空页（保守判断） |
| 标题页识别 | 微信读书无 heading 语义标签，标题页/封面页（如章节标题）会被误判为正文朗读。方案：background 侧判断新章节文本 <30字 且 无标点 = 标题页，不朗读，直接翻页继续找正文。正文字号基准（前200字符字号众数）识别标题行，插入虚拟句号分隔标题与正文 |
| 书尾判断：指纹必须包含 URL | 空页翻页的书尾判断用指纹比较（连续4次无变化→书尾）。weread 是翻页模式，URL 每次翻页都会变，必须比较 `fp.url === last.url`。漏掉 URL 比较会导致连续插图页每次都被误判为"无变化"（textLen=0/canvasCount/scrollY 都相同），4次后误判为书尾。koodo 是滚动模式 URL 不变，用 imgCount/imgSrcs 判断内容变化 |
| 排序性能：预缓存 canvas rect | charIndex.sort 的 comparator 里每次比较都调用 getBoundingClientRect() 会触发 reflow，大章节（5000-10000字符）排序比较约14万次，直接卡死主线程。修复：排序前预缓存所有 canvas 的 getBoundingClientRect() 到数组，comparator 直接用缓存，getBoundingClientRect 从28万次降到2次 |
| 静音占位：标题后停顿 | 微信读书无 heading 语义标签，标题与正文直接连在一起朗读没有停顿。方案：标题块末尾插入私有标记 `\uE000`，切 chunk 时识别为静音块，播放时等待 450ms（`PAUSE_MS`）。用 `\u0001` 作为 charIndex 虚拟占位符（`isVirtual=true`），不参与高亮，不影响文本搜索。不能用普通句号做分隔符——会污染文本重建和搜索匹配 |
| 标题识别：正文字号基准 | 不能写死标题字号（不同书/不同主题字号不同，日志里出现过 28.8 vs 18、33.6 vs 21）。方案：取前 200 字符的字号众数作为正文字号，标题阈值 = 正文字号 × 1.2。支持多级标题连续识别（章标题 + 小节号），直到出现正文行为止 |
| 已验证死路：WeakMap+canvasId | 尝试用 WeakMap 存储 canvas 引用 + canvasId 追踪 canvas 替换，失败原因：document_start 时 body 不存在，MutationObserver 无法初始化；canvas 替换时旧引用被 GC，WeakMap 自动清理导致字符丢失。最终方案：直接在字符上记录 `el: canvas` 元素引用，canvasObserver 移除时按引用过滤 |
| 已验证死路：buildCharSpans 全量持久 DOM | 尝试把每个字符包成 `<span>` 持久插入 DOM 实现高亮，失败原因：大章节 12000+ span 阻塞主线程，页面卡死；微信读书 canvas 重绘时 span 坐标全部失效需要重建。最终方案：绝对定位 div 高亮层，只高亮当前句，用完即删 |
| 已验证死路：fillRect 清屏检测 | 尝试通过 hook `clearRect`/`fillRect` 检测翻页时清屏来重置字符缓冲，失败原因：微信读书切章是**替换 canvas 元素**而非复用 canvas 清屏，clearRect 不会被调用。最终方案：canvasObserver 监听 canvas 元素移除事件 |
| Koodo 自动翻章 | Koodo 章末有"下一章"按钮（class 动态分配不可依赖），通过按钮文本正则匹配（`/下一章|下一节|Next/i`）定位并点击。跳转后 iframe 重新加载，`content-koodo.js` 检测整章刷新后重新提取，从第一章第一句开始继续朗读。连续空页/插图页通过 `imgCount`/`imgSrcs` 指纹比较判断，连续 4 次无变化则翻页 |
| 热键机制：pynput→LL 钩子 | pynput 回调 `return False` 语义是"停止整个监听器"，不是"吞键"→ 彻底放弃 pynput 做热键。改用 Windows WH_KEYBOARD_LL 低层钩子，回调 `return 1` 真正吞键。ctypes 必须显式声明 argtypes/restype，否则64位 HMODULE 句柄按32位截断 → SetWindowsHookExW 返回 NULL |
| 钩子回调线程：pystray 而非 Tk | LL 钩子回调执行在"正在 pump 消息的线程"，本机是 pystray 托盘线程（不是 Tk 主线程）。回调里直接调 `root.after`/`ui_log` = 跨线程操作 Tk → 轻则异常被吞（热键无效），重则 GIL 错乱崩溃（PyEval_RestoreThread）。修复：回调零 Tk 操作（只设标志 + ctypes GetForegroundWindow），主线程 `root.after(50)` 轮询标志执行真实动作 |
| 自动上屏：卸载钩子→模拟→重装 | 自动上屏用 keybd_event 模拟 Ctrl+V，但模拟的按键会被自己的 LL 钩子收到 → 吞键逻辑可能吞掉模拟的 Ctrl up → 系统认为 Ctrl 一直按着 → 键盘全乱（必须重启恢复）。keybd_event 是异步的，_ignore_all 恢复太早也会导致竞态。最终确定性方案：模拟前 `hook.stop()` 完全卸载钩子 → Ctrl down → Win KEYUP（此时 Ctrl 按着，Windows 不弹开始菜单）→ V down/up → Ctrl up → `hook.start()` 重装钩子 + 80ms _ignore_all 保险。卸载窗口仅 ~50ms，发生在用户松开快捷键之后 |
| 两键同松漏吞：_swallow_keys 集合 | 两键几乎同时松开时，第一个松开的键触发 end（rec=False），第二个键的 was_rec=False → 吞判定失败 → 第二个键 release 不被吞 → 如果第二个是 Win，系统收到 Win up → 开始菜单弹出。修复：begin 时把所有成员 vk 加入 `_swallow_keys` 集合，成员 release 时只要在集合里就吞（无论顺序、无论 rec 状态），吞后移除 |
| 先按 Win 后按 Ctrl 的状态不一致 | 如果用户先按 Win 再按 Ctrl，Win down 时 combo 未激活 → 不吞 → 系统知道 Win 按下了；但 Win up 时被 _swallow_keys 吞 → 系统不知道 Win 松开 → 系统认为 Win 一直按着。修复：模拟 Ctrl+V 时先 Ctrl down（系统知道 Ctrl 按下），再发 Win KEYUP（此时 Ctrl 按着，Windows 检测到 Win 按下期间有其他键，不弹开始菜单），既清理 Win 状态又不弹菜单 |

---

## 7. 开发常用命令

```bat
:: 打包 onefile（本地桥接）
cd D:\Documents\VoxEcho\VoxEcho-bridge
build.bat

:: 打包 onedir（备用，可 rcedit 改图标）
build_onedir.bat

:: 仅启动 TTS 服务（无 GUI，调试用）
python launcher.py --run-server

:: 手动验证服务
curl http://127.0.0.1:5005/health
```

扩展加载：Chrome → chrome://extensions → 开发者模式 → Load unpacked → 选 `D:\Documents\VoxEcho\VoxEcho-extension`。

---

*本文档由源码阅读整理，如需更新请同步修改对应代码后再改此处。*
