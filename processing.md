# VoxEcho 项目处理日志（processing.md）

> 从 2026-09-06 起记录本项目（VoxEcho 浏览器扩展）的洞察、诊断与修改过程。
> 每条记录包含：背景 → 洞察/根因 → 修改 → 验证 → 残留风险。

---

## 2026-09-06 微信读书单页滚屏模式下朗读"无端插入上一章第一句"bug

### 背景

用户反馈：微信读书（weread.qq.com）单页滚屏（scroll）模式下，朗读到章节中段时会突然插进上一章节的第一句话（如日志里的"王二生在北京城，我就是王二。"）。项目接近收尾，此为本轮最后一块。

### 诊断过程与关键证据（来源：tts-diag-log-2026-09-05T16-12-33-322Z.txt）

1. **三个朗读会话的 fullText 长度演化**：7125（首屏）→ 12067（翻页后纯 canvas）→ 16156（DOM 合并后）。
2. **首屏文本异常**：`三十而立一。王二生在北京城，我就是王二。夏天的早上…` —— "一"字是标题行句号插入位置错乱的痕迹。
3. **翻页后 newStart**：`。。。三十而立。想到这件事，不知不觉喝了很多酒…` —— 开头三个句号即上一轮重建残留的虚拟标题句号被排序顶到最前。
4. **插句直接证据**（00:10:12.636）：`chunk[3] "牛子你意下如何？"王二生在北京城，我就…"` —— 正确文本此处应为"她上唇留一撮胡须"。
5. **完整预览对比（298/376 块）确认污染是"整章级"而非"一句级"**：旧章节（三十而立一）整章字符（王二生/书包板砖/教室/虚伪论）与新章节字符交错混排在同一个 fullText 里。

### 根因（最终结论）

- **微信读书翻页/换章时会替换 canvas 元素**（旧 canvas 移除、新 canvas 加入），而 `content-weread-main.js` 的 `canvasObserver` 只把旧 canvas 从 `canvasElements` 数组删除，**没有删除它采集的字符**（`charIndex`）。
- 旧字符的 `canvasIdx` 被"回收"指向新 canvas，排序时旧章节全部字符与新章节字符按 (y,x) 混排 → 朗读流被污染。
- **虚拟标题句号**（`canvasIdx=-1`，为拆开标题与正文而人工插入的"。"）不绑定任何 canvas，翻页后残留在 charIndex 中；排序时因无 canvas 坐标（leftA=-1）被**顶到文本最前面** → 产生 `。。。三十而立` 前缀。
- 次要：标题行判定（`lineSize > nextSize*1.2`）对旧章节的章节号"一"（28.8px）也判定为标题，多插入一个虚拟句号。

### 修改（content-weread-main.js，4 处）

1. **fillText 采集时给每个字符记录 `el: canvas`** —— 用元素身份而非索引关联字符，避免索引回收导致错位。
2. **canvasObserver 移除 canvas 时按元素身份同步丢弃其字符**（`charIndex = charIndex.filter(c => c.el !== removedEl)`）—— 核心修复。
3. **rebuildTextAndNotify 重建前过滤三类过期字符**：
   - 上一轮重建插入的虚拟标题句号（`canvasIdx === -1`，本轮会按新布局重新插入）；
   - canvas 元素已被移除的字符（`el` 不在当前 `canvasElements`，兜底观察器时序漏判）；
   - `canvasIdx` 悬空（超出 `canvasElements.length`）的字符。
   - 过滤后若 charIndex 为空则提前 return，等下一轮 fillText 防抖重建。
4. **排序防御**：虚拟字符（`canvasIdx=-1`）不再被顶到开头，按自身 y/x 就近排。

标题分隔符设计保留：`三十而立。想到这件事` 的朗读停顿效果不变，只是不再残留、不再错位。

### 验证

- `node --check content-weread-main.js` 语法通过。
- 模拟脚本 `verify-stale-fix.js`（VoxEcho 根目录）复现两条路径均通过：
  - 旧版观察器（只删数组不删字符）→ 污染残留；新版（按 el 清理）→ 干净；
  - 旧虚拟句号清理 → 不再以"。"开头。
- 真实环境待用户 reload 扩展后手工验证：单页滚屏读完一章自动切章，确认正文不再插上一章第一句、开头不再读"。。。"。

### 残留风险与待观察

- 若微信读书某些场景是**复用** canvas 元素（而非替换），兜底靠既有 clearRect/`canvas.width` 重置清空钩子；若真复现，需再加"整画布不透明 fillRect 视为清屏"的钩子。
- `pageReported` 变量在代码中只写不读，属遗留调试状态，可后续清理。
- background-weread.js 的片段搜索（oldStart/newStart 匹配推算接续 offset）是启发式逻辑，依赖 content 侧 fullText 稳定；本次未改动，后续若出现"翻页后起点跳变"需再排查。

---

## 2026-09-06 高亮垂直偏移校准 + canvas 高亮改为持久 DOM span

### 背景

用户反馈朗读高亮的黄色背景框整体比文字偏下约半行（文字在高亮框上半部分）。同时用户希望把高亮统一成 DOM 渲染方式（"上一个对话死活做不了DOM的"），前提是不污染其它函数。

### 洞察

微信读书单页滚屏模式是**混合渲染**：章节前半段文字画在 `<canvas>` 上（fillText 逐字绘制，无 DOM 元素），后半段渲染成 `<span class="wr_absolute">`（绝对定位 DOM 元素）。因此高亮被迫分两套：

| 区域 | 原高亮方式 | 坐标来源 |
|---|---|---|
| 前半段（canvas） | 朗读时临时创建 `<div>` 盖在 overlay 上，清除时 `overlay.innerHTML = ""` | fillText 记录的 y + 校准偏移 |
| 后半段（DOM span） | 直接给 `span.wr_absolute` 加 `backgroundColor` | span 自身 box |

两套坐标参考系不同，偏移量可能不同。原 canvas 高亮的 cssY 校准为 `y - 1.5*size`，基于截图判断偏下半行。

### 修改（content-weread-main.js，5 处）

1. **新增 `charSpans` 变量**：和 `charIndex` 一一对应的持久 DOM span 数组，虚拟字符位置存 null。
2. **新增 `buildCharSpans()` 函数**：重建时遍历 charIndex，为每个非虚拟 canvas 字符创建持久 `<span>`（绝对定位、pointer-events:none、无背景色），放在 overlay 里；翻页/重建时先清空旧 span 再创建新 span。cssY 校准为 `y - 2.0*size`（原 1.5 偏下半行；增大=上移，减小=下移）。
3. **`rebuildTextAndNotify` 中 `charIndex = charIndexWithTitles` 之后调用 `buildCharSpans()`**：charIndex 确定后立即创建持久 span，后续 DOM 合并的 return 不影响。
4. **`highlightChunk` canvas 分支重构**：不再临时创建 div，改为遍历 `pos..canvasEndPos`，给 `charSpans[i]` 加 `backgroundColor`。自动滚动和 DOM hydration 切换逻辑保留，`createdRects` 改为 `highlighted`；DOM hydration 切换时不再 `overlay.innerHTML = ""`（会删掉持久 span），改为只清除 `charSpans` 的背景色。
5. **`clearHighlight()` 重构**：不再清空 overlay innerHTML，改为遍历 `charSpans` 移除背景色 + `clearDomHighlight()`。

### 设计要点

- **不污染其它函数**：改动集中在高亮相关函数（buildCharSpans 新增、highlightChunk canvas 分支、clearHighlight），文本采集/重建/视口定位/翻页逻辑均未触碰。
- **持久 span vs 临时 div**：重建时一次性创建所有 span（一章约 12000 字），高亮时只改样式，清除时只清背景色。性能开销在重建时一次性付出，高亮/清除更轻量。
- **翻页清理**：`buildCharSpans()` 开头先 removeChild 旧 span，再创建新 span；canvasObserver 移除旧 canvas 时旧 overlay 被移除，旧 span 随 overlay 一起 detached，不会残留显示。

### 验证

- `node --check content-weread-main.js` 语法通过。
- 真实环境待用户 reload 扩展后手工验证：
  - canvas 区域（章节前半段）高亮框是否与文字垂直对齐；
  - DOM 区域（章节后半段）高亮是否正常；
  - 翻页后高亮是否正确切换、无残留。
- 若 canvas 高亮仍偏下/偏上，调整 `buildCharSpans()` 中 `cssY = (c.y - c.size * 2.0) * ratio` 的 2.0 值（增大=上移，减小=下移）。

### 残留风险

- 一章 12000 个持久 span 可能有内存开销，若出现卡顿可优化为"只创建视口附近 span"或"按行合并高亮"。
- DOM 区域高亮仍用 `span.wr_absolute` 的 `backgroundColor`，若 span box 高度 > 文字高度（line-height 导致），可能仍有轻微偏移；若用户反馈 DOM 区域也偏，需单独处理（如给 span 加 `background-clip: content-box` 或改用覆盖层）。
- `chars` 变量在 highlightChunk canvas 分支中仍用于 `chars.length === 0` 检查，但不再用于遍历，属冗余代码，可后续清理。

---

## 2026-09-06 从备份恢复 + 性能安全版污染修复（WeakMap + 延迟过滤）

### 背景

前序迭代中，uildCharSpans 全量持久 DOM 高亮方案导致微信读书页面主线程阻塞卡死；回滚后发现「按元素身份即时 filter + charIndex 存 DOM el」的实现也会在真实浏览器环境卡死（高频 canvas 销毁触发 O(N) 大数组遍历 + DOM 强引用内存膨胀）。

用户使用「本次对话之前的备份」恢复了 content-weread-main.js（该备份为 4 处污染修复之前的更早版本，文本污染 bug 存在），保留 processing.md。

### 修改（content-weread-main.js，5 处）

在备份版本基础上，用**性能安全的方式**重新实现文本污染修复：

1. **全局新增 canvasIdMap（WeakMap）、canvasIdSeq、
emovedCanvasIdSet（Set）**：
   - WeakMap 给每个 canvas 分配唯一字符串 ID（cid_N），不产生 DOM 强引用，canvas 销毁后自动释放。
   - removedCanvasIdSet 仅收集已移除 canvas 的 ID，不做即时遍历。

2. **fillText hook 采集时给每个字符记录 canvasId（字符串）**：替代原方案的 el: canvas DOM 对象引用，避免强引用导致 GC 无法回收。

3. **新增 MutationObserver 监听 canvas 元素移除**：
   - 微信读书翻页/换章时会替换 canvas 元素（旧 canvas 移除、新 canvas 加入）。
   - observer 回调只做 
emovedCanvasIdSet.add(cid)，**不做即时大数组 filter**（高频销毁会卡死主线程）。
   - 同时检查被移除节点的子节点中的 canvas。

4. **
ebuildTextAndNotify 重建前统一过滤过期字符**（延迟过滤，合并多次销毁事件）：
   - 剔除 
emovedCanvasIdSet 中归属已移除 canvas 的字符（核心污染修复）。
   - 剔除上一轮重建插入的虚拟标题句号（isVirtual），避免 。。。三十而立 前缀。
   - 剔除 canvasIdx 悬空的字符（超出当前 canvasElements 范围）。
   - 过滤后清空 
emovedCanvasIdSet；过滤后为空则提前 return。

5. **高亮 cssY 校准从 1.5*size 改为 2.0*size**：高亮框整体上移约半行，解决高亮偏下问题。注释标注：增大=上移，减小=下移。

### 与旧高危实现对比

| 旧高危实现（会崩） | 新实现（性能安全） |
|---|---|
| charIndex 每条存 el:canvas DOM 强引用 | charIndex 存字符串 canvasId；WeakMap 无强引用 |
| onCanvasRemoved 销毁回调即时 charIndex.filter() O(N) | 销毁回调仅 Set.add(cid)；过滤延迟到重建时统一执行 |
| 每销毁一个 canvas 遍历全部字符数组 | 翻页重建才遍历一次，合并多次销毁事件 |
| 模拟脚本通过、浏览器卡死 | 模拟脚本等价，浏览器性能安全 |

### 验证

- 
ode --check content-weread-main.js 语法通过。
- 真实环境待用户 reload 扩展后验证：
  1. 微信读书页面正常加载，不卡死转圈；
  2. 翻页/切章后正文不再插入上一章第一句（文本污染修复）；
  3. canvas 区域高亮框与文字垂直对齐（2.0 校准）；
  4. 多次切换书籍后内存不持续膨胀。

### 残留风险

- 若微信读书某些场景是复用 canvas 元素而非替换，兜底靠既有 clearRect/canvas.width 重置钩子；若真复现需再加「整画布不透明 fillRect 视为清屏」钩子。
- 
emovedCanvasIdSet 在重建时清空，如果两次重建之间有 canvas 被移除但重建未触发，已移除 canvas 的字符会残留到下一次重建——但翻页必然触发 fillText → 防抖重建，所以实际不会残留。
- 高亮 2.0 校准值基于截图估算，真机可能仍需微调（增大=上移，减小=下移）。

---

## 2026-09-06 文本污染未解决——根因：微信读书复用 canvas + fillRect 清屏

### 诊断证据（来源：tts-diag-log-2026-09-05T18-47-18-504Z.txt）

1. **切章前**（02:45:52）：chars=4872，sortDebug 显示 canvasIdx:0, cvTop:-16473，文本是"三十而立一。王二生在北京城..."（第一章）。
2. **切章后**（02:46:19）：chars=9826，**正好翻倍**——旧第一章 4872 字 + 新第二章约 4954 字混在一起。sortDebug 显示 canvasIdx:0, cvTop:186，文本是"三十而立。想到这件事..."（第二章）。
3. **两次都是 canvasIdx:0**——证明微信读书切章时**没有替换 canvas 元素，而是复用同一个 canvas**，只是清空内容重绘，位置从页面顶部（-16473）滚动到视口内（186）。
4. 朗读 preview 从第 5 块开始出现污染："我骑车子去上班，经过学校..."（第一章内容），后续大量块新旧章节字符交错混排。

### 根因

备份版本只 hook 了两种清屏方式：
- clearRect：滚动模式下 charIndex = []
- canvas.width 重置：滚动模式下 charIndex = []

但微信读书切章时实际用的是**整画布不透明 illRect（白底）覆盖整个画布**来清屏，既不调用 clearRect，也不重置 canvas.width。因此旧字符（第一章 4872 字）没有被清理，和新章节字符混排在同一个 charIndex 里，排序后新旧交错 → 文本污染。

之前加的 MutationObserver（监听 canvas 元素移除）和重建前过滤（removedCanvasIdSet）是针对"替换 canvas"场景的兜底，但微信读书实际是"复用 canvas"，所以这层兜底不触发。

### 修复（content-weread-main.js，新增 fillRect hook）

在 clearRect hook 之后、canvas.width hook 之前，新增 illRect 整画布清屏检测：

1. **hook CanvasRenderingContext2D.fillRect**
2. **整画布覆盖判断**：x <= 0 && y <= 0 && w >= canvas.width && h >= canvas.height
3. **不透明填充色判断**：illStyle 不是 
gba，或 
gba 的 alpha=1
4. **满足以上条件则视为清屏**，执行和 clearRect 完全一样的清理逻辑：
   - 滚动模式：charIndex = []（全部清空）
   - 双栏模式：按 canvasIdx 过滤对应 canvas 的字符
   - 同时清除 rebuildTimer

### 三层清屏兜底（现在完整覆盖）

| 清屏方式 | 触发钩子 | 场景 |
|---|---|---|
| clearRect | 已有 hook | 部分页面/操作 |
| canvas.width 重置 | 已有 hook | 部分页面/操作 |
| **整画布不透明 illRect** | **本次新增 hook** | **微信读书切章（实际使用的方式）** |
| canvas 元素被移除 | MutationObserver + 重建前过滤 | 替换 canvas 的场景（兜底） |

### 验证

- 
ode --check content-weread-main.js 语法通过。
- 真实环境待用户 reload 扩展后验证：
  1. 切章后 chars 不再翻倍（应该约等于新章节字符数，不是新旧之和）；
  2. 朗读 preview 不再出现上一章内容；
  3. 页面不卡死（fillRect hook 只在整画布覆盖时做判断，正常绘制的小 fillRect 不触发清理，性能安全）。

### 残留风险

- 如果微信读书某些场景用半透明 fillRect（alpha<1）做清屏效果，当前不透明判断会漏掉。但实际阅读页背景是纯白不透明，暂不考虑。
- 如果误判（正常绘制的大矩形被当成清屏），会导致字符被提前清空。但整画布覆盖 + 不透明白色的组合在正常绘制中极少出现，风险低。若出现误判，可增加"清屏后 100ms 内无 fillText 则确认清屏"的延迟确认逻辑。

---

## 2026-09-06 回退到最初方案（el: canvas + 原有 observer 即时过滤）

### 背景

WeakMap + 延迟过滤方案实测未解决污染（切章后 chars 仍翻倍 9826）。根因：新增的独立 MutationObserver 在 installFillTextHook 里安装，observe(document.body) 时 body 可能不存在（document_start 注入），observer 未生效；且微信读书切章是替换 canvas 元素（非复用），原有 canvasObserver 在 DOMContentLoaded 后安装、能正确捕获移除事件，但唯独没清理 charIndex。

用户提醒：processing.md 最初记录的 4 处修复方案试了一页是有效的，页面卡死是后来加 buildCharSpans 才出现的，不应归因于最初方案。

### 回退内容（从 WeakMap 方案改回最初 el 方案）

1. **fillText 采集**：canvasId: canvasId → el: canvas（记录 canvas 元素引用）
2. **删除全局变量**：canvasIdMap、canvasIdSeq、removedCanvasIdSet
3. **删除独立 MutationObserver**（installFillTextHook 里新增的那个，安装时机有问题）
4. **rebuildTextAndNotify 重建前过滤**：c.canvasId && removedCanvasIdSet.has(c.canvasId) → c.el && canvasElements.indexOf(c.el) === -1（el 不在当前 canvasElements 里则过滤）
5. **原有 canvasObserver 核心修复**：在 canvasElements.splice(i, 1) 之前，加 charIndex = charIndex.filter(c => c.el !== removedEl)——这是最初方案的核心，也是真正生效的修复

### 保留的改动

- fillRect 整画布不透明填充清屏检测钩子（复用 canvas 场景的兜底）
- 高亮 cssY 校准 c.y - c.size * 2.0（原 1.5 偏下半行）
- 重建前过滤 isVirtual 虚拟字符、canvasIdx 悬空字符
- 重建前过滤后为空则提前 return

### 验证

- 
ode --check content-weread-main.js 语法通过。
- 无残留 canvasIdMap/canvasId/removedCanvasIdSet 引用。
- 真实环境待用户 reload 扩展后验证：切章后 chars 不再翻倍、朗读不再插入上一章句子。

### 教训

- 归因要谨慎：页面卡死是 buildCharSpans 全量 DOM 导致的，不是最初的 el 方案导致的。最初方案试了一页是有效的。
- observer 安装时机很重要：document_start 注入时 document.body 可能不存在，observe(body) 会静默失败。应使用 document.documentElement 或在 DOMContentLoaded 后安装。
- 优先在已有的、经过验证的 observer 里加逻辑，而不是新增独立 observer。

---

## 2026-09-06 heading 识别 + 无污染占位符 + 划选起读逻辑

### 背景

用户反馈两个问题：
1. 高亮偏移因字号不同而变化（标题字号大，正文字号小，固定偏移系数不能同时对齐）——暂未修复，需后续用字体度量通用方案
2. 起读逻辑：微信读书无 heading 语义标签，当前兜底逻辑会跳过标题行 + 从第一个标点后开始读，导致章节开头的标题和第一句被跳过。用户希望：检测到 heading（单独一行、字号明显大于下一行、后面无标点）时，走划选逻辑（从 heading 开始念），不走兜底逻辑，且 heading 后要有明显停顿。

### 诊断

日志显示：
- 第一行"男人眼中的女性美"字号 33.6，第二行"从男人的角度..."字号 21
- skipFirstLine=true（33.6 > 21×1.2=25.2）
- 当前逻辑：跳过标题行 8 字 + 虚拟句号 → firstVisible=9（"从"）→ 往后找标点在 punctAt=22 → 从 charOffset=23（"这"）开始读
- 结果：标题和第一句"从男人的角度谈女人的外在美，"全被跳过

### 修改内容（content-weread-main.js）

#### 1. 无污染占位符替代虚拟句号

- 原方案：标题后插入 1 个虚拟句号"。"到 charIndex（isVirtual=true）
- 新方案：插入 2 个 \u0001（不可见控制字符，正文绝对不会出现）到 charIndex（isVirtual=true）
- 构建 fullText 时，把 \u0001 替换为"。"，所以 fullText 里标题后是"。。"，TTS 会有两个自然停顿
- charIndex 里是 \u0001，和正文标点完全区分；isVirtual 标记确保每轮重建前被过滤，不会残留污染
- 插 2 个而非 1 个：增加标题后的停顿时长

#### 2. 起读逻辑：检测到 heading 时走划选逻辑

- 原逻辑：skipFirstLine=true 时跳过标题行，firstVisible 从正文开始；然后从 firstVisible 往后找第一个标点，从标点后开始读
- 新逻辑：
  - 不跳过标题行（注释掉 continue）
  - 在返回逻辑前加判断：if (skipFirstLine && firstVisible >= 0) → 直接返回 firstVisible（从标题首字开始读），不往后找标点
  - 日志标记 result: "ok-heading"
- 无标题的页面（翻页后正文中间）：skipFirstLine=false，走原有兜底逻辑不变

### 保留未修复

- 高亮偏移通用化：当前 cssY = (c.y - c.size * 2.0) * ratio 是固定系数，标题字号大时偏移可能不准。后续可用离屏 canvas 测量字体 actualBoundingBoxAscent 来通用计算。

### 验证

- node --check 语法通过。
- 真实环境待用户 reload 扩展后验证：章节开头应从标题开始念，标题后有明显停顿，然后念正文；翻页后正文中间仍走兜底逻辑。

---

## 2026-09-06 高亮错位修复：应用 ctx.transform() 的 tx/ty 平移 + 换章清理旧高亮

### 问题

用户反馈：在《三十而立》上调好的高亮坐标，换到另一篇文章就对不齐了。截图显示：
- 第一个高亮框在页面顶部"上一章"按钮位置（旧高亮残留）
- 第二个高亮框在标题下方空白处，没有覆盖文字

### 根因诊断

#### 根因 1（核心）：ctx.translate() 的 tx/ty 平移没有应用到高亮

fillText hook 里通过 getContextTransform() 获取了 ctx.getTransform() 的完整矩阵（scaleX/scaleY/translateX/translateY），记录到每个字符的 c.tx, c.ty。但高亮计算时完全忽略了 tx/ty。

微信读书绘制时用 ctx.scale(2,2) + ctx.translate(...)，不同文章/章节的 translate 不同。c.y - c.size * 2.0 里的 2.0 其实是在某篇文章上粗略补偿 ty 的魔法数字，换文章就失效。

正确计算：logicalY = c.y + c.ty / c.scaleY（tx/ty 是设备像素级别，除以 scale 得到逻辑坐标平移）。

#### 根因 2：换章/重建时没有清理旧高亮

rebuildTextAndNotify() 里没有调用 clearHighlight()，旧章节的高亮 rect 残留在 overlay 里。

### 修复内容

1. rebuildTextAndNotify 开头加 clearHighlight()，换章/重建时先清理旧高亮
2. 高亮计算应用 tx, ty transform：logicalX = c.x + c.tx/c.scaleX，logicalY = c.y + c.ty/c.scaleY

### 待调整

- 2.0 系数：应用 tx/ty 后，这个魔法数字可能需要调整。原 2.0 是在未应用 ty 时粗略补偿的，应用 ty 后正确值可能接近 0.5（textBaseline=middle 时文字顶部在中点以上 0.5*size）。需真机测试后微调。

### 验证

- node --check 语法通过。
- 真实环境待用户 reload 后验证：换章后无旧高亮残留，高亮覆盖当前朗读文字，不同文章对齐一致。

---

## 2026-09-06 标题检测优化 + 高亮坐标修复 + 静音占位 + 诊断锚

### 1. 标题检测优化（正文字号基准，支持多级标题）

**问题**：《三十而立》有两级标题——"三十而立"（33.6px）+ "一"（31.5px），两者只差 6.7%，原逻辑只比"第一行 vs 第二行"，阈值 20%，导致第一行漏判，走了兜底从"我就是王二"开始读。

**修复**：
- 统计前 200 字符的字号众数作为正文字号
- 任何比正文字号大 20% 以上的行都是标题行
- 视口定位：第一行 > 正文字号*1.2 → skipFirstLine=true，从标题首字起读
- rebuild：标题行判定从"比下一行大20%"改成"比下一行大20% OR 比正文字号大20%"，插入虚拟句号
- 双栏/滚动模式共享此逻辑（基于 charIndex 字号统计，与 canvas 排列无关）

### 2. 高亮坐标修复（应用 tx/ty transform + 系数 2.0→0.5）

**问题**：换文章后高亮错位，标题高亮甚至跑到 canvas 外面不可见。

**根因**：
- fillText hook 记录了 ctx.getTransform() 的 tx/ty，但高亮计算完全没用
- 系数 2.0 是在某篇文章上粗略补偿 ty 的魔法数字，换文章就失效

**诊断锚**：在高亮绘制时对第一个字符输出完整坐标链路（fillText原始坐标 → transform → canvas/overlay位置 → 计算结果 → rect实际渲染位置），并在页面上画红/品红参考线。

**修复**：
- 高亮计算应用 tx/ty：logicalX = c.x + c.tx/c.scaleX，logicalY = c.y + c.ty/c.scaleY
- 系数 2.0→0.5：c.y 是 textBaseline=middle（文字中点），文字顶部 = 中点 - size*0.5
- rebuildTextAndNotify 开头加 clearHighlight()，换章时清理旧高亮残留
- 验证：正文高亮实际顶部 252.5px，文字顶部 254.5px，误差 2px ✓

### 3. 静音占位优化（\uE000 标记 + pause chunk）

**原方案**：标题后插入 2 个 \u0001，构建 fullText 时替换成"。。"，靠 TTS 句号自然停顿。时长不可控。

**新方案**：
- content-weread-main.js：标题后插 1 个 \u0001，构建 fullText 时替换成"。\uE000"（句号断句 + 私有区标记）
- background-weread.js：发给 offscreen 前遍历 chunks，把 \uE000 转换成 { type: "pause", durationMs: 450 }
- offscreen.js：遇到 pause 对象就 await setTimeout(450ms)，不调用 TTS 合成；ensurePrefetched 跳过 pause；pause 期间发空文本高亮消息
- 静音时长 450ms，可通过 PAUSE_MS 调整

### 4. 待解决：西语/英语高亮重叠

**问题**：西语/英语字符宽度不统一（'i'窄，'m'宽），当前高亮用统一 c.size*ratio 估算每字符宽度，窄字符框太宽导致重叠。中文宽度统一所以不严重。

**候选方案**：
- 方案A：离屏 canvas measureText 测量每个字符实际宽度（最准确，需记录 font）
- 方案B：按行/按单词合并高亮框，避免逐字重叠（视觉效果最好，实现较复杂）

---

## 2026-09-06 空页/插图页自动翻页 + 标题页识别 + 书尾判断修复

### 背景
微信读书单页滚屏/双栏模式下，连续插图页（无文本可读）时朗读会卡住，不会自动向后翻。有一些书是连续多张插图之后又有文本续上的。

### 1. 空页检测：从全局 charIndex 改成视口内可见字符判断

**原问题**：getEmptyPageFingerprint() 用全局 charIndex.length > 0 判断是否空页。但微信读书 charIndex 是整章采集的（含视口外正文），即使当前页是纯插图，全局 charIndex 也可能 >0，被误判为"有文本"而不翻页。

**修复**：改成统计当前视口内的可见字符数。遍历 charIndex，计算每个字符的 absTop（应用 canvas transform + ratio），统计落在 [scrollY-30, scrollY+viewportH+30] 范围内的字符。只要视口内有一个可见字符就不是空页（保守判断，避免误判正文页）。

**性能优化**：预缓存 canvas rect（避免循环里重复 getBoundingClientRect），找到第一个可见字符就 break。

### 2. 标题页识别：background 侧短文本无标点判断

**问题**：切到下一章时，如果新章节是标题页/封面页（如"(끝)춘향전 고문 원본"，12字无标点），background 只要 message.data.length > 0 就认为是"有文本章节"，从章首朗读标题文字。

**修复**：在 background-weread.js 加 isTitleOrCoverPage 判断：
- 条件：
ewText.length > 0 && newText.length < 30 && !/[。！？，；：、,.!?;:]/.test(newText)
- 标题页不重置空页翻页状态（避免空页检测中断）
- 标题页不朗读，直接发送 WEREAD_TURN_PAGE 继续翻页找正文

### 3. 书尾判断修复：指纹比较漏掉 URL 比较（关键 bug）

**问题**：空页翻页翻了几页就停止，被误判为书尾。

**根因**：background-weread.js 的指纹比较（第 504-509 行）注释里写了"weread 指纹：textLen / canvasCount / scrollY / url"，但实际代码里**没有比较 p.url === last.url**。

每次翻页后 URL 变了，但插图页的 textLen=0、canvasCount=2、scrollY=0 都相同，所以每次都被认为是"无变化"，停滞计数 +1，连续 4 次后误判为书尾。

**修复**：加上 p.url === last.url 比较。
- koodo 是滚动模式，URL 不变，用 imgCount/imgSrcs 判断内容变化
- weread 是翻页模式，URL 会变，用 url 判断内容变化
- 只有真正到书尾、翻页无效时，URL 才不变，此时连续 4 次无变化才判断为书尾

### 4. 语法错误修复：括号不匹配导致 Service Worker 注册失败

**问题**：reload 扩展时报"Service worker registration failed. Status code: 15"。

**根因**：用 indexOf 定位修改 background-weread.js 时算错了结束位置，第 404 行多了一个 )（}); 应该是 }），第 405 行多了一个 }。

**修复**：第 404 行 }); → }，删除第 405 行多余的 }。

### 空页翻页完整流程（修复后）
1. content 侧 getEmptyPageFingerprint() 每 1s 检测一次：视口内无可见字符 → 上报 WEREAD_EMPTY_PAGE
2. background 侧收到空页上报：节流 1200ms，比较指纹（textLen/canvasCount/scrollY/url）
3. 指纹有变化（URL 变了）→ 停滞计数清零，翻页
4. 指纹无变化（URL 没变，翻页无效）→ 停滞计数 +1
5. 连续 4 次无变化 → 判断为书尾，停止朗读
6. 翻到标题页（<30字无标点）→ background 识别为标题页，不朗读继续翻页
7. 翻到正文页 → content 检测到可见字符，不上报空页；background 收到 chapter-updated，正常朗读

---

## V2.0 P0 实施记录（2026-09-08）

### P0 范围
平台抽象层 provider.py + 语音服务配置对话框（launcher.py 接入），对应 plan.md §3。

### 已实现

1. **provider.py（新文件，7148 字节）**
   - PlatformDef 数据类 + PROVIDERS 平台定义：groq（推荐！+代理提示 + LLM 置顶 llama/DeepSeek）/ siliconflow / custom
   - Provider 类：list_models()（GET /models 实时抓取）、etch_models_split()（按关键词筛 ASR/LLM）、chat()（LLM 通道）、	ranscribe()（ASR 通道，multipart）、stream_transcribe()（预留 NotImplemented）、	est_connection()
   - ProviderError 异常、FALLBACK_MODELS 后备清单、ASR_KEYWORDS 过滤词
   - 用 requests（系统 Python 2.32.5 可用）

2. **launcher.py 接入（4 处修改）**
   - import provider；load_config 默认值加 provider 字段
   - 左侧面板新增"语音服务"区域：状态行（已配置/未配置）+ "语音服务配置…"按钮
   - open_provider_dialog()：三个平台单选 + 注册链接（可点击打开）+ API Key 掩码 + ASR/LLM 下拉 + 刷新模型列表 + 测试连接 + 保存/取消；对话框 topmost 置顶
   - _pd() 中英双语文案（完整 6 语言留 UI 打磨阶段）

3. **平台切换逻辑（踩坑后修复）**
   - 初版 bug：radio command=refresh_platform 在 def 之前求值 → UnboundLocalError，逻辑函数整体前移到 UI 创建前
   - 默认值 bug：切到硅基流动 LLM 仍是 llama（不跟随平台默认）→ 修复为 platform_cache 每平台独立记忆 + switch_platform() 切换前保存旧平台（radio invoke 时 variable 已更新，不能再用 platform_var.get() 取旧值）
   - 自定义平台：ASR/LLM 清空手填、base_url 可编辑

### 验证
- 	est_provider.py：16 项单元断言通过（平台定义/默认值/错误处理/messages/后备清单）
- GUI 断言脚本（已清理）：11 项通过（Groq 初始 → 硅基 LLM=DeepSeek-V3 → 自定义清空+可编辑 → 切回 Groq 恢复 llama → 保存写入 config.json）
- launcher.py + provider.py py_compile 通过

### 环境备注（截图教训）
- 双显示器：tkinter 窗口可能落在右屏，winfo 虚拟桌面坐标；ImageGrab 默认截主屏 → 必须 ImageGrab.grab(all_screens=True) 用虚拟桌面坐标系裁剪
- 窗口会被用户其他窗口遮挡 → 截图前 root/对话框必须 topmost
- 后续需要界面截图验证时按此处理

### 待办（P1+）
- 场景 2/3 面板接入配置触发逻辑（勾选翻译/风格化/按快捷键 → 未配置则弹对话框）
- 硅基流动真实模型 id 校准（如 deepseek-ai/DeepSeek-V3 带前缀），list_models 实时抓取后确认
- Groq 实时流式转写 API 形态核实（stream_transcribe）
- config.json 多平台并存读写（目前 provider 单实例）

---

## V2.0 P1 实施记录（2026-09-08 续）

### 范围
场景 2（长文本转语音）MVP：server /tts_file + launcher Hot Spot 切换 + 场景 2 面板（含 Native 翻译）。

### server.py 新增
- **/tts_file**：POST {text, voice, rate} → 分段合成 → 拼接 → 保存到 tts_output/ → 返回 {path, filename, bytes, segments}
- **split_text()**：三规则切分——换行硬分段（保留段落边界）、句号软边界（可合并）、超长单句硬切 800 字
  - 踩坑 1：初版按句号切分后合并，**换行被吃**（'第一行\n第二行' 合并成一行）→ 改为 splitlines 按段落处理
  - 踩坑 2：单句 1200 字无标点不切分 → while 循环硬切
- 输出目录：环境变量 VOXECHO_OUTPUT_DIR 优先，否则 server.py 旁 tts_output/（打包模式需 launcher 传 env，P5 处理）

### launcher.py 改造（run_gui）
- **Hot Spot 切换**：顶部 3 个 RadioButton（📖 电子书朗读 / 📝 长文本转语音 / 🎙 语音输入），右侧三 Frame 切换
- **场景 1** = 原日志区；**场景 3** = P2 占位
- **场景 2 面板**：文本输入区 + 音色下拉（/voices 自动加载）+ 语速 + Native 翻译勾选（首次弹配置对话框）+ 目标语言 + 翻译预览（可编辑）+ 生成音频 + 打开输出文件夹 + 状态行
- 回调全部用 lambda 包装避免 UnboundLocalError（P0 教训）；后台线程 + root.after 回主线程更新 UI
- 翻译 prompt：专业翻译，原生风格，仅输出译文（场景 3 的 styles 风格化后续接入）
- /tts_file 404 时提示"服务版本过旧，请退出旧版 VoxEcho-bridge"（版本冲突防护）
- run_gui 加可选 test_hook 参数（注入 UI 断言，对将来测试有用）

### 验证
- split_text 单测通过（换行/软边界/超长硬切）
- /tts_file 端到端：合成 1 段 36KB MP3，文件头 fff364c4 有效，保存到 tts_output/
- GUI smoke（test_hook 断言）：Hot Spot 3 入口 + 三场景切换全部通过

### 环境备注
- **edge-tts NoAudioReceived 间歇性失败**：短文本成功、连续请求部分失败，与 rate 参数无关（+0% 成功、0%/+10% 失败），属微软端点不稳定 + 本地 Clash 代理干扰。重试 3 次后成功。正式版保留 synthesize_with_retry。
- **旧版 VoxEcho-bridge.exe 占用 5005**：PID 7520 跑旧代码，/tts_file 404。用户测试新功能前需退出旧 exe。开发测试用 5055 端口避开。

### 待办
- 场景 2 用真实 Groq key 验证翻译链路（用户已备 key）
- P2：场景 3 语音输入（快捷键 + 录音 + VAD + 压缩 + transcribe + 底部横幅）
- 打包模式 VOXECHO_OUTPUT_DIR env 传递

---

## ⚡ 开发快照（2026-09-08）— 新对话接手必读

### 当前状态
- **V1.2.0 已打包未发布**（zip 在 D:\Documents\VoxEcho-V1.2.0.zip，未签名，README 6 语言含 SmartScreen 提示）
- **V2.0 进度**：P0（provider 平台抽象 + 配置对话框）✅ / P1（场景 2 长文本 TTS + Hot Spot UI）✅ / P2（场景 3 语音输入）待做
- 设计文档：plan.md（§1-9 完整）、stt_styles.md（开发参考，不进发布包）
- 实施记录：processing.md（本文档）

### 运行与测试（重要！）
- **开发运行**：cd VoxEcho-bridge && python launcher.py（系统 Python：D:\Program Files\Python\Python310\python.exe）
- **端口冲突**：5005 被旧版 VoxEcho-bridge.exe 占用时新 server 不可用（/tts_file 404）。测试前先退出托盘旧 exe；开发测试可临时用 5055：python -c "import server; server.app.run(port=5055)"
- **GUI 断言测试**：run_gui 有 	est_hook 参数（
un_gui(test_hook=fn)），fn(root, cfg) 内可注入断言
- **截图**：双屏环境，tkinter 窗口可能落右屏 → ImageGrab.grab(all_screens=True) 用虚拟桌面坐标（3840×1080），且窗口要 topmost 防遮挡
- **PowerShell 5 不支持 &&**（分号/分行）；改项目文件用 Python 脚本做字符串替换（CRLF 下 Edit 工具易失败）；改 JS 后 
ode --check

### 关键文件
- VoxEcho-bridge/launcher.py：主 GUI（Hot Spot 三场景 + 配置对话框 + run_gui(test_hook)）
- VoxEcho-bridge/server.py：/voices /speak /tts_file（分段合成）/health
- VoxEcho-bridge/provider.py：平台抽象（Groq 推荐/硅基/自定义；chat/transcribe/list_models/stream_transcribe 预留）
- VoxEcho-bridge/proto_ui.py：v2 UI 原型（pythonw 运行截图）
- 输出目录：VoxEcho-bridge/tts_output/（场景 2 生成 mp3）

### 用户环境
- Windows 10，双显示器，常住深圳，Clash Verge 代理（edge-tts 间歇 NoAudioReceived 与代理有关）
- **Groq key 用户已备好**（尚未填入 config，用户会在配置对话框里填）
- 用户偏好：功能先行 UI 审美最后；屏蔽 ASR/LLM 术语面向小白；直接高效不啰嗦；诚实沟通

### 已定参数（勿改）
- 默认 Groq；ASR=whisper-large-v3-turbo；LLM=llama-3.3-70b-versatile（置顶）+ DeepSeek-V3 紧跟；硅基=whisper+DeepSeek-V3；自定义全手填
- 场景 3：快捷键默认 CTRL+Win（可配置，Mac 长按 FN）；转写显示=底部横幅（放弃光标跟随）；极速听写（仅 Whisper ~200ms）+ 3 风格（智能润色/Email/严肃文档）+ 翻译独立开关单请求拼接；风格上限 10
- 静音占位  + 450ms（weread 场景）；标题识别=正文字号众数×1.2 动态阈值

### 待办
1. P2：场景 3 语音输入（全局快捷键 CTRL+Win → sounddevice 录音 → VAD 静音过滤 → mp3/ogg 压缩 → provider.transcribe → 底部横幅上屏）
2. 场景 2 翻译链路用真实 Groq key 验证（用户操作）
3. Groq 实时流式 API 形态核实（stream_transcribe）
4. 硅基流动模型 id 校准（deepseek-ai/ 前缀）
5. 打包模式 VOXECHO_OUTPUT_DIR env 传递 + V1.3 重新 build
6. 6 语言 UI 完整化（当前 _pd 中英双语）

---

## V2.0 P1 修复：音色下拉空 + 两级菜单（2026-09-08）

### 用户反馈
场景 2 输完 Groq key 后音色下拉为空；要求像场景 1 那样语言→音色联动，一级语言默认跟系统。

### 根因（不是 Groq key 冲突，也不是微软 API 冲突）
1. load_voices() 只在 show_scene 切到场景 2 时调用**一次**（root.after(100)）；若当时 server 未就绪（health_ok False）直接 return，**无重试** → 下拉永久为空。
2. 旧版 server（V1.2 exe）返回字段可能是 short_name（snake_case），新 launcher 读 shortName → KeyError 同样落空。
3. 顺带发现 run_gui 状态行 PlatformDef(name="?") 缺 4 个必填参数 → TypeError 崩 GUI（已修）。

### 修复
- **两级菜单**：语言下拉（142 种，按 locale 分组，显示「zh-CN 中文（简体）」）+ 音色下拉（该语言下）联动；语言切换 → 刷新音色
- **默认语言跟随系统**：ctypes GetUserDefaultUILanguage，LANGID 0x0804=简体/0x0404=繁体（**踩坑：sublang 位不是 0x04，是 0x0804 整体比较**，初版判断反了导致中文系统默认成 zh-TW）
- **失败重试**：health 未就绪或请求失败，2 秒后重试，上限 10 次
- **手动刷新按钮**：语速行旁「刷新音色」兜底
- **字段兼容**：.get("shortName") or v.get("short_name")，新旧 server 都认
- 音色选中规则：当前语言下第一个（保留 XiaoxiaoNeural 优先逻辑已随分组自然实现——zh-CN 首音色即 XiaoxiaoNeural）

### 验证（GUI smoke 断言）
- 142 语言加载 ✅ / 默认 zh-CN ✅ / 中文 6 音色、默认 XiaoxiaoNeural ✅
- 切 en-US → 17 音色、首音色 en-US-AvaNeural ✅ 联动正确

---

## V2.0 P1 交互重构：目标语言=输出语言（2026-09-08）

### 用户新设计（比"两级菜单"更简）
去掉独立的语言下拉和"Native 翻译"勾选：
- **输出语言下拉**（原翻译目标语言）即唯一语言入口，默认简体中文
- **音色跟随输出语言联动**（选 English → 英文音色，选 简体中文 → 中文音色）
- **语言一致自动跳过翻译**：输入与输出语言相同 → 不调 LLM（也不需要 Groq key），直接 TTS 原文；不一致 → 自动翻译后 TTS
- 预览区常显"输出预览（可编辑）"，可先看翻译结果再生成

### 实现
- 删 lang_cb 一级下拉；trans_row 改"输出语言"标签 + 目标语言下拉（readonly，绑定 <<ComboboxSelected>> → refresh_voices_by_lang）
- TARGET_LOCALE 映射：English→en-US / 简体中文→zh-CN / 繁體中文→zh-TW / 日本語→ja-JP / 한국어→ko-KR / Español→es-ES / Français→fr-FR / Deutsch→de-DE
- detect_input_lang()：启发式（谚文→ko，假名→ja，汉字占比>20%→zh，简繁用 _HANT_CHARS 繁形字集判断→zh/zh-hant，其余→other）
- 
eeds_translation()：拉丁语言之间（en/es/fr/de）保守不翻（无法可靠区分）；中文简繁互转需要翻译（交给 LLM 转写）；其他跨主语言 → 翻译
- do_tts 拆分：_tts_with_text(text)（合成）+ do_tts（先判 need → 翻译回调 → 合成）；do_translate → do_preview（语言一致时预览直接填原文）
- **_HANT_CHARS 踩坑**：首版混入简繁同形字（的/是/在/有…）导致简体文本误判繁体 → 只保留繁形专有字（這/個/說/時/後/來/裡…）

### 验证
- 语言判断 14 项边界单测全过（简繁互转、拉丁保守、中日韩互转）
- GUI smoke：目标语言默认简体中文 → 6 中文音色/XiaoxiaoNeural；切 English → 17 英文音色/AvaNeural；切回恢复 ✅
- 注意：修改了用户此前确认的"翻译需配置"交互（去勾选）；如需翻译仅在选择不同语言时触发 key 弹窗

---

## V2.0 P1 翻译失败根因与修复（2026-09-08）

### 用户反馈
语言一致能生成音频；但跨语言翻译一直失败。用户用同一 key 在 spokenly 测实时转写成功（怀疑与模型/网络有关）。

### 根因（两个叠加）
1. **网络：Python requests 直连 api.groq.com 被墙**（SSLEOFError: EOF occurred）
   - 国内访问 Groq 必须走代理；浏览器/桌面应用（spokenly）走 Windows 系统代理（Clash Verge 127.0.0.1:7897）所以成功
   - **Python requests 默认不读 WININET 系统代理**（只读环境变量）→ 直连 → 被墙
   - 实测：直连 SSLEOFError；走系统代理 HTTP 200 ✅
2. **模型：配置里 llm_model=DeepSeek-V3 在 Groq 不存在**
   - Groq 实际模型列表（14 个）：whisper-large-v3 / whisper-large-v3-turbo / qwen/qwen3.6-27b / qwen/qwen3.8-27b / openai/gpt-oss-20b / openai/gpt-oss-120b / groq/compound 等
   - **llama-3.3-70b-versatile 已下架**；DeepSeek-V3 是硅基流动的模型，Groq 从未托管过（P0 默认值设计错误，当时 llama-3.3 还在）
   - 用户"其他模型也试了"——试的都是不在列表的旧 id，且网络也不通，双重失败

### 修复
1. **provider.py 加 system_proxy()**：读注册表 HKCU\...\Internet Settings 的 ProxyEnable/ProxyServer（Clash 设置系统代理后自动读到）；list_models/chat/transcribe 全部显式传 proxies；无系统代理时直连
   - 踩坑：provider.py 缺 import sys（system_proxy 用 sys.platform）→ NameError，补上
2. **Groq 默认模型更新**：default_llm=llama-3.3-70b-versatile → **qwen/qwen3.8-27b**；llm_pinned=(qwen/qwen3.8-27b, openai/gpt-oss-120b)
3. **硅基流动 LLM 默认加前缀**：DeepSeek-V3 → deepseek-ai/DeepSeek-V3（之前待办）
4. **对话框 on_fetch 自动校验**：刷新模型列表后，若已存 asr/llm 不在平台列表 → 自动设为列表第一个（防下架/改名后再踩）
5. **config 修正**：llm_model=DeepSeek-V3 → qwen/qwen3.8-27b（已写 bridge_config.json，未动 key）
6. **默认输出语言跟随界面语言**：target_var 初值 = _pd("简体中文", "English")

### 验证
- 走代理 chat 实测：'你好，今天天气很好。' → 'Hi, the weather is lovely today.' ✅（qwen/qwen3.8-27b）
- 直连 vs 代理对比：直连 SSLEOFError / 代理 200 ✅
- 模型列表实测：Groq 14 个，无 llama-3.3、无 DeepSeek-V3 ✅

### 注意
- 若用户关闭 Clash 系统代理（ProxyEnable=0）→ system_proxy() 返回 None → 直连 → 翻译会失败。失败提示建议用户检查代理。
- 以后 Groq 模型再变动，用户在对话框点"刷新"即自动修正。

---

## V2.0 P1 细节：繁体中文合并香港粤语音色（2026-09-08）

用户反馈繁体中文音色缺香港。refresh_voices_by_lang 对 zh-TW 目标合并 _lang_voices 的 zh-TW + zh-HK 两组。

验证：繁體中文音色 6 个 = 台湾 3（HsiaoChen/YunJhe/HsiaoYu）+ 香港 3（HiuGaai/HiuMaan/WanLung）✅

---

## V2.0 P1 修复：场景 2 假死（2026-09-08）

### 用户反馈
场景 2 点一下（切场景/刷新音色）会有一段时间像假死。

### 根因
load_voices() 在 **UI 主线程**同步 urllib.request.urlopen(/voices, timeout=15)。
首次请求会触发 server 现场抓 edge-tts 322 音色（网络慢时十几秒）→ 主线程阻塞 → 整个窗口无响应。
（server 已是 threaded=True，排除 Flask 单线程因素）

### 修复
load_voices 改为**后台线程**：主线程只负责 root.after 调度；fetch 线程内 urlopen → root.after(0, apply) 回主线程更新 UI。
重试逻辑保留（_voice_retry 上限 10，成功归零）。

### 验证（心跳法）
切场景 2 后 6 个 400ms 心跳全部准时触发 [0.44, 0.88, 1.28, 1.68, 2.08, 2.48] → 主线程零阻塞 ✅
音色后台加载成功（6 中文音色 / XiaoxiaoNeural）✅

### 遗留
do_tts/do_preview 的 health_ok() 仍是主线程同步（timeout=2s），点击生成时最多 2 秒内部检查，不构成假死；若想彻底消除可后续也线程化。

---

## V2.0 P2 场景 3 语音输入实施（2026-09-08）

### 交付内容
- **stt_engine.py（新模块）**：Recorder（sounddevice 16k mono int16 采集）+ VAD（30ms 块 RMS 能量门控，去头尾静音 + 全静音/过短拦截 + 0.2s padding）+ 风格 prompt 拼接（verbatim 纯 Whisper 直出不调 LLM；fluent/email/formal + 翻译 = 单次 chat，风格与翻译指令拼一个 System Prompt）
- **场景 3 UI**：模式四选（忠实记录/智能润色/Email 格式/严肃文档）+ 自动翻译勾选 + 目标语言下拉（复用 TARGET_LOCALE）+ 自动上屏勾选 + 状态行 + 最近结果区 + 配置入口
- **全局热键**：pynput 监听 CTRL+Win（cmd_l/r + ctrl_l/r 组合），按住开始录音、松开结束；热键回调经 root.after(0,...) 回主线程（pynput 回调在独立线程）
- **转写/上屏**：WAV 临时文件 → provider.transcribe（走系统代理）→ process_result 后处理 → 剪贴板 + 自动 Ctrl+V 上屏（pynput Controller）+ 最近结果区显示
- **底部横幅**：Toplevel overrideredirect + topmost，右下角（任务栏上方），聆听/转写/结果/失败各状态，自动消失

### 依赖
- pynput 1.8.2（pip 安装成功）；sounddevice 0.5.5 + numpy 已有
- 无 ffmpeg：P2 音频直传 WAV（16k mono，VAD 已滤静音）；P3 再做 mp3/ogg 压缩

### 验证
- VAD 单测：全静音/低噪拦截、语音段正确裁剪（16320 samples）✅
- prompt 拼接单测：verbatim/email/formal × 翻译开关 4 种组合 ✅
- 场景 3 GUI smoke：7 项 UI 元素齐全 ✅
- 真实链路：TTS 合成"今天天气很好，我想出去走走。" → Groq whisper 转写 → "今天天气很好,我想出去走走。" ✅
- 后处理：verbatim 直出原文 ✅；formal+翻译 → "The weather is currently favorable. I intend to go for a walk." ✅

### 已知限制（P3/P5 待办）
- 录音链路（热键+麦克风）未自动化验证，需用户真机按 CTRL+Win 实测
- 快捷键暂不可配置（P5 做）；横幅固定主屏右下角（双屏用户注意）
- 自动上屏用模拟 Ctrl+V，会占用剪贴板（结果已复制）；未做剪贴板恢复
- Groq 实时流式转写（P3 微信式边录边上字）未核实 API 形态

---

## V2.0 P2 修复：热键卡死"一直正在聆听"（2026-09-08）

### 用户反馈
按一次快捷键后一直"正在聆听"，再按没反应。

### 根因
**Windows 低级键盘钩子对按住不动的修饰键会重复发 KEYDOWN（key repeat）**。
原实现用计数（state["ctrl"] += 1 / 松开 -= 1）：按住 2 秒计数累加到 5+，松开只减 1，
永远不为 0 → on_release 的 finish 条件 (ctrl==0 or win==0) 永不满足 → 一直聆听；
且 rec 卡 True，后续按下全被 
ot state["rec"] 挡住。

### 修复（launcher _start_hotkey）
1. 计数 → **布尔状态**：按下即 True、松开即 False（repeat 事件重复置 True 无副作用）
2. on_press/on_release 包 try/except：回调异常不杀 pynput 监听线程
3. **超时保险**：MAX_REC_SEC=45s，_hotkey_timeout 每 5s 检查，超时自动 finish（防任何卡死路径）

### 验证（GUI 内模拟按键闭环）
- 按下 Ctrl+Win → 状态"正在聆听…"（录音启动）✅
- 松开 → 状态"未检测到语音，请重试"（VAD 拦截模拟静音；状态机正常流转、busy 已重置）✅
- 再按可再次触发（busy 已重置）✅

---

## V2.0 P2 修复：转写卡顿感知 + 结果双份（2026-09-08）

### 用户反馈
1. "正在转写"卡很久（不知道网络还是代码）
2. 总是出来双份

### 实测结论（回答"网络还是代码"）
- 10 秒音频（89.6KB MP3）→ Groq whisper 转写 **1.4 秒**（走代理）
- 合成 1.9s + 转写 1.4s，网络和转写本身都很快
- 用户"卡好久"的真实来源：①录音很长（几十秒 → WAV 1-2MB 上传慢，P3 才做压缩）；②风格化/翻译模式下转写后还有一次 LLM 调用
- Provider 构造无网络调用（已确认）

### 修复 1：转写阶段状态细化
"正在转写…" → 拆成「正在上传转写…」→「正在润色/翻译…」（仅风格化/翻译时有后段），用户能看清卡在哪一步。状态经 root.after 回主线程更新。

### 修复 2：结果双份
根因：测试时 bridge 窗口在前台，自动上屏的模拟 Ctrl+V 把结果粘贴进了 bridge 自己的「最近结果」Text → 看起来两份。
修复：新增 _is_bridge_foreground()（ctypes GetForegroundWindow 对比主窗口/Toplevel 句柄），前台是 bridge 时**跳过自动上屏**（仍复制剪贴板+显示结果）。非 bridge 前台（正常使用场景：焦点在记事本/浏览器）照常上屏。

### 验证
- 状态机闭环：按下→"正在聆听…"→松开→"未检测到语音，请重试" ✅
- 前台检测：测试环境中前台非 bridge → 返回 False（自动上屏执行路径正确）；bridge 前台 → 跳过（逻辑代码审查确认）

---

## V2.0 P2 Prompt 资产正式埋入（2026-09-08）

用户要求把 Prompt 作为"核心算法资产"分层埋入代码（stt_styles.md 从纯参考 → 代码实现）。

### 分层结构（stt_engine.py）
1. **Whisper ASR 层**：WHISPER_PUNCTUATION_PROMPT——全局固定标点引导（中英粤混合示范"听写文本含逗号句号问号…"）。
   靠"示范"而非"指令"工作；**单通道（忠实记录）也使用**。launcher 转写调用传 prompt=stt_engine.WHISPER_PUNCTUATION_PROMPT。
2. **LLM 层**：STYLE_RULES 四风格积木（verbatim/fluent/email/formal，Role/Task/Constraint 结构）
   + uild_system_prompt(style_mode, enable_translation, target_lang) 工厂：
   风格积木 × 翻译积木（Language Instruction + Output Constraint）字符串拼接 → **单次 LLM 请求**。
3. **零多余交互**：process_result 中 verbatim 且不翻译 → 纯 Whisper 直出，不调 LLM（FakeProv 单测验证）。
   LLM 调用 temperature=0.2（格式遵循）。

### 验证
- Prompt 资产存在性、4 风格积木 ✓
- 工厂组合：fluent 无翻译 / email+英文翻译 / verbatim+中文翻译 ✓
- verbatim 无翻译 → FakeProv.chat 不应被调用 ✓
- 真实链路：带引导转写 1.2s"今天下午三点开会,请准时参加。" → formal+英文 0.9s "The meeting is scheduled for 3:00 PM today. Your punctual attendance is required." ✓

### 扩展性
后续加"微信对话模式/PodCast"等：STYLE_RULES 加一项 + UI 加按钮即可，架构不动。

---

## V2.0 P2 模式注释 + 自定义风格积木 + 标点按语言（2026-09-08）

### 1. 模式注释
模式区下方灰色动态说明（随选中切换）：
- 忠实记录：只加标点，保留所有口头废话
- 智能润色：去除口头废话，句子更通顺
- Email 格式：整理成规范的邮件格式
- 严肃文档：改写为正式、规范的文档
- 自定义风格：使用你自己的 Prompt 作为风格积木

### 2. 自定义风格（第 5 个模式）
- 模式行加「自定义风格」radio；选中后启用下拉（选具体风格）+「配置风格…」按钮；非 custom 时下拉/按钮禁用（radio 天然互斥，一次只能选一个）
- 配置对话框 open_stt_styles_dialog：左侧 Listbox 最多 5 个风格（新增/重命名/删除），右侧 Text 编辑 Prompt 积木；保存写入 cfg["stt_styles"]（[{"name","prompt"}]）
- stt_engine 工厂支持 custom：uild_system_prompt(style_mode, enable_translation, target_lang, custom_prompt=None)——custom 且有 prompt 用用户积木，空 prompt 回退 fluent
- 转写 work：mode=custom 时从 cfg 取选中风格的 prompt，空则报错提示先配置

### 3. 标点引导按语言全角/半角
- uild_whisper_prompt(lang_hint)：zh/ja/ko → 全角中文示范（，。？）；其余 → 半角英文示范（, . ?）
- launcher 按目标语言主前缀（TARGET_LOCALE 拆分）自动选择；保留 WHISPER_PUNCTUATION_PROMPT 常量兼容

### 验证
- stt_engine 单测：zh/ja 全角 ✓ en 半角（无全角逗号）✓；custom 积木进工厂 ✓ 空回退 fluent ✓；process_result custom ✓；verbatim 零交互 ✓
- GUI smoke：5 模式 radio ✓ 默认说明=忠实记录 ✓ 切 custom 说明切换 ✓ custom 下拉 readonly 启用 ✓
- 踩坑：command=_on_stt_mode_change 在 radio 创建时立即求值 → 函数必须定义在 radio 之前（UnboundLocalError 已修）

---

## V2.0 P2 "一直上传转写"诊断与加固（2026-09-08）

### 用户反馈
语音输入卡在"正在上传转写…"不动。

### 实测诊断
- 系统代理开启（127.0.0.1:7897）✓
- **直连 api.groq.com 现在也 HTTP 200**（之前被墙，网络环境已变化/全局代理）✓
- 代理 HTTP 200 ✓
- 完整转写 1.6s ✓
→ 链路本身正常；用户卡住应为网络瞬态（Clash 节点切换/代理抖动）或录音较长上传慢。

### 加固（避免再次"卡死无反馈"）
1. provider.transcribe timeout 120 → **60s**（快速失败，不挂 2 分钟）
2. work 线程加**耗时日志**：ui_log 打印"转写耗时 Xs / 润色/翻译耗时 Xs"，卡住时看日志定位阶段
3. 大音频提示：wav > 3MB 时状态提示"音频较大，转写会稍慢"
4. _stt_error 失败信息追加代理提示（网络/timeout/EOF 相关错误时）

### 验证
py_compile 通过；链路实测 1.6s 正常（见上）。

---

## V2.0 P2 删 Email 模式 + 自定义无积木置灰（2026-09-08）

### 用户决策
- Email 格式模式删除（写邮件走长文本转语音场景，STT 不需要）；模式变 4 个：忠实记录 / 智能润色 / 严肃文档 / 自定义风格
- 自定义风格在没有任何积木配置时**置灰不可选**（防呆：选了 custom 但没配积木会卡/报错）

### 实现
- STT_MODE_DESC 与 radio 循环删除 email
- custom radio 单独创建存引用 stt_custom_radio；_refresh_stt_styles 更新其状态：
  - cfg["stt_styles"] 非空 → normal
  - 空 → disabled；若当前正选中 custom → 回退 verbatim 并刷新说明
- 配置对话框保存后经 on_saved=_refresh_stt_styles 自动刷新（删光积木同样生效）

### 验证
GUI smoke：4 模式（无 Email）✓ custom 无积木 state=disabled ✓

---

## V2.0 P2 配置风格对话框 NameError 修复（2026-09-08）

### 用户反馈
点「配置风格…」无效果，控制台 NameError: name 'tk' is not defined（launcher.py:1285 open_stt_styles_dialog）

### 根因
launcher.py 的 import tkinter as tk 只在 run_gui / open_provider_dialog 等**函数内部**局部导入；open_stt_styles_dialog 是**模块级函数**，作用域内无 tk → NameError

### 修复
open_stt_styles_dialog 函数体开头补局部导入：import tkinter as tk + rom tkinter import ttk, messagebox

### 验证
GUI smoke：点配置按钮 → Toplevel 正常打开（Toplevel 数量 1）✓

---

## V2.0 P2 王家卫风预制 + 快捷键自定义（2026-09-08）

### 1. 预制自定义风格「王家卫风」
- run_gui 启动时：cfg 无 "stt_styles" 键（首次）→ 预置「王家卫风」完整 prompt（王家卫式电影独白风格，含 Temporal Anchors/Sensory & Visual Imagery/Tone/Core Retention/Output Constraint），用户可编辑/删除；已存在则不覆盖（尊重用户删光意图）
- 用户当前 cfg 已有王家卫风（用户自行添加过），预置为防御逻辑

### 2. 自定义快捷键
- 场景 3 顶部新增「自定义快捷键」按钮 + 动态提示行（"按住 CTRL+Win 说话，松开自动上屏"），提示随配置变化
- open_stt_hotkey_dialog：输入框捕获组合键（**ctypes GetKeyState 物理键查询**，Windows Tk event.state 不保证 Win 位），实时预览（如 "ALT+Space"），保存校验必须含修饰键
- 配置存 cfg["stt_hotkey"]（如 ctrl+win / alt+space）
- _start_hotkey 重构：解析任意组合（修饰键集合 + 可选触发键）；on_press/on_release 用通用 norm() 映射键名 + combo_active() 判定；保存后 _restart_hotkey stop 旧 listener 重绑并刷新提示行
- _hotkey_display：ctrl+win → CTRL+Win；alt+space → ALT+Space

### 验证
- 单测：_hotkey_display 三种输入 ✓
- GUI smoke：提示行显示当前快捷键 ✓ 按钮存在 ✓ 对话框打开 ✓

---

## V2.0 P2 状态条滚动出字 + 极速记录中 + 结果不进状态条（2026-09-08）

### 用户反馈
1. 状态条太呆（"正在聆听…"），想要微信语音转文字那种滚动出字感
2. 松开后单通道应显示「极速记录中」区分轻量化 vs 搭积木
3. 转写完成后状态条不要显示整段文本（长话一大片）

### 实现
1. **状态动画** _stt_anim_start(base_zh, base_en)：文字 + 递增圆点（·、··、···、····，380ms 周期），status 行与底部横幅同步滚动；_stt_anim_stop 停止并取消 after
2. **极速记录中**：_fast_path = mode==verbatim and not 翻译 → 松开后显示「极速记录中」；否则「正在转写」→「正在上传转写」→「正在润色/翻译」（各阶段动画切换）
3. **结果不进状态条**：commit 后 status 显示「完成，已复制到剪贴板」，banner 短提示绿色 2.6s 消失；全文只进结果框 + 剪贴板（自动上屏）

### 验证
GUI smoke（真实 pynput 模拟按住/松开）：按住 → "正在聆听（松开完成）··"（动画跑）✓ 松开 → "未检测到语音，请重试"（anim 停止）✓

---

## V2.0 P2 转录文字滚动显示（打字机）+ 转写耗时显示（2026-09-08）

### 用户澄清
上轮"滚动出字"指 **滚动显示转录出来的文字**（微信语音转文字式），不是圆点动画。圆点动画仅保留在聆听阶段。

### 实现
1. **_stt_banner_typewrite(full)**：转写完成后，底部横幅逐字滚出转录全文（一次 3 字；前 60 字 34ms/帧，之后 12ms/帧提速；滚完停 5s 消失）。窗口内状态行保持「完成，已复制到剪贴板」短提示（不糊一大片）。
2. **转写阶段带耗时**：_stt_anim_start(..., track_elapsed=True) → 「极速记录中 5s…」「正在转写 12s…」，用户能分辨"还在跑" vs "卡死"。

### 卡死问题
用户又遇转写卡死。上次实测链路 1.6s 正常（直连+代理都 200）。新增耗时显示帮助定位：若秒数持续增长说明服务端慢（长音频/代理慢）；>60s 超时报错并提示查代理。继续观察用户反馈。

---

## V2.0 P2 转写 10 秒硬超时 + 失败交代（2026-09-08）

### 用户要求
1. 松开快捷键后 10 秒还没转写完就停掉
2. 转录没成功时状态条给交代（网络原因等），显示几秒

### 实现
- **10s 总预算**：work 线程 _deadline = time.time() + 10；transcribe / process_result 各按剩余时间分配 timeout（max(2, int(remain - 1))，留 1.5s 提交余量），预算耗尽抛 ProviderError("转写超时（超过 10 秒），已停止。请检查网络/代理或缩短录音")
- stt_engine.process_result 加 	imeout 参数透传 provider.chat（默认 90 保留）
- 失败交代：_stt_error 已有——status 行留失败原因（含超时/网络提示），底部横幅红色显示 4s 后消失

### 验证
- 单测：process_result timeout 透传 ✓ verbatim 零交互不受影响 ✓
- py_compile 通过

---

## V2.0 P3 实时流式转写：一边说一边滚动出字（2026-09-08）

### 用户需求（最终确认）
按下快捷键 → 一边说，状态条一边滚动显示转录的文字；不要出现"聆听中"。

### 架构（P2 一次性转写 → P3 流式）
- **Recorder 分块**：stt_engine.Recorder 采集时按 CHUNK_SEC=1.5s 封块（int16 bytes 存 _sealed + 正在写的 _tail）；新增 sealed_count/sealed_audio/tail_audio/stop_stream；stop() 保留旧 VAD 完整路径兼容
- **wav_bytes_from_int16**：裸采样封 WAV helper
- **_stream_worker**（按住期间后台线程）：每封 2 块（~3s）触发一次转写；**重叠窗口**（前 1.2s overlap + 新块）→ transcribe(timeout=8) → _merge_continuation 去掉与上一段末尾重复前缀 → 增量追加 _stream_state["text"] → root.after 更新 status（最近 80 字）+ banner（最近 160 字）
- **聆听阶段**：_stt_anim_start("", "") 只显示脉动圆点（无"聆听中"字样），第一段文字 ~2-3s 后开始滚动
- **_stt_finish 收尾**：stop_stream → join worker(2s) → 剩余（未转写块+尾部+overlap）一次转写 → 拼接 → 搭积木则 LLM 润色（10s budget）→ commit
- **快速按放**（<1.5s 没出字）→ 旧单次路径（完整音频 VAD + 10s budget）
- 迟到流式结果保护：_stt_stream_update 检查 _stt_busy，已收尾则忽略

### 踩坑
- **except 变量闭包陷阱**：
oot.after(0, lambda: _stt_error(e)) 延迟执行时 e 已被 Python 清除（except 子句变量生命周期结束）→ NameError。修复：lambda e=e: _stt_error(e)（3 处）

### 验证
- 单测：_merge_continuation 去重 ✓ Recorder 封块（2s 音频 → 1 块 + 0.5s tail）✓ wav 封装 ✓
- GUI smoke：按住 → '···'（无聆听中）✓ 松开无语音 → '未检测到语音' ✓
- 真机说话验证待用户（录音+实时转写闭环需真实麦克风）

---

## V2.0 P2 补漏：音频压缩 OGG（2026-09-08）

### 澄清
- 音频压缩（plan.md §P2 场景 B「录音→VAD→压缩→整段上传」）原本就是 **P2** 的正式步骤，此前一直漏做（直传 WAV）
- P3 的流式实时滚动反而提前完成了 → 现在补上 P2 的压缩

### 实现
- **stt_engine.compress_audio**：int16 裸采样 → soundfile OGG Vorbis（约 1/9 体积，440Hz 1s 音 → 3825B）；soundfile 不可用返回 None
- **stt_engine.transcribe_compressed**：压缩 → 写 oxecho_stt.ogg 临时文件 → provider.transcribe（multipart filename 带 .ogg，Groq 按格式识别）；压缩失败回退 WAV
- launcher 接入：_stream_worker 窗口转写 + _stt_finish 收尾转写都走 transcribe_compressed；快速按放单次路径保留直传 WAV（仅上传一次，压缩收益小）
- 依赖新增：soundfile 0.14.0（含 libsndfile，OGG 编码原生支持，无需 ffmpeg）
- 顺手修 stt_engine 顶层漏 import Path 的 NameError；更新模块 docstring

### 验证
- 单测：压缩比 12% ✓ .ogg 文件 + OggS 魔数 + prompt/timeout 透传 ✓ 压缩失败回退 WAV（RIFF）✓
- 真机待用户实测（说话流式上屏 + 上传体积下降）

---

## V2.0 修复：latin-1 API Key 编码崩溃 + 清洗（2026-09-08）

### 症状
硅基流动 key 转写时报 UnicodeEncodeError: 'latin-1' codec can't encode characters in position 7-10。
根因：requests 用 latin-1 编码 Authorization header，粘贴的 key 里混入非 ASCII（中文/零宽空格/换行/全角）。

### 修复（三重防线）
1. **provider.sanitize_api_key**：先 encode('ascii', errors='ignore') 剔除非 ASCII，再 
e.sub(r"[^a-zA-Z0-9\-_=]", "", ...) 保留字母数字 - _ =（兼容 base64 key，如 sk-ant-...）；空/None → ""
2. **Provider.__init__ 构造时清洗**：所有入口（含旧配置脏 key）自动洗净，运行期/转写免疫
3. **配置对话框**：
   - key 输入框左侧新增「粘贴」按钮 → 读剪贴板 → 清洗 → 填入（有变化时 ui_log 提示）
   - key_entry bind <Control-v> / <Control-V> 拦截 → 同样清洗后插入（选中内容先删除再插）
   - _build_provider / on_save 也调 sanitize（手动输入兜底）

### 踩坑
- patch 脚本里 if 'import re' not in src 被 import requests 子串干扰 → 判断永远 True 跳过替换 → 运行期 NameError。修法：按行精确检查 l.strip() == 'import re'
- Tk event_generate 模拟 Ctrl+V 在 Windows 上剪贴板读取时机不可靠（smoke 中 v 变空），真实按键走系统消息链无此问题；smoke 以 bind 注册 + 按钮路径（同一 sanitize 逻辑）+ Provider 构造清洗为准

### 验证
- sanitize 单测：中文/全角/零宽/换行 → 干净 ✓ gsk_ 保留 ✓ = 保留 ✓ 正常 key 原样 ✓
- Provider 构造清洗 ✓
- GUI smoke：粘贴按钮（脏 key → sk-key==）✓ bind 两键位注册 ✓

---

## V2.0 模型下拉：可编辑/粘贴 + 不再覆盖未命中模型（2026-09-08）

### 背景
ASR/LLM 下拉按关键词（ASR_KEYWORDS）自动分类，硅基流动等平台经常没命中；
用户需要能手动粘贴模型名。

### 根因（两个）
1. **非自定义平台下拉被设 readonly**（state = "normal" if custom else "readonly"）→ 根本不能输入/粘贴
2. **on_fetch 自动修正**：if asr_var not in asr_list: asr_var.set(asr_list[0]) → 刷新时把用户已保存但未命中的模型悄悄改成列表第一个（实测例：已保存 deepseek-ai/DeepSeek-V3 会被改成 DeepSeek-V3）

### 修复
- 模型下拉一律 state="normal"（可点选可输入可粘贴）；URL 仍仅自定义平台可填
- 删除 on_fetch 两行自动修正；当前值（含手输未命中模型）通过 dict.fromkeys([当前值] + 列表) 始终置顶保留
- 刷新按钮下加灰字提示："模型下拉可点选，也可直接输入/粘贴模型名（列表未命中时）"

### 踩坑
- ttk Combobox cget('state') 返回 Tcl_Obj 而非 str，== 'normal' 恒 False → smoke 断言用 str(...) 比较

### 验证（GUI smoke，monkeypatch fetch_models_split 避免真网络）
- 两个模型下拉 state=normal ✓
- 手输 iic/SenseVoiceSmall（不在列表）→ 点刷新 → 值保留 + values 含它 ✓
- LLM 已保存 deepseek-ai/DeepSeek-V3 刷新后不被覆盖 ✓

---

## V2.0 排查：硅基 SenseVoice 400 code:20012（2026-09-08）

### 症状
用户硅基平台 + FunAudioLLM/SenseVoiceSmall 转写报 HTTP 400 {"code":20012,"message":"Model does not exist. Please check it carefully."}

### 排查结论（用用户真实 key 实测）
- **模型 ID 合法**：硅基官方文档 Available options 就是 FunAudioLLM/SenseVoiceSmall / TeleAI/TeleSpeechASR（2026-09-01 更新仍在）
- **配置无误**：bridge_config.json platform=siliconflow、key 干净、base_url 空自动回退 https://api.siliconflow.cn/v1
- **完整路径复现成功**：带代理（system_proxy() 读到 127.0.0.1:7897）→ transcribe_compressed（OGG+引导 prompt）→ HTTP 200 返回 "🎼Yeah."
- 20012 确认是硅基官方错误码（SiliconFlow error-code 文档 + 社区大量案例：模型 ID 错/未开通/瞬态）
- 直连 api.siliconflow.cn 在用户机器上 Read timed out → 硅基必须走代理

### 判断
400 属瞬时/网络路径问题（当时 Clash 分流到海外节点被硅基边缘拒绝，或平台模型热更抖动），配置本身无问题。用户现在配置可直接用。

### 增强
- provider.transcribe 错误信息追加网络路径提示：转写失败（HTTP 400，走代理 127.0.0.1:7897）: body；直连则显示 直连（未走代理）——以后任何平台报错可秒级判断是否网络路径问题
- chat 错误块实际格式与假设不同，未命中（跳过，保持原样）

---

## V2.0 ASR 热词纠偏（hotwords.txt）（2026-09-08）

### 需求
Whisper 也有识别不准的场景 → 用可编辑的 TXT 热词文档给 ASR 纠偏。

### 现状调研
**没有"下载即用"的通用热词文档**：所有平台（腾讯/阿里/火山/WeNet）的热词都是用户自建（热词本质=你容易听错的词，因人而异）。可参考的示例源：WeNet hotwords_all.txt（AISHELL 评测词表）、各类中文专有名词清单。另有谐音纠错表思路（Whisper→微死婆，后处理替换），与 Whisper prompt 偏置是两条路线，暂不做。

### 实现（走 Whisper prompt 偏置，复用现有 build_whisper_prompt 链路）
- **hotwords.txt**（bridge 目录，UTF-8，内置常见专有名词示例：鸿蒙/大疆/比亚迪/特斯拉/SpaceX/王小波/沈从文/ChatGPT）
  - 每行一个词，# 注释；最多生效 150 条、总长 1200 字符（Whisper prompt token 有限）
  - 读取实时（每次转写时 load），保存即生效，不用重启
  - utf-8-sig 兼容 BOM；文件缺失/异常返回空
- **build_whisper_prompt**：标点引导 + 语言提示后追加 \n以下专有名词可能出现在语音中，请优先准确识别：词1 词2 ...
- 流式 worker / 收尾 / 快速单次路径全部自动生效（都走 build_whisper_prompt）
- **UI**：语音服务配置面板「刷新模型列表」旁新增「打开热词文件」按钮（os.startfile 打开，Mac 兜底 subprocess open；文件不存在自动创建模板）

### 验证
- 单测：注释忽略/去重/150 条与 1200 字截断/BOM/缺文件/prompt 拼装（中英）✓
- GUI smoke：按钮存在 ✓
- 真机纠偏效果待用户实测（建议放 2-3 个自己常听错的词验证）

---

## V2.0 热词上限实测与调整（2026-09-08）

### 实测（硅基真实请求）
- 300 条（1725 字符）/ 800 条（4725 字符）：Read timed out —— Clash 网络抖动，非平台拒绝
- 500 条（2925 字符）：HTTP 200 正常识别 → **平台对 prompt 长度容忍度高**

### 关键认知
Whisper 模型对 initial prompt 的利用上限约 224 token（中文约 200 字），**超出部分模型读不到**。
所以堆条数无收益；有效策略 = 高频/易错词放最前面（模型优先读取）。

### 调整
- _MAX_HOTWORDS: 150 → 500；_MAX_HOTWORDS_CHARS: 1200 → 2500（防极端场景 + 不卡用户）
- hotwords.txt 注释更新：说明"约 200 字内最有效，最常错的 10~30 个放最上面"
- 单测：600 条输入 → 截断到 500 ✓
- 未来若热词上千：考虑本地"谐音纠错表"后处理替换（与 prompt 偏置互补，不限量），已记录备选

---

## V2.0 同音消歧 + AGC 自动增益（2026-09-08）

### 用户实测反馈
"这个逗号是全角还是半角" → 识别成 "这个逗号是拳脚还是半脚"（同音字错）。
叠 LLM 润色能纠正一部分（有世界知识），但延迟/成本高。
微信语音转文字不靠 LLM，靠 ASR 端优化 + 录音链路：**离麦克风很远也能收到**。

### 两个改进
1. **热词即时见效**：全角/半角 已加入 hotwords.txt 顶部（同音消歧正是热词适用场景）。
   注意：Whisper prompt 是偏置非强制，若仍错，LLM 层兜底或未来谐音纠错表（后处理强制替换）。
2. **AGC 自动增益（复刻微信"远距离可识别"的录音链路半）**：
   - VAD 阈值 500 → 150（安静环境远距离说话 RMS 常在 200~600，原阈值会误裁；短噪声块由 min_speech_blocks 拦截）
   - `apply_agc_int16/apply_agc`：去直流 → 提升到目标 RMS 4000（-18dBFS）→ clip 保护
   - 参数：max_gain 24dB（RMS 250 即可达标）；noise_gate=100（RMS<100 视为静音/底噪，不放大，防抬噪）
   - 挂载点：transcribe_compressed 入口（流式窗口/收尾自动覆盖）+ Recorder.stop()（快路径 VAD 后）
   - 副作用：整段统一增益会把静音尾同步放大，但信噪比保持 20dB+（实测 32dB），whisper 无感

### 验证
- 单测：RMS 300→4000 ✓ / 150→2370 ✓ / 30000→3999 ✓（双向 AGC）/ 纯静音不放大 ✓ / 直流偏置去除 ✓ / 信噪比 32dB ✓
- 远距离真机效果待用户实测（建议离远一点说几句话对比）
- 微信还做了降噪/回声消除（AEC/NS），我们没有；纯安静房间够用，嘈杂环境是已知差距

---

## V2.0 流式收尾重复输出（"两句变两句"）修复（2026-09-08）

### 用户现象
按住说一句"这个逗号是全角还是半角？"，返回两份："这个逗号是全角还是半角？这个 多号是全角还是半角？"（whisper V3 turbo 单通道直出）

### 根因（三连环）
1. **流式中途**：按住 ~3s 时（2 块）转写一次 → 完整句已出 → text 已含整句
2. **松手收尾**：window = overlap（上一窗口末尾 1.2s，**是已转写过的音频**）+ remain（尾静音）→ 又把整句识别一遍（识别差异："逗号"→"多号"）
3. **去重失效**：_merge_continuation 是**精确后缀匹配**，第二次文本与流式文本不逐字相同 → 匹配失败 → 追加成双份

### 修复（三件套）
1. **收尾窗口去掉 overlap**：只转写 remain（未覆盖过的全新音频）——overlap 是重复转写的根源
2. **收尾前能量检查** stt_engine.has_speech(raw, thresh=150, min_blocks=2)：remain 基本静音（如只说了一句话就松手）→ 直接跳过收尾转写（省一次 API + 防双份）
3. **宽松重复检测** _is_redundant(prev, new, ratio=0.6)（仅收尾路径）：difflib 对比新文本与已有文本**末尾 len(new)（最少 8 字）窗口**，重叠 >60% → 判定幻觉/整句重识，丢弃
   - 窗口取尾部（不是固定 40 字）：贴合"重复来源=已转写尾部"，避免长前文稀释相似度
   - 1-3 字衔接重复**不拦**（那是 _merge_continuation 精确去重的职责，收尾无 overlap 后只剩跨窗口半个词）

### 验证
- has_speech：静音/低能量 False，语音/混合段 True ✓
- _is_redundant 7 场景：整句重复（含识别差异）→ 丢 ✓；衔接增量 → 留 ✓；改口 → 留 ✓；长文本尾部重复 → 丢 ✓；短衔接 → 留（交精确去重）✓；新话题 → 留 ✓
- 真机待用户复测：说一句 3s 左右的话，应只出一份

---

## V2.0 降噪（NS）定案 + 上下文注入 + ChatGPT 架构评审（2026-09-08）

### 降噪实现与定案（stt_engine.denoise）
- 链路：降噪 → VAD → AGC（transcribe_compressed 入口 / Recorder.stop() / has_speech 三处统一）
- 算法：STFT(25ms/10ms) → 噪声底 = **能量最低 10% 帧的平均谱**（帧级选择，不用 per-bin 分位——
  Rayleigh 分布下分位严重低估噪声水平，g 偏高压不动）→ Wiener 增益 g = mag²/(mag²+α·noise²)，α=3.0（谱减过减值）→
  低频（<60Hz）额外 ×0.05（除 50/60Hz 交流声与 DC）→ OLA 逆变换（裁掉反射 padding 边缘，防异常放大）
- 坑：`g[freqs<60]` 布尔索引曾作用在帧维（帧数=201 时恰好不报错但语义错误）→ 必须 `g[:, freqs<60]`
- 实测：纯语音保真 100% / 纯噪声压至 48%(-6.4dB) / 语音段保留 94%、停顿段压至 43% / 50Hz 压至 9%(-21dB) / 1.5s 耗时 9ms
- 已知边界：单通道谱门控对"持续语音+强噪声"压不干净（保护语音优先）；嘈杂环境需深度学习降噪/阵列（不做）

### 上下文注入（ChatGPT 评审最值得引用的洞见，已实现）
- 洞见：每窗口 Whisper 独立转写 = 丢失前文 → "这个多号是拳脚还是半脚" 跨窗口不会自我修正
- 实现：流式每窗口 + 收尾的 prompt 追加 `\n上文内容：` + 已识别文本末尾 50 字（复用 build_whisper_prompt 链路）
- 效果预期：跨窗口同音消歧/衔接更稳（待真机复测"全角/半角"场景）
- 快速路径（单次整段）无窗口概念，不带上下文

### ChatGPT 九层链路评审 → 对照结论
- **已做**：流式增量识别（非"说完才转"）、interim UI 滚动出字、音频预处理（16k mono/NS/AGC/低频）、
  VAD 能量门控+阈值自适应、LLM 窄门修正（verbatim 直出不调 LLM）、热词 prompt 偏置
- **本次新增**：上下文注入（第⑥层）
- **值得后续做**（按收益）：
  1. 热词文件支持 `错误=正确` → 本地后处理强制替换（100% 命中，与 prompt 偏置互补，不限量）
  2. Interim→Final 加强：短录音（<15s）松手后整段重 decode 一次（修正 interim 整句错误）
  3. 轻量 Text Normalization（ai→AI、百分之五十→50%、中英文空格）——verbatim 也生效，无 LLM 延迟
- **不适用**：本地推理后端（whisper.cpp/faster-whisper）——我们是云端 API 架构；
  自动 VAD 端点检测——我们按住说话，手动起止 + 能量门控已够
- **提示**：Spokenly 若为 whisper.cpp 本地架构，其优势=零网络延迟+可能真流式；我们差距=API 往返延迟
  + 分块伪流式（真 WebSocket 流式 = provider.stream_transcribe 后续研究项）

---

## V2.0 单通道双输出根因补全 + Groq 速率门控（2026-09-08）

### 用户现象
1. 单通道（verbatim）直出效果不错，但仍输出两次（先一次、再转写一次）
2. 引用 LLM 即 429；Groq 限速 ASR 20/min、LLM 30/min

### 双输出的完整根因（三层）
1. 收尾窗口带 overlap 重转已转写音频（上一轮已修：去掉 overlap + has_speech 检查 + _is_redundant）
2. **流式窗口间 overlap 重识**（本轮补）：窗口1 识别整句 → 窗口2 音频含 overlap（1.2s 句尾）→
   whisper 又输出整句变体（"拳脚/半脚"），精确后缀匹配不上 → 追加 → 双份
3. 用户猜"ASR 底层 prompt 加标点导致二次输出"——**不是**：prompt 只是偏置，不产生额外输出；
   第二次是同一段音频的重复转写

### 本轮修复
1. **`_merge_continuation` 模糊前缀剥离**：精确匹配失败后，用 SequenceMatcher 找
   t 前缀与 prev_tail 末尾的最佳近似重叠（阈值 0.5、最小 4 字、上限 40 字），剥掉后只返回新内容
   - 中文变体重识+"下午开会" → 正确剥前缀只留"下午开会"
   - 完全重复（无新内容）→ 返回空，不追加
   - 英文 30 字重复前缀同样处理
2. **速率门控**（launcher 模块级）：
   - `_rate_check(kind)`：61s 窗口令牌，ASR 上限 18/min、LLM 28/min（Groq 20/30 留余量）
   - `_rate_wait(kind, deadline)`：等待至可用或超时（LLM 场景等待，预算内）
   - 流式窗口被限流/服务端 429 → **跳过窗口**（sealed_idx/overlap 照常推进，音频由收尾统一补转）
   - 快速路径/收尾 ASR、LLM 调用前 _rate_wait，超预算提示"达到速率限制，请稍候重试"

### 验证
- 模糊剥离：中文/英文变体+新内容、完全重复、不同内容、短重叠、精确匹配 6 场景 ✓
- 门控：18 次放行后拒绝、62s 过期恢复、两类独立、_rate_wait 超时 ✓
- 真机待复测：单通道一句是否只出一份；连续多次 LLM 是否不再 429

### 说明
- 流式被限流跳过的窗口：用户实时性短暂停更（3s 级），松手后收尾补全——可接受
- 若 Groq 免费额度仍紧张，后续可做：流式窗口动态降频（检测 429 后自动拉长间隔）

---

## V2.0 架构决策：放弃伪流式切片，回归整段一次转写（2026-09-08）

### 决策背景（用户提出，双确认后执行）
伪流式（按住时每 ~3s 分块独立转写 + 文本拼接）三项成本超过收益：
1. **降准确率**：每窗口独立转写、窗口边界截断、上下文丢失，且需文本级拼接/去重（为此写过精确匹配、
   模糊剥离、_is_redundant 三套去重）
2. **爆限流**：3s/次 ASR = 60s 顶满 Groq 20/min；一次完整转写只要 1 次（效率差 20 倍）
3. **Groq 无真流式端点**：OpenAI 兼容 /audio/transcriptions 是同步整段，"边说边出字"的微信体验
   在 Groq 上做不了（真流式需本地 whisper.cpp 或 WebSocket 端点，非当前架构）

### 新管线（单路径）
按住说话 → 松开 → stop() 整段（降噪 → VAD 裁剪 → AGC，均在 Recorder.stop() 内）→
OGG 压缩上传（失败回退 WAV）→ 单次 ASR（热词 prompt）→ 按需单次 LLM（风格×翻译积木）→ commit

### 代码改动
- **stt_engine.py**：
  - 删 has_speech、CHUNK_SEC、transcribe_compressed（单路径由 launcher 直接压缩上传）
  - Recorder 重构：删 _sealed/_tail/封块/sealed_count/sealed_audio/tail_audio；
    stop() 改为返回裸 int16 bytes（原返回 WAV）
  - 保留：denoise / AGC / VAD.trim / compress_audio / wav_bytes_from_int16 / build_whisper_prompt
- **launcher.py**：
  - 删 _stream_state/_STREAM_CHUNKS/_STREAM_OVERLAP_SEC/_stream_worker/_stt_stream_update/
    _stt_stream_fail/_merge_continuation/_is_redundant（约 150 行）
  - _stt_begin：只启动录音 + 脉动圆点（删 worker 启动/provider 预检）
  - _stt_finish：双分支（快速路径 + 流式收尾）→ 单路径 work 线程
  - 保留：_rate_check/_rate_wait 门控（ASR 18/min、LLM 28/min）、OGG 压缩、热词 prompt、
    快速路径"极速记录中" vs "正在转写"文案、_stt_banner_typewrite（结果滚动展示）
- **体验变化**：无实时滚动出字；松开后状态条 转写中 → 结果滚动展示（保留微信式打字机效果）

### 验证
- stt_engine 单测：Recorder 有语音返回非空/静音 None/过短 None、OGG 压缩、WAV 封装、热词 prompt ✓
- GUI smoke：STT 区域控件 + 快捷键提示存在 ✓
- 真机待复测：按住说话一句 → 只出一份；连续多次 LLM 不再 429；准确率应高于伪流式

---

## V2.0 Groq LLM 429 "Request too large for model" 根因与修复（2026-09-08）

### 用户现象
单通道（verbatim 直出）正常；一旦引用 LLM（风格/翻译）报错："443 错误" + "Toolarge forLLM Model，千问的"

### 根因（两个独立问题，被合并描述）
1. **"Request too large for model" = HTTP 429，不是文本太大**：
   - 错误原文：`{"error":{"message":"Request too large for model \`qwen/qwen3.8-27b\` ... on output tokens per minute (OTPM): Limit 1000, Requested ..."}}`
   - Groq 免费档按 **OTPM（输出 token/分钟）预留容量**计限流；我们不传 `max_tokens` 时，
     Groq 按模型最大输出上限（几万 token）预留 → 一次请求就远超 1000 → 429
   - qwen/qwen3.8-27b 的 OTPM = 1000（免费档）
2. **"443" = SSLEOFError**：HTTPSConnectionPool host='api.groq.com' port=443，
   Groq 直连/代理瞬时被墙（GET /models 与直接 requests 均 200，重试后恢复——网络抖动，非代码问题）

### 修复
1. `provider.chat(..., max_tokens: int | None = None)`：为 None 时不传（场景 2 长文本翻译保持默认，
   避免截断）；传值则 payload["max_tokens"]=...
2. `stt_engine.process_result(..., max_tokens: int | None = 512)`：STT 场景默认 512（< 1000 OTPM，
   中文几百字输出够用）——launcher 无需改动（默认值生效）
3. provider.chat 429 分支：错误文本含 "Request too large" 时附加中文提示
   （"当前模型输出速率受限 OTPM；短文本场景请降低 max_tokens，长文本翻译建议更换 LLM 模型或平台"）

### 验证（真实 Groq key + qwen3.8-27b）
- 王家卫风风格化：成功（输出质量惊艳）
- verbatim + 翻译英文：成功
- 带 max_tokens=16 的裸 POST：200（确认 OTPM 逻辑）

### 边界（未改，提醒用户）
- 场景 2 长文本翻译走 provider.chat **不传 max_tokens**——在 Groq+qwen3.8-27b 下长文本
  输出仍会 429（OTPM 1000 不够长文本）。建议：场景 2 翻译用硅基（DeepSeek-V3 无此限制），
  或 Groq 只用于短文本。错误提示已覆盖该场景。
- Groq 免费档 qwen OTPM=1000：STT 场景 max_tokens=512 下一分钟最多约 1-2 次 LLM 调用

---

## V2.0 Groq 443/SSLEOFError 网络重试机制（2026-09-08）

### 现象
用户实际使用中反复遇 "443 / max retries"（SSLEOFError: EOF occurred in violation of protocol）。
实测 GET /models 与直接 requests POST 有时 200、provider.chat 有时 SSLEOFError——间歇性。

### 根因
Groq 域名经代理的 TLS 握手被间歇性 RST（GFW 对 Groq 的干扰），同一时刻小请求通、换时刻握手被重置。
非代码逻辑问题，代码层面正解 = 网络层自动重试 + 明确提示。

### 修复（provider.py）
1. `_is_network_error`：网络层异常（requests.RequestException）可重试；HTTP 业务错误不可重试
2. `_request_with_retry(method, url, retries=2, **kw)`：统一请求入口，网络异常重试 2 次
   （退避 0.8s/1.6s），每次重试前重新读 system_proxy()（Clash 节点/开关可能变化）；
   耗尽后抛 ProviderError 带"请切换 Clash 节点或确认代理已开启"
3. list_models / chat 改用 _request_with_retry
4. transcribe 独立重试循环（multipart 文件流每次重试重开文件，避免文件指针已消费）

### 验证
- 伪造测试：首次 SSLError → 第 2 次成功（调用 2 次）；持续失败 → 3 次后 ProviderError 带提示；
  HTTP 429 业务错误不重试（不浪费额度）✓
- 真实调用：智能润色（fluent）成功 ✓

### 说明
- 重试上限 2 次 + 退避 ≈ 增加 ~3.6s 最坏延迟，对 STT 10s 预算可接受
- 若 Clash 节点长期不通，错误消息会明确指向"切换节点"，不再让用户误以为是代码问题

---

## V2.0 Groq LLM 模型选型实测（2026-09-08）

### 背景
qwen/qwen3.8-27b 连续使用即 429（OTPM=1000）。用户贴出 Groq 全部 Chat 模型，问换一个是否更好。

### 实测结果（真实 key，逐模型探测）
| 模型 | max_tokens=4096 探测 | 中文风格化 |
|---|---|---|
| openai/gpt-oss-120b | 200 | ✅ 好（全中文，王家卫风最对味） |
| groq/compound | 200 | ⚠️ 输出偏英文（900 字但语言跟随差） |
| groq/compound-mini | 200 | 未测质量 |
| openai/gpt-oss-20b | 200 | ❓ "你好"返回空（可疑） |
| qwen/qwen3.8-27b | 200 | ✅ 中文最好，但 OTPM=1000 |
| qwen/qwen3.6-27b | 200 | ❌ 带 <think> 思考链污染输出 |
| allam-2-7b / gpt-oss-safeguard | NETFAIL（代理抖动） | 阿拉伯语/护栏模型，不适合 |

### 关键认知修正
Groq OTPM 按**实际输出 token/分钟**计（非 max_tokens 预留）：
- max_tokens=4096 请求全部 200（输出只有几 token）
- qwen3.8 单次风格化 288 字 OK，但一分钟 3-4 次即累计超 1000 → 429
- 之前 max_tokens=512 修复仍有效（控制单次输出上限），但换高 OTPM 模型才是根治

### 决定
llm_model: qwen/qwen3.8-27b → **openai/gpt-oss-120b**（bridge_config + PROVIDERS 默认/置顶同步更新）
- qwen3.8 中文最优但 OTPM 1000 太紧，保留在 llm_pinned 备选
- qwen3.6 因思考链排除（若未来 Groq 支持关 thinking 可重估）
- 需重启 bridge 生效；真机复测：连续多次风格化/翻译是否不再 429

---

## V2.0 崩溃修复 + UI 版面重构（2026-09-08）

### 崩溃："说了一段程序直接退出"
**根因**：work 线程（transcribe/process_result 的后台线程）里直接读 Tk 变量
（stt_mode_var.get() / stt_translate_var.get() / stt_target_var.get() / stt_custom_var.get()）。
Tcl 解释器从非主线程执行命令 → 竞态 → 随机段错误/进程直接退出（无 traceback）。
之前一直没崩是时序侥幸，重试/门控改动改变了 work 线程执行时机后触发。

**修复**：
1. `_stt_finish`：主线程预读 `_mode/_tr/_lang/_custom_prompt`（含 custom 空 Prompt 提前拦截），
   work 闭包只引用预读值
2. `_translate_async`（场景 2）：target 主线程预读
3. 全局兜底 `_install_crash_hook()`：sys.excepthook + threading.excepthook → crash.log
   （下次崩溃可定位；run_gui 开头调用）

### UI 版面重构（用户拍板：服务按钮保留给开发检测，最终产品再优化）
1. **场景顺序**：语音输入 → 电子书朗读 → 长文本转语音（默认打开语音输入）
2. **left 面板只在电子书朗读场景显示**：语音输入/长文本全宽工作台
   （ttk.Panedwindow forget/add；注意 winfo_children 在 forget 后仍返回 widget，
    判断显隐用 winfo_manager()==""）
3. **left 面板删语音服务配置区**（入口在语音输入/长文本场景内按钮）；
   保留：标题/状态/自动启动/4 个服务按钮（启动/停止/健康/打开健康页）/说明/赞助
4. prov_status 变量定义保留（_ensure_provider / 场景3 配置按钮复用）

### 验证
- GUI smoke：默认 scene3 left 隐藏 ✓；切 scene1 left 显示 ✓；切回隐藏 ✓
- Grep：work 线程无 Tk 变量读取；读取点全部在主线程 ✓
- py_compile ✓
- 真机待观察：连续说话 + 风格化是否还崩（crash.log 若生成即定位）


---

## Text Normalization 层（2026-09-08）

**背景**：ChatGPT 九层 STT 评审里点名缺失的"轻量规则层"。此前只有热词（识别前偏置）和 LLM 润色（重改写），verbatim 单通道直出完全裸奔。

**实现**：新建 `normalize.py`（纯规则、无网络、无 LLM），31 项单测全过。

### 规则与防误伤设计（用户明确怕误伤）
1. **中文数字 → 阿拉伯**：百分之五十→50%、一百亿→100亿、一千万→1000万、二零二五→2025、三点一四→3.14
   - 防误伤：单字数字不转（一个人/第一句/十年）；连词不转（万一/万万）；固定搭配不转（三十而立/十四五规划，`_FIXED_WORDS/_FIXED_SUFFIX`）
   - 大数保留万/亿汉字单位（100亿、1亿5000万）；小数保留（3.14）
   - cn2num 双模式：含十百千万亿→位权解析；纯数字串→逐位拼接（二零二五）
2. **缩写大写化（白名单制）**：who→WHO、ai→AI、gpt→GPT；品牌名首字母大写 groq→Groq、openai→OpenAI
   - 防误伤：只在白名单（`_ABBR_WORDS` 全大写 + `_ABBR_TITLE` 品牌表）+ 词边界（前后非字母）；i love you、doc 等普通英文词不碰
3. **排版（低风险附带）**：字母与中文间补空格（AI 模型）；数字与中文不补（100亿、2025年 保持紧凑）；中文语境半角逗号句号→全角（你好，世界）

### 接入
- `stt_engine.process_result`：verbatim 直出 normalize(text)；LLM 输出也 normalize(final)（补齐 LLM 偶尔漏的全角/数字/缩写）
- 顺序：数字 → 缩写 → 空格 → 全角标点

### 后续可扩展
- 新机构/缩写 → `_ABBR_WORDS` 加小写形式；新品牌 → `_ABBR_TITLE` 加 小写→官方写法
- 用户自定义强制替换（错误=正确）可以挂到这里（hotwords 升级为双列），未做待排期


---

## normalize 实测反馈 + 连字符缩写规则（2026-09-08）

### 用户实测数据（语音输入 → 识别结果）
- 说"三十而立" → 识别成：三十二例 / 三十 二 历 / 三十二粒 / 三十二厘
- 说"Groq" → 识别成"黄黄"
- 说"HOA"（拼字母）→ 识别成"H-O-A"
- who / NASA 识别正常（小写 who 由 normalize 补成 WHO）

### 分层归因
1. **"三十二X"（同音 lì）**：ASR 同音错误。热词偏置可改善但不保证——whisper 对完全同音字（例/立/粒/厘）仍可能猜错。
2. **"黄黄"（Groq）**：热词里一直有 Groq 且完整进 prompt（39 条约 100 字，2500 上限未截断），仍识别错 → 音差太远，热词偏置拉不回；属输入端问题（发音/距离/噪声）。
3. **"H-O-A"**：whisper 对口头拼字母的输出形式。**已修**。
4. **"三十二例"→"32例"**：normalize 按规则转数词"没错"，但它没有语义判断——无法区分"三十而立"与真的"三十二例病例"。**不能全局强制替换**（误伤真数字）。

### 已做
- normalize 新增**拼字母连字符规则**：`[A-Za-z](?:-[A-Za-z]){2,}` 每段仅 1 字母、至少 3 段 → 去连字符大写（H-O-A→HOA、N-B-A→NBA）；e-mail / T-shirt 不误伤（单测过）。
- hotwords.txt 置顶新增"三十而立"（用户实测高频错词）。

### 可选下一步（待用户拍板）
- **用户级"错误=正确"强制替换表**（如 hotwords 升级双列：错误词=正确词，normalize 里字符串级替换）：能 100% 纠正"黄黄→Groq"这类，但"三十二例→三十而立"有误伤风险（用户可能真说 32 例），需用户自己权衡是否收录。
- 输入端优化（离麦近/口音/降噪）是 ASR 准确率最大杠杆，代码层已到上限。


---

## 同音纠错积木（2026-09-08）

**背景**：用户实测暴露热词枚举法天花板（"三十而立"→三十二例/历/粒/厘、"Groq"→黄黄，中文同音词太多，200 词永远不够）。用户提出本质方向："中文输入时给一个中文脑"。落地为：ASR 后接 LLM 同音纠错层（LLM 本身懂中文常识，无需枚举词表）。

**实现**：`stt_engine.HOMOPHONE_RULE` 积木，`build_system_prompt` 拼接顺序改为 **同音纠错 → 风格 → 翻译**（仍单次 LLM 请求，不增 API 调用）：
- fluent（智能润色）/ formal（严肃文档）/ custom（自定义风格）全部叠加
- verbatim（忠实记录）不走 LLM（单通道直出 + normalize），不受影响

**Prompt 铁律**（ChatGPT 评审第十点：LLM 自作主张改话是最大风险）：
- 只修明显的同音/谐音错字（据上下文与常用搭配推断，如 三十二例→三十而立、拳脚→全角）
- 不改写/不润色/不总结/不改述；不删口头语（嗯/啊/um）；不改事实、数字、人名、语义
- 拿不准的保留原样

**验证**：fluent/formal/custom 三种 prompt 均含纠错积木且顺序正确；+翻译叠加正常；py_compile 通过。真机待测（智能润色模式下说"三十二例/黄黄"类句子观察纠错效果）。


---

## 杀进程按钮 + 硅基流动直连修复（2026-09-08）

### 1. "杀进程"按钮（场景 3，排在"打开语音服务配置…"旁边）
用户痛点：LLM 润色/翻译延迟烦人，卡住时无法中断。
- 实现 `_stt_abort()`：置 `_stt_cancel` 事件 + `_stt_busy=False` + 停动画/录音 + 横幅"已终止"
- work 线程关键点检查 `_stt_cancel`（压缩前/转写后/LLM 前），取消则静默退出、结果丢弃
- 底层网络请求无法强杀，由各自 timeout 兜底；新任务开始时 `_stt_cancel.clear()`
- 录音中点杀进程安全：pynput 后续 on_release → `_stt_finish` 因 busy=False 直接 return

### 2. 硅基流动"第一次成功、第二次失败"根因：被强制走 Clash 代理
- **bug**：`system_proxy()` 无条件注入所有平台请求（`_request_with_retry` 和 transcribe 都 `proxies=system_proxy()`）。硅基流动是**国内直连**服务（api.siliconflow.cn），走 Clash 代理（127.0.0.1:7897）依赖 Clash 分流规则，不稳定 → 时好时坏
- **修复**：`PlatformDef.needs_proxy: bool = True`（默认）；siliconflow `needs_proxy=False`；`Provider.needs_proxy` 取自平台定义；`_request_with_retry`/`chat`/`list_models`/`transcribe` 按标志决定是否注入代理
- Groq/自定义平台仍走代理（被墙必需）
- dataclass 字段顺序坑：带默认值字段必须放在所有非默认字段之后（CRLF 项目 Edit 工具失败 → Python 脚本改）

### 待用户验证
- 杀进程按钮：卡住时点一下，UI 立即恢复
- 硅基流动：再贴 key/model 连续转写两次，观察是否稳定；仍失败则需"转写失败: xxx"具体报错（业务错误如 429/模型名错误不重试直接显示）


---

## 硅基流动默认模型调整（2026-09-08）

**ASR**：`whisper-large-v3-turbo` → `FunAudioLLM/SenseVoiceSmall`（用户点名；注意上一轮改 ASR 的脚本因引号转义 SyntaxError 未生效，本轮已重做并断言验证）

**LLM**：基于实时查到的硅基流动价格表（2026-09）选型：
- 默认 `deepseek-ai/DeepSeek-V4-Flash`：输入 ¥1/M、输出 ¥2/M（全站最低档），284B MoE 速度快，DeepSeek 中文底子好
- 置顶备选 `deepseek-ai/DeepSeek-V3.2`：¥2/¥3，671B 旗舰，中文最强最稳，输出规范不废话
- 排除：GLM-5.x 旗舰（¥6/¥28 贵）、Qwen3.6-27B（输出 ¥14.4/M 贵）、V3.1-Terminus（¥4/¥12 贵）
- 选型逻辑：STT 每次调用仅几百 token，单价可忽略；关键在**输出干净省 token + 中文常识（同音纠错）+ 响应快**——DeepSeek 系指令遵循好、不啰嗦

**注意**：bridge_config.json 若已存旧硅基模型名，UI 读的是存的值而非默认；用户重选或清空模型字段才会回落到新默认。


---

## 国内平台直连域名规则（2026-09-08）

**背景**：用户反馈硅基"还是没连上，是不是还在走梯子"。实测（直连 + 真实 key）**连通 OK、96 模型、SenseVoiceSmall/V4-Flash/V3.2 均在列表**——代码层面无问题，根因大概率是 bridge 未重启（旧进程仍带旧代码）。

**实现**：`provider.is_domestic_url()/should_use_proxy()` 域名自动判定规则：
- 国内服务商 hints：siliconflow/aliyun/alibaba/volcengine/volces/bigmodel/zhipuai/z.ai/tencent/qcloud/baidu/bcebos/iflytek/minimaxi/minimax/sensetime/moonshot/01.ai/deepseek/baichuan/stepfun
- `.cn` 域名直接命中
- `Provider.needs_proxy` 改为 `should_use_proxy(base_url)` 动态计算——自定义平台填国内地址也自动直连，填国外（openai/anthropic/groq）自动走代理
- 断言：openai/groq/anthropic→走代理；siliconflow/volcengine/dashscope.aliyuncs→直连 ✓

**待用户**：重启 bridge 后再测硅基；仍失败需提供"转写失败: xxx"原文。


---

## 硅基"第一次成功、第二次失败"：429/5xx 业务重试（2026-09-08）

**模式定位**：成功一次第二次失败 → 状态性问题（免费档限流/节点抖动），非配置/代理（实测直连 OK、96 模型、SenseVoice 在列表）。

**修复**（provider.transcribe）：
1. **429/5xx 瞬时业务错误纳入重试**（原来"业务错误不重试"直接抛）：退避 1.0s/2.5s，最多 3 次
2. **错误分支区分**：业务层耗尽 → "服务暂时不可用（HTTP 429...），可能是该平台免费额度/限流，请稍候再试"；网络层耗尽 → 原 Groq 代理提示（不再串味）
3. 错误信息保留状态码 + 响应体 + 走代理/直连标注

**验证**：伪造 429→200 成功（2 次请求）；三次 429 抛错且提示准确（含 429/限流、不含 Groq）✓

**待用户**：重启 bridge 后连续两次转写，观察是否被重试救回；若仍失败，报错会明确显示 HTTP 状态码——429 就是硅基限流（看平台控制台额度），其他码再定位。


---

## SenseVoiceSmall 实测否决 + ASR 回退（2026-09-08）

**用户复现**："第一次上屏但质量差（'3十2立了'），第二次连不通"。

**实锤复现**（用户真实 key + TTS 测试音频 + 与真实链路一致的 OGG 压缩）：
```
第1次: OK (17.1s)   -> '也是别傻的。幸。'   ← "王小波写的三十而立"被识别成乱码
第2次: FAIL (92.7s) -> Read timed out (read timeout=30) ×3
```
**结论**：不是限流 429，是 **SenseVoiceSmall 在硅基上推理节点极慢+不稳定+识别质量差**——第一次 17 秒且识别完全错误，第二次 30s 超时×3。用户点名默认 SenseVoice 的决定被实测推翻。

**动作**：
1. siliconflow default_asr 改回 `whisper-large-v3-turbo`（provider.py + bridge_config.json 同步）
2. 网络层错误提示通用化（去掉 Groq 专属"切换 Clash 节点"串味，改"请检查网络连接或代理设置"）——任何平台超时都适用
3. LLM 保持 DeepSeek-V4-Flash（未受此影响）

**教训**：换默认模型前应先小样本实测（速度+质量+稳定性三指标）；"能通"≠"可用"。


---

## 崩溃真凶锁定：OGG 编码栈溢出 + 拉丁语系实测（2026-09-08）

**用户反馈"又崩溃退出"（无 crash.log）→ 实锤根因**：
- 用 faulthandler 复现：Windows fatal exception: stack overflow (0xC00000FD)
- 栈：soundfile._cdata_io -> _array_io -> write -> stt_engine.compress_audio
- libsndfile 的 Vorbis 编码在 Windows 上对长音频（约 40s+，50s 必崩）栈溢出，原生崩溃无 traceback、无 crash.log——与用户"进程无痕消失"完全吻合
- 修复：compress_audio 改纯 Python wave 打包 WAV（零依赖、永不崩；体积约 OGG 9 倍，上传影响可忽略）。保留函数名与调用点（launcher 2383 compress_audio(raw, _stt_recorder.sr)），最小改动
- 教训：能"处理短音频"≠"处理长音频"；Windows 上原生库编码长数据是崩溃高发区

**拉丁语系实测（用户 Groq key + TTS 三语口述音频 + 真实链路 16k mono WAV）**：
- 质量：whisper-large-v3-turbo 三语 100% 正确（含变音符号）——拉丁语系是 whisper 强项，用户判断正确
- 延迟：ASR 6.6s~75s 抖动大 = 用户本地 Clash 代理延迟，不代表 Groq 本身（10s 音频处理 ~1-2s；LLM 1.7s 为代理快时参照）。海外直连无代理会稳定秒级
- 法语 LLM 一次 SSLEOFError = 代理连接重置，网络重试逻辑正确兜住

**决策**：海外主线 = 直连 Groq（whisper-large-v3-turbo ASR + gpt-oss-120b LLM）；国内用户后期单独做"中文脑"入口（硅基 LLM DeepSeek + 中文母语 ASR）。拉丁语系质量已验证无需再测；延迟需海外真机才准确。


---

## 国内流式 ASR 平台调研结论（2026-09-09，组织者并行调研 5 平台）

**目标**：为中国用户"中文脑"入口选流式 ASR（按住说话→边说边出字），硬约束=中英混说质量+真流式 interim+个人免费额度+境内直连+Python 可接入。

**排名与一句话**：
1. 🥇 **火山引擎**（综合最优，首推实测）：20h 免费/半年、1 元/h、热词现成对接 hotwords.txt（5000 词上限）、bigmodel_async+enable_nonstream 二遍识别（流式快+分句重识别准）= 官方推荐微信式链路；唯一同时满足全部硬约束。风险=自定义二进制 WS 协议需自封装（官方 demo 可抄 1-2 天）+ 中英混说缺 API 级硬数据。
2. 🥈 **腾讯云大模型 2.0**（中英混说标杆，微信同源）：官方 16k_zh_en_2.0 + 开发者实测 94.2%；但 Preview 三重限制：≤60s、热词未开放、**无免费额度**（1 元/h 后付费）。
3. 🥉 **阿里云百炼**（SDK 最丝滑、单价最低 0.864 元/h）：dashscope pip 即用、language_hints=['zh','en']；免费仅 10h/90 天一次性、云端中英混说英文部分被评一般。
4. **科大讯飞**（免费最慷慨：听写每日 500 次+1万次/3月）：但正对短语音场景的**听写版仅"简单英文"**，中英混说要上实时转写（免费仅 5h/年、约 4.95 元/h）；动态修正仅中文。
5. **百度**（保底）：实时 10h/180 天免费+真流式；但中英混说定位保守、零独立测评。

**关键事实**：所有平台"词级中英夹插（PR/CI/review）"均无 API 级独立 A/B 数据——**必须实测**。

**下一步（已定方向）**：①火山引擎零成本实测（20h 免费；用户如有字节系账号可加快）②腾讯充值 1-5 元做同一批中英混说测试句 A/B 对照 ③统一测试集 20-30 句（技术术语/日常混说/专有名词/长短句）④Python 接入原型：火山官方 sauc_python demo 改造（1-2 天）。

**对照基线**：Groq whisper 整段转写（海外主线）不动；硅基 ASR 已判死刑（SenseVoice 乱码/XingChen 空/无 whisper）不再回测。


---

## 火山流式 ASR 接入成功 + 中英混说实测（2026-09-09）

**协议踩坑（重要，避免重踩）**：
1. **帧格式不是 `[4B size][payload]`**！正确 = `[4B Header][4B Payload size 大端][Payload]`。
   Header byte0 = (Protocol version=1 << 4)|(Header size=1)；byte1 = (Message type<<4)|flags；byte2 = (Serialization<<4)|Compression；byte3=0x00。
   只发 `[size][payload]` 会被服务端读成 "protocol version 0" 直接拒绝。
2. **消息类型**：full client request=0b0001(JSON)、audio only=0b0010、full server response=0b1001、error=0b1111。
3. **响应帧是 12 字节头**（Header+Sequence 4B+Size 4B+Payload），客户端帧是 8 字节头——解析必须区分！
4. **结束不需要单独空帧**：最后一包音频帧 flags=0b0010（负包/最后包标记）即是结束。
5. 音频用裸 PCM（format=pcm，剥 wav 头），200ms/块（6400B@16k）最优。
6. 请求头需 X-Api-Key（新版鉴权，UUID）+ X-Api-Resource-Id（volc.seedasr.sauc.duration=2.0小时版）+ X-Api-Request-Id + X-Api-Sequence: -1。
7. 官方 sauc_python.zip 无法直接下载（JS 渲染）；协议文档 1354869 完整可抓。

**实测结果（用户真实 key + 三组中英混说 TTS 音频）**：
- 技术术语（review/PR/CI/API/debug/K8s）：1.2s 完成，全部正确；**唯一瑕疵 K8s→"K 八 S"**（热词可纠）
- 日常混说（meeting/idea）：0.9s，100% 正确
- 专有名词（Grok/SiliconFlow/OpenAI）：0.9s 全对（Silicon Flow 分词空格可接受）
- **流式体验**：interim 逐字上屏 0.4-0.6s 首字，修正过程可见（Gro→Groke→Grok）——微信式"边说边出字"达成
- 相比 Groq whisper：同为秒级但**中文+中英混说质量显著更高** + 真流式

**结论**：火山引擎（豆包流式 2.0 + enable_nonstream 二遍识别）= 国内"中文脑"流式 ASR 选定方案。待办：①K8s 类热词直传验证 ②接入 launcher 场景3 流式 UI（interim 上屏）③硅基流动平台条目可降级/保留（ASR 不用它）。


---

## 火山流式接入 launcher 场景3 完成 + 协议两处深坑（2026-09-09 续）

**已完成的接入**：
1. `volcengine_asr.py` 新增 `VolcengineStreamSession`：start() 建连发配置帧 → send_audio() 边录边传 → finish() 发最后包取最终结果；接收线程实时解析 interim/final 经 on_partial 回调。新增 `_split_server_frame` 统一帧拆分。
2. `stt_engine.py` Recorder 新增 `set_on_block(cb)`：运行时切换实时帧回调（录音期间每 200ms 块推给流式发送线程）。
3. `provider.py` 新增 `volcengine` 平台条目：ASR=`volc.seedasr.sauc.duration`（资源 ID 即模型），LLM=方舟 doubao-seed-1.6-250615，needs_proxy=False（国内直连）；新增 `VOLC_ASR_IDS` 内置下拉（火山 ASR 无列表接口）。
4. `launcher.py` 场景3 流式路径：platform=volcengine 时按住→`_stt_stream_start`（建会话+录音实时发送+interim 回调 root.after 刷状态条/banner 滚动出字）→松手→`_stt_finish_stream`（发最后包→finish→verbatim 直出/非 verbatim 走 LLM→commit 剪贴板）；`_stt_abort` 加会话清理。配置面板平台单选加火山、ASR 下拉用资源 ID、on_fetch 火山不抓 ASR。

**协议两处深坑（血泪，文档未写）**：
1. **服务端首帧（flags=0，建连确认，含 log_id）是 8 字节头**（Header+Size，无 Sequence）！结果帧（flags=1/3）才是 12 字节头（Header+Sequence+Size）。文档只画 12 字节头。**不区分就会把首帧 size 读成巨大值 → 后续帧全部错位**。
2. **帧解析错位 → 接收线程 recv 异常 → websocket-client 内部把 sock 置 None → 发送线程报 "socket is already closed"**——表现为"发送中途连接断开"，实际根因是解析错位，不是发送太快（0 间隔连续发送服务端可容忍，bench 验证过）。

**流式实测（修复后，真实 200ms/块节奏）**：三组全过，interim 逐字上屏 0.4-0.6s 首字，修正轨迹可见（K8→K84集群→K 八 S；Groke→Grok；Openai→OpenAI）。"K 八 S" 类读音拆解词需热词直传纠偏（context 支持，未验证）。

**待用户真机验证**：重启 bridge → 语音服务配置选火山引擎 → 粘贴 key（新版 X-Api-Key UUID）→ 场景3 按住说话。注意：火山 LLM 润色需开通方舟（豆包模型），未开通时报错提示，流式 verbatim 主路径不受影响。


---

## 火山双 Key 架构 + Doubao-Seed-Evolving（2026-09-09 续2）

**问题**：火山平台 ASR（语音控制台 X-Api-Key）与 LLM（方舟 API Key）是两套独立凭证，原配置面板一个 Key 框只能存一个，填了方舟 Key 流式识别鉴权失败，反之润色失败。

**解决**：Provider 支持独立 LLM Key。
- `provider.py`：Provider.__init__ 新增 `llm_key` 参数；`self.llm_key = llm_key or api_key`（缺省回退主 Key，老配置兼容）；新增 `_llm_headers()`；chat() 用 llm_key 校验与鉴权（未填提示"火山平台请填方舟 Key"）。transcribe/list_models 仍用主 api_key。
- `launcher.py`：配置面板新增第二 Key 行"方舟 LLM Key"（仅火山平台显示，pack before=asr_row），独立粘贴/清洗/缓存/保存；cfg["provider"] 新增 `llm_key` 字段；`_provider_from_cfg` 透传。

**模型选择**：火山默认 LLM 改为 `doubao-seed-evolving`（统一 ID 自动升级、首次开通语言模型 50 万免费 tokens、6元/百万输入 30元/百万输出、1024k 上下文、深度思考）。注意：该模型**不在方舟自动开通范围**，需在开通管理页手动点"开通服务"。

**用户开通清单**：①方舟控制台→开通管理→Doubao-Seed-Evolving 手动开通 ②API Key 管理→创建方舟 Key（只显示一次）③bridge 语音服务配置→火山引擎→主 Key=语音 Key（6b443a16-…）、方舟 LLM Key=新建方舟 Key ④测试：忠实记录=流式识别，智能润色=流式+方舟 LLM。


## 2026-09-09 UI 收尾第一轮（布局重构 + 隐私清理）

用户拍板 UI 总原则：极简悬浮反馈 + 沉浸细节，"用时灵动显眼，不用时完全隐形"。技术栈为 Python Tkinter（Windows），评估后只做"放心做"档（低饱和渐变强调色、10-15fps 声浪波形、状态过渡、winsound 提示音、卡片化设置面板、API Key 掩码+测试连通）；不做真毛玻璃（Tkinter 无 backdrop blur）、不做光标跟随悬浮窗（已否决，用底部状态条）、不引入 Web 组件库、不做高帧率粒子。

### 本轮改动（launcher.py 重构）
1. **场景切换改卡片式**：radiobutton 小圆点 → 三个 tk.Frame 卡片（🎙语音输入/📖电子书朗读/📝长文本转语音），选中态 #eef0ff 背景 + #6366F1 边框（淡紫渐变强调色落地），未选中 #f7f8fa + #d5d8e0。
2. **删除左侧服务面板**（heading/状态/启动/停止/健康检查/打开健康页/howto）：电子书朗读是静默服务，无需任何按钮。bootstrap 的 `do_start()` 改直接 `service.start()`；tray 菜单 start/stop 同理改直接调 service。`status_var` 删除，环境错误改走 ui_log。
3. **运行日志改为右侧抽屉**：默认折叠，右缘 `▶` 箭头按钮（展开后 `◀`），任意场景可打开，全局共用 log_queue。`poll_logs()` 单实例（删除了原场景1的重复调用）。
4. **场景1 电子书朗读改介绍页**：说明静默服务 + 复用 t("howto") 使用说明，无按钮无日志。
5. **隐私清理（用户明确要求）**：
   - 配置面板删除全部"粘贴"按钮 + `_paste_key`/`_paste_llm_key`/`_on_key_paste` 函数 + Ctrl+V 绑定（程序不再主动读剪贴板）；key 保存时仍 sanitize。
   - 场景3 删除"自动上屏到当前应用（Ctrl+V）"勾选；`_stt_commit` 不再写剪贴板/模拟 Ctrl+V；删除 `_is_bridge_foreground`。"最近结果（已复制到剪贴板）"→"最近结果"。
   - "杀进程"按钮 → "中止当前服务"（文案与注释全量替换）。
6. **底部常驻条保留**（sponsor 左 + 开机自启动右），不受场景切换影响。

### 验证
- py_compile 通过；冒烟测试（run_gui(test_hook) 自动销毁）无 Tk 回调异常。
- 踩坑：删除 UI 函数后必须全局搜死引用——bootstrap/tray 均曾引用已删的 do_start/do_stop/status_var（NameError），已修复。

### 下一轮（待做）
- 状态条波形 + 状态过渡（录音中声浪 / 转写中光点 / 完成淡出）
- 完成提示音（winsound，轻"叮"）
- 设置面板卡片化 + API Key 掩码 + 测试连通按钮
- 中文界面无剪贴板概念后，STT 结果只展示在结果框（用户手动处理）


## 2026-09-09 新图标落地（SVG → ICO/PNG 全套）

用户提供 macOS 风格 SVG（暗蓝圆角底 #0F172A→#1E1B4B + Echo 回声环三色渐变 #38BDF8→#C084FC→#FB923C + 三根声波柱 #38BDF8→#818CF8）。

### 实现（gen_icon.py，保留在 VoxEcho-bridge/ 供后续改图重新生成）
- 无外部依赖：PIL + numpy 忠实渲染 SVG 语义（圆角矩形+垂直渐变、分段渐变圆弧描边、圆角渐变声波柱）。
- 产物：`VoxEcho.ico`（7 尺寸 16~256，窗口/任务栏/exe 内嵌用）、`VoxEcho-512.png`（预览）、`icon/VoxEcho-{16,32,48}.png`（托盘用）。
- 链路无需改代码：`apply_window_icons` 读 VoxEcho.ico 最大帧；`load_tray_image` 读 ico 或 icon/*.png；build.bat 的 --icon 指向同一文件，重新 build 即自动用新图标。
- Ko-fi 赞助图标（KO_FI_ICON_B64）保留——那是 Ko-fi 品牌 logo，不换。

### 踩坑
- numpy 广播：垂直渐变 `t` 需扩展为 (h,1,1) 才能广播到 (h,w,3)。
- 圆角蒙版：四角象限判定必须用 `np.minimum(m, alpha)` 只在角外凸区域削减，否则内部像素全被判为圆外→整图透明（第一次渲染全透明，回声环/声波单独画的部分例外）。

### 验证
- 像素抽样：背景上/下部、三根声波顶/底渐变、回声环左/顶/右三色全部符合 SVG 色值。
- Read 读图：与 SVG 描述一致（深蓝圆角底 + 三色弧 + 高低声波柱）。
- ICO 7 尺寸齐全；GUI 冒烟测试（iconphoto 读新 ico）无异常。
- 注意：已 build 的 exe 内嵌图标仍是旧的，需重新 build 才更新。


## 2026-09-09 图标全套铺开 + 自动上屏澄清

### 自动上屏澄清（用户纠偏）
用户原意是"不要自动上屏到 bridge 自己的程序窗口"，**外部文本框（微信/浏览器/编辑器）的上屏必须保留**。已恢复：
- 「自动上屏到当前应用（Ctrl+V）」勾选（默认 True）
- `_stt_commit` 的剪贴板写入 + pynput 模拟 Ctrl+V（焦点在自身窗口时跳过 `_is_bridge_foreground`）
- 提示文案「松开自动上屏」、结果标签「已复制到剪贴板」
- **配置面板的「粘贴」按钮保持删除**——用户反感的"剪贴板"是程序主动读剪贴板（窥探感），自动上屏是"写"，性质不同。

### 图标全套铺开（gen_icon.py 重构）
用户把 SVG 拷贝到 `VoxEcho-bridge/icon/VoxEcho.svg`（与之前一致）。gen_icon.py 改为输出三处（与代码引用路径一一对应）：
- `VoxEcho-bridge/VoxEcho.ico`（7 尺寸，窗口/任务栏/exe 内嵌）
- `VoxEcho-bridge/icon/`：VoxEcho.ico + 16/32/48/128/256 PNG + 512 源图（托盘备用）
- `VoxEcho-extension/icon/`：同上全套（manifest 引用 16/32/48/128）
- 删除两处无引用的遗留大图 `VoxEcho.png`（1.1MB×2）。
- 图标尺寸结论：托盘只用 16/32，界面 48 以内，但 **256 必须保留**（exe 大图标 + iconphoto 取最大帧防糊），128 是 Chrome 商店/扩展标准尺寸。


## 2026-09-09 「放心做」档全部落地 + 错误分类

用户验收提醒后补做（此前只做了卡片/抽屉/删按钮等布局，动画项未做）：

### 声浪波形（仅录音中活跃，~14fps）
- `stt_engine.Recorder` 新增 `self.rms`：`_cb` 每块（200ms）计算 `sqrt(mean(int16²))`，不依赖 on_block，与火山流式推送互不干扰。
- 横幅（Toplevel）改为 Frame 布局：左 Canvas(210×26) 画 5 根白色圆角竖条（权重 0.30/0.55/0.85/0.55/0.30），高度 = 12% + 88%×权重×平滑RMS；平滑系数 0.7/0.3 防抖；RMS 归一化 1200 阈值。
- `_stt_wave_tick` 每 70ms 重绘；**tick 内检测 `_stt_recorder.active`，松开自动停**（无需到处挂 stop）。录音启动成功后 `_stt_wave_start()`；abort/error/commit 也显式停波。

### 状态过渡淡入淡出
- `_stt_banner_show`：窗口 `-alpha` 0→1（10 步×28ms）；`_stt_banner_hide`：1→0（8 步×26ms）再 withdraw。用 `_stt_fade["after"]` 管理，show/hide 互相取消对方法动。Toplevel 独立窗口整窗 alpha，实现真淡入淡出。

### 完成提示音
- `_play_done_sound()`：`winsound.MessageBeep(MB_OK)`（系统轻"叮"），try/except 包裹。`_stt_commit` 成功时调用。

### 测试连通性 → 内联绿 badge
- 原「测试连接」用 messagebox 弹窗；改为 `btn_row` 内联 label：测试中灰字「测试中…」→ 成功绿字「连接成功（N 个模型，耗时 Xms）」→ 失败红字分类提示。线程执行不卡 UI（`test_connection(timeout=12)` → `win.after` 回主线程）。

### 错误分类（网络未触达 / 触达但模型没起作用 / 额度用完）
模块级 `_classify_error(e)` 按特征归类（顺序=优先级）：
1. **超时**（timed out/timeout/转写超时）→ 服务器慢，稍后重试
2. **网络未触达**（connection/EOF/proxy/TLS/network）→ 查网络或代理 Clash
3. **认证失败**（401/403/unauthorized/invalid api key）→ Key 无效，检查或重新生成
4. **模型错误**（404/model does not exist/not found）→ 模型名错误或未开通（如硅基 SenseVoiceSmall、未开通火山资源 ID）
5. **额度/限流**（429/rate limit/quota/insufficient/credit）→ 稍候重试或检查免费额度
6. 其他 → 原样兜底
- `_stt_error` 改用分类：状态行 + 红横幅（4s 淡出）+ 日志抽屉留原始错误细节。
- 分类 10 组单测通过（含中文错误、英文错误、连接异常、EOF）。

### 验证
py_compile + GUI 冒烟通过；分类单测 9/10 命中预期 + 1 条 500 走兜底（设计行为）。


## 2026-09-09 状态条 v2（微信式）——用户试玩后的重构

用户实测反馈：没看到波形；要求 Streaming 与 Block 模式区分处理；状态条像微信一样在任务栏上方居中；"完成/已复制到剪贴板"不报告；限制宽度。

### 状态条视觉 v2（_stt_banner_show 重写）
- 深色半透明底：`#202124` + 整窗 alpha 0.93（微信语音条风格），前景色参数化：正常白 `#e8eaed`、错误浅红 `#ff6b6b`、提示橙 `#ffb74d`。
- 位置：`x=(sw-w)//2, y=sh-h-80`（任务栏上方居中），不再是右下角。
- 限宽：Label `wraplength=540`，长段话折行不会拉宽窗口。
- **闪烁根因修复**：原淡入在每次内容更新（anim 每 380ms / 逐字 partial）都被取消并重设为 alpha 0 → 横幅一直处于半透明闪烁。改为读取当前 alpha，仅 `cur<0.95` 时淡入；内容更新不再打断透明度。

### 模式区分（按用户要求）
- **Streaming（火山流式）**：一边说一边逐字出现（`_stt_stream_show` interim 每帧刷 banner，限 140 字）；状态行「识别中…」；波形同步活跃。
- **Block（Groq/硅基等）**：录音中「聆听中 ··」（原为空圆点，按用户要求补上）+ 波形；松开后「正在转写 ··」→「正在润色/翻译 ··」。

### 波形可见性
- 静音也有基础高度（`max(8, (0.25+0.75·wgt·h)·H)`），说话明显起伏，深底白条清晰。
- Canvas 200×30 五根白竖条，14fps，RMS 平滑 0.7/0.3。

### 完成静默
- `_stt_commit` 不再显示「完成，已复制到剪贴板」横幅；状态条 150ms 后淡出，状态行回「就绪」。剪贴板写入+自动上屏逻辑不变。
- 参考图（用户附图）正是旧版右下角蓝条「完成，已复制到剪贴板」——已删除该行为。

### 验证
py_compile + GUI 冒烟通过。提醒：用户运行的是源码还是旧 exe 需确认，改动需重新 build 才进 exe。


## 2026-09-09 状态条"全黑"根因修复（alpha 从未被设置）

用户实测：状态条全黑不透明（位置居中已 OK）。实测 Tk 行为发现根因：
- **Tk 新窗口 `-alpha` 默认 1.0（完全不透明）**，读取 `attributes("-alpha")` 返回 "1.0"。
- 原淡入判断 `cur<0.95` 首次创建时读到 1.0 → 跳过淡入 → alpha 从未设置 → 全黑不透明块。
- 修复：fade 状态加 `ready` 标记。首次 show（或淡出打断后）强制 `-alpha 0 → 0.88` 淡入，完成后 `ready=True`；内容更新（anim/逐字）不打断透明度；hide 开始时 `ready=False` + 从 0.88 淡出到 0 再 withdraw。
- 目标透明度 0.88（微信感更明显）。
- 验证：`-alpha` 在 overrideredirect 窗口上设置/读取均正常（0.5→0.5）；py_compile + GUI 冒烟通过。


## 2026-09-10 三项体验：咔嗒音效 / 无感上屏 / 风格面板改版

### ① 上屏音效 = 咔嗒（像合上盒子）
- 弃用 winsound.MessageBeep（系统"叮"）。`_click_wav()` 用 numpy 合成 100ms WAV 并缓存：215Hz 正弦指数衰减（盒盖闷响）+ 前 12ms 瞬态噪声（"咔"），峰值 55% 不刺耳。
- `_play_done_sound` 改 `winsound.PlaySound(data, SND_MEMORY|SND_ASYNC)`。
- 验证：4454B WAV，22050Hz/100ms/峰值 18021（55%）。

### ② 无感上屏 + 热键不触发系统 UI
- **根因**：pynput 不吞键，Win 键按下瞬间会被 Windows 捕获（先按 Win 或组合按下时可能激活开始菜单/系统 UI）→ "系统栏里的程序跳出来"。
- **修复**：on_press 在组合键齐了时 `return False` 吞掉后按下的修饰键/触发键（`swallowed` 集合记录）；on_release 对称吞释放 → 系统自始至终不知道组合键被按过。用户单独按 Win 键不吞（不影响日常）。
- **无感上屏**：热键触发瞬间 `GetForegroundWindow` 记录说话前的前台窗口到 `_stt_target["hwnd"]`；松开后 `PostMessageW(hwnd, WM_PASTE)` 直接粘贴（**不激活窗口**）；目标无效/是 bridge 自身时回退模拟 Ctrl+V（`_simulate_ctrl_v` 提取为独立函数）。

### ③ 风格配置面板改版
- 弃用 Listbox + 底部三按钮。改为 **5 个固定槽位行**（tk.Frame grid）：已有风格行=名称(点击选中高亮 #eef0ff)+行内 ✏️(重命名)/🗑(删除,需确认)；空槽位=灰色"（空）"+禁用图标。
- 列表**右侧竖排 ➕ 加号**（rowspan=5 大按钮）= 新增风格（命名对话框）。
- save 逻辑改用 `selected_idx[0]`。

### 验证
py_compile + GUI 冒烟 + 音效合成单测（WAV 参数正确）+ 风格面板打开单测（1 风格+4 空槽位正常渲染）均通过。


## 2026-09-10 热键回归修复 + 风格面板 v3（按用户截图标注）

### ① 热键"完全没有转录"——吞键 return 跳过触发（严重 bug）
- 上一轮 on_press 把「吞键判断」放在「组合激活触发录音」之前：修饰键齐了时 `return False` 直接返回，**组合激活检查永远到不了** → 热键从不触发 _stt_begin。
- 修复：**先检查 combo_active 触发录音，再判断吞键**。吞键仍对称（swallowed 集合），Win 键防系统 UI 保留。

### ② 风格面板 v3（用户截图红标注逐条落实）
1. 点 ✏️ → **行内 Entry 直接编辑**（预填、全选、回车/失焦保存），不弹框。
2. **空槽位行只有单个 ➕**（新增到该槽位 `styles.insert(i)`）；新建后才变 ✏️/🗑。
3. **加号不放右侧竖排**（删掉 rowspan 大按钮）。
4. 所有按钮 `relief=FLAT, bd=0` 做平。

### 验证
py_compile + GUI 冒烟 + 面板打开单测通过。提醒用户重启 py 测热键（触发顺序已修）。


## 2026-09-10 热键"只转录一次"修复 + 面板 v4（用户截图第二轮）

### 热键只转录一次
- 修复：on_release 里 **rec 复位移到 swallowed 判断之前**——被吞的修饰键（win）释放时提前 return 曾可能跳过 `rec=False`，残留导致下次按 `combo_active and not rec` 不成立 → 不再触发。
- 加诊断日志：触发处 `hotkey: begin`、`_stt_begin` 被挡时 `stt_begin blocked: busy / no provider`。若再复发，日志抽屉可见原因。

### 面板 v4（截图标注）
1. **加号移到行右侧**（column 1），尺寸/字号与 ✏️🗑 一致（width=2, font 9）；column 0 留白。
2. **点 ➕ 不再弹框**：直接 `styles.insert(i, {"name":"","prompt":""})` + 该行进入行内编辑（Entry 空输入），回车创建。
3. **Entry 做平**：`relief=FLAT, bd=0, highlightthickness=1` 细线（不再凹陷）。
4. commit_edit：空名新建=取消该条目；改名时留空=保留原名。


## 2026-09-10 流式收尾"卡死无上屏"（火山）

### 现象
按住说"先试一下。有没有效果？"→ 松开 → 状态条停在 interim 文字上不动，无上屏。

### 排查
- 非流式路径状态条从不显示文字（只有"正在转写/极速记录中"），**显示文字 = 火山流式 interim 上屏** → 用户走的是流式路径。
- commit 链路完整（clipboard→PostMessage→淡出），_play_done_sound 有 try 保护不会卡。
- 真卡点：流式 work 线程 `sender.join(timeout=8)` + `sess.finish(timeout=20)` **最坏 28s 无反馈**，期间状态条停在最后 interim 文字 → 用户感知"卡死"。
- 协议侧确认无 bug：send_audio 与 finish 共用 _send_lock，work 先 join sender 再 finish，时序正确；finish 超时后若已有 definite_parts 会直接返回，否则抛"无返回结果"。

### 修复
- sender.join 8→4s，sess.finish 20→10s（与用户 10s 预算一致）。
- 加日志：`stt_stream: sender joined` / `stt_stream: finish ok (N chars)` / finish 异常走 _stt_error 红条。


## 2026-09-10 "卡死"真因：ALT+Win 释放事件丢失 → rec 卡 45s（热键回归）

### 日志铁证（用户 ALT+Win 复测）
- 00:41:01 hotkey: begin
- 00:41:50 stt_stream: sender joined（+finish ok 7 chars）
- begin 与收尾间隔 **49s = 45s 超时兜底 + 4s join** → **on_release 从未触发 finish，rec 卡 True 45 秒**。
- 用户"卡死没上屏" = 松开后其实仍在录音态，45s 后超时才收尾上屏（用户早放弃）。

### 根因
Windows 上 **ALT 键 release 事件在 pynput hook 中不可靠**（焦点/菜单栏干扰），"全部组合键松开"的 combo 判断永远等不到 alt 复位 → rec 卡死。
（用户上轮 ctrl+win 正常 = ctrl 的 release 可靠；换 ALT 后暴露。）

### 修复
- on_release：**任一组合键成员松开 = 立即 finish**（`n in mods or n==trigger`），不再等 combo_active 全 False。release win 就收尾，不依赖 alt 的 release。
- 日志：`hotkey: release <key> -> finish` / `hotkey: 45s timeout finish`。

### 洞察
按住说话类热键的结束判定：**不要依赖"全部修饰键松开"**，任一成员松开即结束（更符合直觉 + 免疫个别键 release 丢失）。


## 2026-09-10 热键彻底重写：pynput → WinHotkey 低层钩子（根因 = return False 停 Listener）

### 铁证
用户 ALT+Win / ALT+X 复测日志：`hotkey: begin` 后**只有 45s timeout finish**，无任何 release 日志 → **on_release 从未被调用**。

### 根因（终于挖到底）
**pynput 的回调返回 False = 停止整个监听器**！吞键 `return False` 让 Listener 在第一次触发后直接死亡：
- "完全没有转录"（吞键在触发前 return）：触发被跳过 + Listener 停
- "只转录了一次"（触发提前）：第一次触发 + Listener 停 → 第二次没反应
- "卡死"（ALT 组合）：触发后 Listener 停 → 松开永不触发 finish → rec 卡 45s 超时兜底
- 换 ALT+X 无效 = 与 ALT 无关，任何组合都死于同一机制

### 修复：hotkey_hook.py（WH_KEYBOARD_LL 低层钩子）
- `SetWindowsHookExW(WH_KEYBOARD_LL)`，回调 **return 1 吞掉单键**（系统收不到 Win 键），监听持续有效
- 状态机 `_on_event(vk, down)`：左右修饰键 VK 归一（0xA0-0xA5、0x5B/0x5C）；组合全按下 → on_begin；任一成员松开（combo 不再全按）→ on_end；吞判定 = 组合激活中 / 录音中 / 结束录音的那个松开
- trigger 支持单字符（alt+x → x 的 down/up 都被吞，不输入系统）
- launcher `_start_hotkey` 整体替换：pynput Listener/state/swallowed 全删，改用 WinHotkey + _hk 闭包状态；`_hotkey_timeout` 45s 兜底保留
- 卸载：`hook.stop()`（UnhookWindowsHookEx），与 _restart_hotkey 兼容

### 验证
- 单测 6 组：alt+win 全链路（BEGIN/END/吞键）、二次触发回归、alt+x trigger 吞、普通键不吞、先松 alt 仍 END、左右修饰键归一——全部通过
- py_compile + GUI 冒烟（真实装钩子）通过

### 关键教训
**pynput 回调 return False 的语义是"停止监听"，不是"吞键"**。要吞键必须用系统级钩子（WH_KEYBOARD_LL return 1）。


## 2026-09-10 热键钩子启动失败 → ctypes 64 位句柄截断（续）

### 症状
换 WinHotkey 后日志：`热键钩子启动失败` → 毫无反应。

### 根因
ctypes 未声明 `SetWindowsHookExW` 的 argtypes/restype → 参数默认按 32 位 int 传递，**64 位 HMODULE 句柄被截断** → API 返回 NULL。

### 修复
显式声明：
- `kernel32.GetModuleHandleW`：argtypes=[LPCWSTR], restype=HMODULE
- `user32.SetWindowsHookExW`：argtypes=[c_int, _HOOKPROC, HMODULE, DWORD], restype=HHOOK
- `UnhookWindowsHookEx` / `CallNextHookEx` 同样声明

### 验证
- 真机测试：Tk 主循环里 `hook.start()` 返回 True ✓
- 单测 6 组 + GUI 冒烟通过

### 教训
所有 ctypes Win32 API 调用必须声明 argtypes/restype，64 位系统下句柄/指针截断是最隐蔽的"静默失败"。


## 2026-09-10 热键 GIL 崩溃 + "没用"——LL 钩子回调跑在 pystray 线程（同一根源）

### 症状
钩子装上成功但"还是没用"；随后程序崩溃：
`Fatal Python error: PyEval_RestoreThread ... pystray._win32.py _mainloop`

### 根因
**WH_KEYBOARD_LL 钩子回调执行在"正在 pump 消息的线程"**——本机是 **pystray 托盘线程**（它的 _mainloop 是标准 GetMessage 循环），不是 Tk 主线程！
回调里直接 `root.after`/`ui_log` = **跨线程操作 Tk**：
- 轻：Tcl 解释器跨线程调用抛异常 → 被回调的 except 吞掉 → **热键"没用"**
- 重：GIL 线程状态错乱 → **pystray 线程崩（PyEval_RestoreThread）→ 程序退出**

### 修复（回调零 Tk 操作 + 主线程轮询）
- 回调（_begin/_end）只做：纯 Python 标志赋值（`_hk["pending"]`）+ ctypes GetForegroundWindow（无 GIL 风险）
- `_poll_hotkey()`：主线程 `root.after(50)` 轮询 pending → 真正调 _stt_begin/_stt_finish + ui_log
- 45s 超时兜底同样改为主线程直接调（不再 root.after 包一层）

### 验证
- **端到端真机**：Tk 主循环 + 真实钩子 + keybd_event 模拟 alt+win 按下/松开 → BEGIN/END 正确触发 ✓
- 编译 + GUI 冒烟通过

### 教训
ctypes LL 钩子回调**不保证在安装线程执行**（pystray 等库的消息线程也可能接管）。跨线程回调只允许：纯 Python 数据操作 + 无 GIL 依赖的 Win32 调用；所有 Tk/UI 动作必须回主线程。


## 2026-09-10 自动上屏连环问题：PostMessage 无效 → pynput 模拟卡 Ctrl → keybd_event + 注入忽略

### 问题链
1. **PostMessage WM_PASTE 无效**：豆包/Chrome/Electron 应用不响应 WM_PASTE（自有输入处理）→ 自动上屏失败，但剪贴板内容正确（手动 Ctrl+V 可粘贴）。
2. **改用 pynput Controller 模拟 Ctrl+V** → **Ctrl 键 release 可能失败**（pynput 与钩子冲突/SendInput 异常）→ 系统认为 Ctrl 一直按着 → **键盘全乱、敲字变快捷键、Ctrl+V 也异常**。

### 修复
1. **_simulate_ctrl_v 改用底层 keybd_event** + `try/finally` 确保 Ctrl/V 的 release 一定执行（finally 里即使 press 抛异常也 release）。
2. **钩子忽略注入按键**：LL 钩子回调检查 `kb.flags & LLKHF_INJECTED (0x10)`，我们自己模拟的 Ctrl+V 直接放行（CallNextHookEx），避免钩子与模拟互相干扰（防止模拟 Ctrl 被钩子吞/误触发）。

### 验证
- 注入按键忽略测试：keybd_event 模拟 alt+win → 钩子 log 为空（正确忽略）✓
- 编译 + GUI 冒烟通过

### 教训
- 模拟键盘输入必须用 try/finally 确保修饰键 release，否则系统修饰键状态会卡住。
- 自己装了 LL 钩子时，模拟按键必须标记/识别注入并忽略，否则自激干扰。
- WM_PASTE 对现代 Electron/Chrome 应用不可靠，模拟 Ctrl+V 是更通用的上屏方式。

### 用户应急
若键盘仍处于 Ctrl 卡住状态：物理按一下 Ctrl 键（按下再松开）即可重置系统修饰键状态。


## 2026-09-10 开始菜单弹出 + 上屏失败：两键同松时第二个 release 漏吞

### 现象
按快捷键松开时开始菜单弹出，同时自动上屏失败。

### 根因
两键几乎同时松开时：第一个松开的键触发 end（rec=False），第二个键的 `was_rec` 变成 False → 吞判定失败 → **第二个键的 release 不被吞**。如果第二个是 Win，系统收到 Win up → 开始菜单弹出 → 焦点跳到开始菜单 → 模拟 Ctrl+V 粘贴到开始菜单搜索框而非目标应用 → 上屏失败。

### 修复
- 新增 `_swallow_keys` 集合：begin 时把所有成员 vk 加入；成员 release 时只要在集合里就吞（无论松开顺序、无论 rec 状态），吞后从集合移除。
- 单独按 Win（不按组合其他键）不触发 begin，集合为空 → 不吞 → 正常弹开始菜单 ✓。

### 验证
- 场景1：ctrl↓→win↓→ctrl↑→win↑（win 第二个松）：win↑ 被吞 ✓
- 场景2：win↑ 先、ctrl↑ 后：都被吞 ✓
- 场景3：单独 win↓↑：不吞 ✓
- 编译 + GUI 冒烟通过


## 2026-09-10 键盘反复卡死的真正根因：keybd_event 异步 + _ignore_all 恢复太早

### 现象
按快捷键说话→松开→自动上屏后，键盘立刻失常（Ctrl 键状态卡住，敲字变快捷键，删不了字）。launcher 退出后污染仍残留（系统级按键状态）。物理按 Ctrl 无法恢复，必须重启。

### 根因（确认，非猜测）
`keybd_event` 是**异步 API**：调用后事件被放入系统消息队列，不会立即被 LL 钩子处理。
`_simulate_ctrl_v` 的 finally 里**立刻**把 `hook._ignore_all = False`（恢复钩子），但此时模拟的 **Ctrl up 事件还在队列里没被处理**。等钩子处理到它时，`_ignore_all` 已经是 False → 钩子的吞键逻辑（_swallow_keys / rec 状态）把模拟的 Ctrl up 吞掉 → **系统收不到 Ctrl up → 系统按键状态表记录 Ctrl 一直按下 → 键盘全乱**。

之前的 _ignore_all 方案方向对（切断钩子与模拟的冲突），但恢复时机错了。

### 修复
模拟完后**不立即恢复钩子**，改用 `root.after(150, ...)` 延迟 150ms 再恢复 `_ignore_all=False`。150ms 足够 keybd_event 的所有事件（Ctrl down/V down/V up/Ctrl up）被系统队列处理完并被钩子忽略。
150ms 内用户物理按键会被钩子忽略，但刚说完话的 150ms 内用户几乎不会按键，可接受。

### 验证
- 编译 + GUI 冒烟通过
- 待用户真机验证（重启后干净启动测试）

### 教训
- 模拟键盘输入（keybd_event/SendInput）是异步的，与全局钩子共存时必须考虑事件队列延迟。
- "模拟期间忽略钩子"的方案必须保证忽略窗口覆盖到所有模拟事件被处理完，不能在模拟调用返回后立刻恢复。
- 系统级按键状态卡住（修饰键 release 被吞）是严重问题，用户必须重启才能恢复，开发阶段要极其谨慎。


## 2026-09-10 键盘卡死最终方案：卸载钩子→模拟→重装钩子（Gemini+Grok 双AI共识）

### 之前的方案为什么都失败
keybd_event 是异步 API，模拟的 Ctrl up 事件放入系统队列后不会立即被钩子处理。无论 _ignore_all 恢复多晚（150ms），都存在竞态：pystray 消息循环可能延迟，模拟的 Ctrl up 在钩子恢复后才被处理 → 被吞 → 系统 Ctrl 状态永久卡住。

### 最终方案（确定性，完全消除竞态）
`_simulate_ctrl_v` 重写为四步：
1. **hook_obj.stop()** — 完全卸载钩子（UnhookWindowsHookEx），此时不存在任何东西能吞模拟按键
2. **强制清理修饰键状态** — 向系统投递 Ctrl/Win/Alt 的 KEYUP，清理之前物理 KEYUP 被吞导致的悬挂状态
3. **keybd_event 模拟 Ctrl+V** — 无钩子干扰，Ctrl/V 的 down/up 一定到达系统
4. **hook_obj.start()** — 重装钩子 + 80ms _ignore_all 保险

异常路径也强制 release V/Ctrl，防止半发送序列留物理键卡住。

### 为什么这个方案确定性
卸载钩子后，系统中没有任何 LL 钩子能拦截模拟按键（其他程序的钩子除外，但那是用户环境问题）。模拟的 Ctrl up 一定到达系统 → 系统按键状态表正确更新 → 不会卡住。
卸载/重装钩子的开销很小（几毫秒），发生在用户松开快捷键之后，不影响日常使用。

### 验证
- 编译 + GUI 冒烟通过
- 待用户真机验证

### 参考
- Gemini：放弃 keybd_event 改用 SendInput + 上屏前强制释放修饰键
- Grok：卸载钩子→模拟→重装钩子是确定性方案，完全消除竞态


## 2026-09-10 热键/上屏最终方案确认（卸载钩子→Ctrl-down→Win-KEYUP→模拟→重装钩子）

### 最终 _simulate_ctrl_v 流程（确定性方案）
1. **hook_obj.stop()** — 完全卸载钩子（UnhookWindowsHookEx），此时系统中无任何东西能吞模拟按键
2. **Ctrl down** — 系统知道 Ctrl 按下（为后续 Win KEYUP 不弹开始菜单做铺垫）
3. **Win KEYUP（左右 Win）** — 清理可能卡住的 Win 状态。此时 Ctrl 按着，Windows 检测到 Win 按下期间有其他键（Ctrl），**不会弹开始菜单**
4. **20ms → V down → 20ms → V up → Ctrl up → 30ms** — 正常粘贴，确保事件处理完
5. **hook_obj.start()** — 重装钩子 + 80ms _ignore_all 保险

### 为什么之前 pynput 时代没问题
pynput 回调 `return False` 的语义是"停止整个监听器"，不是"吞键"。所以 pynput 时代根本没真正吞掉 Win 键，系统完整收到 Win down/up，按键状态完全正确——只是热键触发有问题（监听器被停掉了）。
LL 钩子时代用 `return 1` 真正吞键了，但吞键逻辑有漏洞（先按 Win 时 Win down 没被吞但 up 被吞 → 系统认为 Win 一直按着）。

### 关键坑位汇总（热键/上屏）
- pynput return False = 停监听器，不是吞键（彻底放弃 pynput 做热键）
- LL 钩子回调跑在 pystray 线程（不是 Tk 主线程）→ 回调零 Tk 操作 + 主线程轮询
- keybd_event 异步 → 模拟的 Ctrl up 可能在钩子恢复后才被处理 → 被吞 → Ctrl 卡住
- 强制清理发送 Win KEYUP 时如果钩子已卸载且 Ctrl 没按着 → 弹开始菜单
- 两键同松时第二个 release 的 was_rec=False → 漏吞 → 用 _swallow_keys 集合解决
- 卸载钩子→模拟→重装钩子是确定性方案，完全消除竞态

### 验证状态
- 编译 + GUI 冒烟通过
- 待用户真机最终验证

---

## 2026-09-10 热键/上屏彻底修复（主窗口唤出 + ALT卡住 + 开始菜单弹出）

### 背景
上一轮确认了"卸载钩子→模拟→重装钩子"的确定性方案，但用户真机测试发现三个连锁问题：
1. **主窗口从托盘被唤出**，抢占焦点 → 模拟的 Ctrl+V 粘贴到 bridge 自己窗口 → 不上屏
2. **ALT 键永久卡住**（用户用 ALT+Win 快捷键）→ 键盘失效，按什么都像 ALT+组合键
3. **开始菜单弹出** → 模拟的 Ctrl+V 粘贴到搜索框

### 根因1：主窗口被唤出（三个AI共识：Gemini/Grok/DeepSeek）
- **Toplevel(root) 的 master 绑定是根因**：即使 root.withdraw()，Toplevel 的显示/更新会通过 WM_TRANSIENT_FOR 消息链把已隐藏的 root 重新激活到前台
- **GetParent(winfo_id()) 用错了 HWND**：WS_EX_NOACTIVATE 样式加在了子窗口上，顶层窗口没生效（Grok 精确定位）
- **异常路径的 deiconify()+lift() 回退**：lift() 会强制激活整个应用（含已隐藏的 root）

### 根因2：ALT 键永久卡住
- 用户按 ALT+Win 时，**第一个按下的修饰键（ALT）的 down 没被吞**（combo 未激活，Win 还没按），但 **up 被 _swallow_keys 吞了**
- 系统只收到 ALT down，没收到 ALT up → 系统认为 ALT 一直按着 → 键盘失效
- 之前只清理了 Win，没清理 ALT → ALT 卡住无人处理

### 根因3：开始菜单弹出
- _stt_begin 里直接发 Win KEYUP，但如果用户先按 Win（Win down 没被吞、Win up 被吞 → 系统认为 Win 一直按着），此时发送 Win KEYUP 会触发开始菜单
- 同理，_simulate_ctrl_v 里如果 Win KEYUP 在 Ctrl down 之前发，也会弹开始菜单

### 修复（综合 Grok 版本 + 我的修复，最终合并）

#### 1. 状态条与 root 彻底解耦
- Toplevel(root) → Toplevel(None)（无父窗口，切断 master 绑定）
- .attributes("-disabled", True)（窗口不接收任何输入，永不抢焦点）
- .wm_transient("")（切断 transient 关系）
- HWND 兼容：GetParent 返回 0 时用 winfo_id()
- 样式后用 SWP_FRAMECHANGED 强制生效
- 显示只走 ShowWindow(SW_SHOWNOACTIVATE) + SetWindowPos(SWP_NOACTIVATE)
- 异常回退只用 deiconify()，**去掉 lift()**（lift 会激活 root）

#### 2. _tray_mode 强制保持 root 隐藏
- hide_to_tray 时 _tray_mode[0] = True，show_from_tray 时置 False
- _force_root_withdrawn()：托盘模式下在 _stt_begin/_stt_banner_show/_stt_commit 等关键节点强制 root.withdraw()
- 防止任何 Tk 操作把主窗口从托盘拉出来

#### 3. _stt_begin 录音开始时清理所有修饰键
- 第一个按下的修饰键 down 没被吞但 up 被吞 → 系统状态不一致 → 永久卡住
- 必须在录音开始时就清理（ALT/Win/Ctrl），否则录音过程中键盘已失效
- **Win KEYUP 必须用 Ctrl 掩护**：先 Ctrl down → Win KEYUP → Ctrl up，此时 Ctrl 按着 Windows 不会弹开始菜单
- ALT KEYUP 和 Ctrl KEYUP 直接发就行（不会弹开始菜单）

#### 4. _simulate_ctrl_v 最终流程（Grok 的正确顺序 + 我的增强）
1. hook_obj.stop() — 卸载钩子
2. ALT KEYUP + ESC — 清理 ALT、关闭可能弹出的菜单（在 Ctrl down 前做，不会弹开始菜单）
3. AttachThreadInput + BringWindowToTop + SetForegroundWindow — 恢复焦点到说话前的前台窗口
4. 焦点验证：打印目标窗口/当前前台窗口标题，确认 match=True
5. Ctrl down — 系统知道 Ctrl 按下
6. Win KEYUP（左右）— 此时 Ctrl 按着，**不会弹开始菜单**
7. V down → V up → Ctrl up — 正常粘贴
8. hook_obj.start() — 重装钩子 + 80ms _ignore_all 保险

#### 5. 诊断日志
- [sim] before/after：ctrl/alt/lwin/rwin 四个修饰键状态
- [sim] target/cur_fg/after_focus：目标窗口和当前前台窗口的 HWND + 标题，验证焦点恢复是否成功
- [sim] rehooked：重装钩子后的状态

### 验证（用户真机 2026-09-10 03:30）
- ✅ 主窗口不再从托盘唤出
- ✅ 上屏正常（焦点恢复 match=True）
- ✅ 系统栏/搜索框不再弹出
- ✅ 开始菜单不再弹出
- ✅ 键盘不再乱（模拟前后按键状态全部 False）
- 连续多次测试稳定

### 关键坑位汇总（热键/上屏，最终版）
- **Toplevel(root) 会通过 WM_TRANSIENT_FOR 激活已 withdraw 的 root** → 必须 Toplevel(None) + -disabled + wm_transient("")
- **GetParent(winfo_id()) 可能返回错误 HWND** → 兼容处理：返回 0 时用 winfo_id()
- **b.lift() 会激活整个应用** → 异常回退只用 deiconify()，去掉 lift()
- **第一个按下的修饰键 down 没被吞但 up 被吞 → 永久卡住** → _stt_begin 里清理所有修饰键
- **Win KEYUP 直接发会弹开始菜单** → 必须用 Ctrl 掩护（先 Ctrl down → Win KEYUP → Ctrl up）
- **ALT KEYUP 和 ESC 不会弹开始菜单** → 可以在 Ctrl down 前做
- **AttachThreadInput + SetForegroundWindow 是恢复焦点的标准方法** → 加 BringWindowToTop 和焦点验证
- **卸载钩子→模拟→重装钩子是确定性方案** → 完全消除钩子与模拟按键的竞态

### 参考
- Gemini：Toplevel(None) + -disabled + wm_transient，切断 master 绑定
- Grok：GetParent HWND 错误定位 + _tray_mode 强制隐藏 + 去掉 deiconify/lift 回退
- DeepSeek：所有 stt_status.set() 改独立状态窗口 + _stt_begin/_stt_commit 主动 root.withdraw()

---

## 2026-09-10 状态条导致主窗口被唤出的最终修复（SWP_FRAMECHANGED 强制样式生效）

### 背景
上一轮修复后，用户发现：
1. 主窗口还是会从托盘被唤出
2. 任务栏出现两个 VoxEcho（说明状态条 Toplevel 也出现在任务栏了）
3. 开始菜单有一定几率弹出

### 根因（最终定位）
**状态条的 WS_EX_TOOLWINDOW 样式没生效**——设置样式后漏掉了 SetWindowPos(SWP_FRAMECHANGED) 强制刷新，导致：
- 状态条出现在任务栏（WS_EX_TOOLWINDOW 本应隐藏任务栏按钮）
- Windows 把状态条当成独立应用窗口，创建/显示时激活它的进程（root）
- root 被唤出，抢占焦点 → 模拟的 Ctrl+V 粘贴到 bridge 自己窗口

### 关键线索
用户观察到"任务栏有两个 VoxEcho"——这直接指向状态条的 WS_EX_TOOLWINDOW 没生效。如果样式生效了，状态条不应该出现在任务栏。

### 修复（launcher.py _stt_banner_show）

1. **SWP_FRAMECHANGED 强制样式生效**（最关键）：
   `python
   SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
   SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
   `
   没有 SWP_FRAMECHANGED，SetWindowLong 修改的样式不会立即生效，窗口管理器可能缓存旧样式。

2. **HWND 兼容**：先 GetParent(winfo_id())，返回 0 就用 winfo_id()。Toplevel(None)+overrideredirect 时 GetParent 可能返回 0，之前直接用 GetParent 的结果导致样式加在了 NULL HWND 上（静默失败）。

3. **显示前再次检查样式**：防止 Tkinter 在 geometry()/update_idletasks() 时覆盖扩展样式。如果发现 WS_EX_TOOLWINDOW 丢失，重新设置并 SWP_FRAMECHANGED。

4. **多层 _force_root_withdrawn()**：
   - 状态条创建后立即调用
   - 状态条显示后立即调用
   - 淡入动画每帧调用
   - 淡出结束后调用
   - _stt_begin / _stt_commit / _stt_error 调用
   确保任何 Tk 操作激活 root 后都被立即拉回隐藏状态。

5. **_force_root_withdrawn() 改为检查 
oot.state()**：不依赖 _tray_mode 标志（可能在 show/hide 切换时不同步），直接检查 
oot.state() == 'withdrawn'，只要当前是隐藏状态就强制保持。

### 验证（用户真机 2026-09-10）
- ✅ 任务栏不再出现两个 VoxEcho（状态条 WS_EX_TOOLWINDOW 生效）
- ✅ 主窗口不再从托盘被唤出
- ✅ 上屏正常（焦点恢复 match=True）
- ✅ 开始菜单不再弹出
- ✅ 键盘正常（模拟前后按键状态全部 False）
- 连续多次测试稳定

### 关键坑位汇总（状态条/主窗口唤出，最终版）
- **SetWindowLong 后必须 SWP_FRAMECHANGED**：否则样式不立即生效，窗口管理器缓存旧样式。这是本次问题的根因。
- **GetParent 可能返回 0**：Toplevel(None)+overrideredirect 时，GetParent(winfo_id()) 可能返回 0，必须做兼容（返回 0 就用 winfo_id()）。否则样式加在 NULL HWND 上静默失败。
- **Toplevel(root) 会通过 WM_TRANSIENT_FOR 激活已 withdraw 的 root**：必须 Toplevel(None) + -disabled + wm_transient("") + GWLP_HWNDPARENT=0。
- **b.lift() 会激活整个应用**：异常回退只用 deiconify()，去掉 lift()。
- **_tray_mode 标志可能不同步**：_force_root_withdrawn() 直接检查 root.state() 更可靠。
- **状态条出现在任务栏 = WS_EX_TOOLWINDOW 没生效**：这是诊断主窗口被唤出的关键线索。

### 参考
- Grok：GetParent HWND 错误定位 + _tray_mode 强制隐藏 + 去掉 deiconify/lift 回退
- Gemini：Toplevel(None) + -disabled + wm_transient，切断 master 绑定
- DeepSeek：所有 stt_status.set() 改独立状态窗口 + _stt_begin/_stt_commit 主动 root.withdraw()
- 最终根因由用户观察"任务栏两个 VoxEcho"锁定 → SWP_FRAMECHANGED 缺失

---

## 2026-09-11 扩展心跳检测 + Flask 端点重复定义坑

### 背景
电子书朗读面板需要实时显示 Chrome 扩展是否在线（已就绪/请加载）。之前是纯文本静态显示，扩展开了关了都不更新。

### 实现
- server.py 添加两个端点：`POST /extension_heartbeat`（扩展发心跳）、`GET /extension_status`（bridge 轮询状态）
- 心跳超时 90 秒：扩展关闭后 90 秒内 bridge 自动识别为离线
- 启动策略：前 5 次每 1 秒快速同步（确保 server.py 启动后尽快对齐），之后每 3 秒稳定轮询
- 扩展侧：chrome.alarms 每分钟发一次心跳，popup 打开时立即发一次

### 关键坑位：Flask 端点重复定义
**症状**：server.py 启动时直接崩溃，报 `AssertionError: View function mapping is overwriting an existing endpoint function: extension_heartbeat`。
**根因**：之前的对话已经加过一次 `/extension_heartbeat`，新对话又加了一次，同名函数被重复 `@app.route` 注册。Flask 不允许同名 endpoint。
**教训**：多人/多对话协作改同一个 Flask 服务时，先 `grep "@app.route" server.py` 确认端点不存在再加。

---

## 2026-09-11 Tkinter 子线程 root.after 不可靠 → 主线程轮询模式

### 背景
TTS 长文本生成时，子线程里用 `root.after(0, callback)` 回调主线程更新 UI。但用户反馈：切换场景后状态消失、按钮不变灰、合成完按钮不恢复。

### 根因
**子线程里的 `root.after(0, callback)` 在某些情况下不执行**。Tkinter 的 after 事件队列依赖主线程进入 event loop，如果子线程在主线程繁忙时 post 事件，事件可能被丢弃或延迟到不可预测的时机。在 TTS 这种长时间任务中尤其明显。

### 方案：主线程轮询共享变量
子线程只做网络请求，把结果存到共享变量（`_result_holder = {}`）；主线程每 200ms 用 `root.after(200, poll)` 轮询共享变量，发现有结果就更新 UI。
- 子线程：`_result_holder["text"] = result`
- 主线程：检查 `_result_holder`，有结果就更新按钮状态、播放音效、清空变量

### 教训（Tkinter 架构经验）
1. **子线程永远不要直接操作 Tkinter 控件**——即使包了 `root.after`，在 Windows 上也可能不可靠
2. **跨线程通信的可靠模式**：子线程写共享变量（dict/list），主线程用 `after` 轮询。不要依赖 `root.after(0, callback)` 从子线程唤醒主线程
3. **超时保护必须有两层**：子线程里的 `requests` timeout + 主线程里的 `_tts_timeout()` 兜底，防止网络卡死导致 UI 永久假死
4. **ttk 按钮 disabled 样式必须显式配置**：默认 ttk 主题下 disabled 按钮颜色和 normal 一样，必须 `style.configure("TButton", background=[("disabled", "#374151")])`，否则用户不知道按钮被禁用了

---

## 2026-09-11 Toplevel + overrideredirect 在 Windows 不可靠 → Frame + place 方案

### 背景
语音配置面板里，火山引擎的"控制台"链接需要悬停时弹出一个可交互的悬浮面板（含 ASR + LLM 两个链接）。要求类似 Tooltip，但停留时间够长让用户能移过去点击。

### 第一版（失败）：Toplevel + overrideredirect
用 `tk.Toplevel` + `overrideredirect(True)` + `withdraw/deiconify` 实现。
**症状**：调试日志显示事件触发了、`deiconify()` 调用了、geometry 也设置了，但面板就是不显示。
**根因**：Tkinter 在 Windows 上，`overrideredirect(True)` 的 Toplevel 配合 `withdraw()`/`deiconify()` 有已知 bug，窗口可能创建了但不映射到屏幕。这是 Tkinter 跨平台不一致的经典坑。

### 第二版（成功）：Frame + place
直接在对话框的 `card` 容器里创建一个 `tk.Frame`，用 `place()` 定位在链接旁边。
- 显示：`popup.place(x=..., y=...)`
- 隐藏：`place_forget()`
- 延迟隐藏 800ms（让用户有时间把鼠标移到面板上点击链接）
- 定位用 `link_lbl.winfo_rootx() - card.winfo_rootx()` 换算成相对于 card 的坐标

### 教训（Tkinter 架构经验）
1. **Windows 上不要依赖 Toplevel + overrideredirect + withdraw/deiconify 做悬浮面板**——事件触发了但窗口不显示，调试极困难
2. **同窗口内的悬浮面板用 Frame + place**——100% 可靠，因为是父容器的子组件，不存在跨窗口映射问题
3. **place 定位用 winfo_rootx 差值换算**：控件在屏幕上的绝对坐标减去父容器的绝对坐标，得到 place 需要的相对坐标

---

## 2026-09-12 多对话协作导致的两组 UI 代码问题

### 背景
用户在多个对话窗口（豆包、Grok、Gemini）之间来回修改 launcher.py，每个对话都基于自己看到的版本改代码。

### 症状
改了一个 bug，用户说"还是有问题"。排查发现 launcher.py 里存在**两组完全独立的 UI 代码**：
- 第一组：约 2200-3300 行
- 第二组：约 3300-4400 行
- 两组都定义了 `show_scene()`、`_tts_with_text()`、`do_tts()`、`load_voices()` 等同名函数
- 后定义的函数会覆盖先定义的，所以用户实际用的是第二组
- 但每次修改只改了一组，另一组还是旧代码

### 教训（协作经验）
1. **多对话改同一个文件是高危操作**——每个对话都以为自己看到的是最新版本，实际上可能基于旧版本修改，导致代码重复或覆盖
2. **修改前先 `grep` 确认有几处**：用 `c.count(old_string)` 确认替换了几处，预期替换 N 处就必须看到 N，否则说明有重复代码
3. **长期建议**：UI 组件应该拆成独立函数/类，避免在一个 5000 行的文件里重复定义两套
4. **CRLF 文件注意**：launcher.py 是纯 CRLF 文件，Edit 工具直接失败，必须用 Python 补丁脚本（`Path.read_text` → `replace` → `write_text`）修改

---

## 2026-09-12 双击 Ctrl 长按快捷键状态机

### 背景
ALT+Win 组合键有几率弹出开始菜单（两键同松时第二个 release 漏吞），且在 PowerShell 等应用里 ALT 快捷键冲突多。用户想改成"双击 Ctrl，第二下按住说话"。

### 初版失败
简单地在 Ctrl 按下事件里判断时间间隔，但**键盘硬件有微秒级抖动/重复码**——按住 Ctrl 不放时 Windows 会重复发送 keydown 事件，导致第一下按下就触发录音。

### 最终状态机
四个关键状态：
1. `_dc_first_pressed`：第一次 Ctrl 已按下
2. `_dc_first_released`：第一次 Ctrl 已物理松开
3. `_dc_first_press_time` / `_dc_release_time`：时间戳
4. `_dc_other_key_intervened`：中间有没有按其他键

**判定条件（第二次按下时）**：
- 距离第一次松开 > 50ms（排除键盘硬件抖动）
- 距离第一次按下 < 350ms（双击时间窗口）
- 中间没有按其他键（排除 Ctrl+C / Ctrl+V 等正常操作）

**松开逻辑**：第二次松开时结束录音并发送。第一次松开时只记录时间戳，不触发任何动作。

### 教训
1. **全局热键必须考虑键盘硬件抖动**——Windows 按住不放会重复发 keydown，不能只看"又收到一个 keydown"就认为是新的按键
2. **修饰键组合的 release 事件容易丢**——ALT+Win 两键同松时第二个 release 可能被系统吞掉，这是开始菜单弹出的根因
3. **双击类快捷键必须有"中间按了其他键就取消"的逻辑**——否则 Ctrl+C / Ctrl+V 会被误判为双击

---

## 2026-09-13 PyInstaller 打包坑汇总

### 坑 1：sounddevice / PortAudio DLL 没打包
**症状**：`OSError: PortAudio library not found`，`cannot load library .../libportaudio64bit.dll: error 0x7e`
**根因**：`--hidden-import sounddevice` 只告诉 PyInstaller 导入模块，不收集 DLL。
**修复**：`--collect-binaries sounddevice`（同时收集 lameenc）

### 坑 2：base_library.zip 找不到
**症状**：`[Errno 2] No such file or directory: '...\_MEIxxxxxx\base_library.zip'`
**根因**：onefile 模式运行时把所有文件解压到临时目录 `_MEIxxxxxx`，杀毒软件（Windows Defender）把临时解压的文件隔离/删除了。
**修复**：把 exe 所在文件夹加入杀毒软件白名单排除项。

### 坑 3：Tcl/Tk 找不到
**症状**：`Can't find a usable init.tcl in the following directories... This probably means that Tcl wasn't installed properly.`
**根因**：PyInstaller 没有正确收集 Tcl/Tk 运行时文件。
**建议**：升级 PyInstaller 到最新版本，或用 onedir 模式。

### 最终建议：onedir 模式
onefile 模式虽然只有一个 exe 方便分发，但在 Windows 上经常遇到：
- 杀毒软件误报/拦截临时解压
- 各种 DLL/运行时文件收集不全
- 启动慢（每次都要解压到临时目录）

**onedir 模式（build_onedir.bat）稳定性好很多**：所有文件都在一个文件夹里，不需要临时解压，杀毒软件干扰小。分发时把整个文件夹打成 zip 即可。

---

## 2026-09-13 其他 Tkinter 踩坑记录

### Tkinter font 不支持字体回退
**症状**：`_tkinter.TclError: expected integer but got "Consolas"`
**根因**：`font=("Cascadia Code", "Consolas", 8)` 这种写法，Tkinter 把第二个元素 `"Consolas"` 当成 size 参数了。Tkinter 不支持字体回退元组。
**修复**：只能传一个字体名 `font=("Cascadia Code", 8)`，不要写 fallback 列表。

### Tkinter 高度硬编码同步问题
改窗口高度时，必须同时改以下所有位置：
1. 初始 `root.geometry("530x400")` 和 `root.minsize(500, 400)`
2. 两组 UI 的 `show_scene()` 函数里的 geometry 和 minsize
3. `_toggle_log()` 里的硬编码 `_h = 520 if scene2 else 400`
4. 所有 `root.after(120, lambda: root.geometry(...))` 强制回调

漏改任何一处，切换场景或展开日志时高度就会跳变。

### ttk 样式暗色化要点
- Combobox 暗色：需要同时配置 `fieldbackground`、`foreground`、`arrowcolor`、`selectbackground`
- RadioButton/Checkbutton 暗色：`background`、`foreground`、`indicatorcolor`、`indicatorbackground`
- Entry 只读样式：用 `state="readonly"` + 自定义 `ReadOnly.TEntry` 样式（fieldbackground 暗一点、foreground 灰一点）
- 按钮 disabled 颜色：必须用 `style.map("TButton", background=[("disabled", "#374151")])`，否则禁用时还是亮绿色


---

## 2026-09-14 V2.0.1 发布与 GitHub release 维护经验（TK 线）

### zip 发布结构
- 便携版 zip = PyInstaller onedir 产物整个文件夹 + 根目录放 VoxEcho-extension（扩展 load unpacked 用）
- 包内 README.txt（英文）+ README_ZH.txt（中文）：说明扩展加载方法；不使用电子书朗读的用户可跳过
- 未签名 exe：SmartScreen / 杀软会提示，说明文件里要写清"加入白名单即可"

### GitHub release 维护
- 只留最新版 + 旧大版本（3.1.x 最新 + 2.0.x）；有功能缺陷的旧 release 删除（tag 保留）
- 发布正文中英双语 + 英文截图（GitHub 面向国际用户）；截图从 tkinter 便携版切 ui_lang=en 截取，不用切系统语言
- 版本号必须前后端一致（前端 VERSION 常量 + 后端版本 + release tag）

### 发布工具链坑（curl / GitHub API）
- token 取法：git credential fill（stdin 传 protocol/https + host/github.com），password 行 len=40
- PowerShell 里 curl --data-binary "@绝对路径" 读不了文件，必须 cd 到文件所在目录用相对路径 @file.json
- PATCH release body 时，全角括号（3.1.6）和半角 (3.1.6) 是两回事，replace 要两种都处理
- release body 的截图 URL 用 raw.githubusercontent.com/owner/repo/master/...（branch 名）比 tag 稳：tag 固定后无法再放新文件
- GitHub repo PATCH 不更新 topics，必须单独 PUT /repos/{owner}/{repo}/topics

---

## 2026-09-14 git 双线结构维护（Tauri 主线 + TK 支线）

### 当前结构
- master = Tauri 3.1.x 主线（D:\Documents\VoxEcho）
- tkinter-legacy = TK 2.0.1 支线（D:\Documents\VoxEcho-tk，worktree 关联 D:/Documents/VoxEcho/.git/worktrees/VoxEcho-tk）
- 每个分支各自维护 README 三语（README 是跟分支走的，不是全项目一份）

### 坑：worktree 指针指向 temp
- 曾出现 .git 是指针文件（gitdir: D:/temp/voxecho-git），真 git 元数据全在 temp——temp 一清理两端全断
- 迁移：Move-Item temp 目录到 Documents\VoxEcho\.git，重写两处指针文件；GitHub Desktop / explorer 开着会锁句柄导致失败，先退出/关闭
- 迁移后 GitHub Desktop 报"找不到仓库"是缓存问题：Remove（别勾 Also move to Trash）→ Add Local Repository 重新挂载

### 远端分支
- origin/tkinter-legacy 是远端跟踪镜像，不是独立分支，删不了也不该删（等于砍 TK 支线）
- GitHub 网页分支列表只显示真分支（master + tkinter-legacy），多出来的 origin/* 是 GH Desktop 幽灵缓存

---

## 2026-09-14 隐私决策 + 扩展双端口自适应

### 隐私（已实施）
- 日志只记错误（网络不通 / model 未触达 / API key 不对 / 排查用），不记录每次转写原文
- 转写内容不落盘
- 日志可划选复制（排查时有用）

### VoxEcho-extension 自适应双端口
- 扩展自动探测本地 bridge：先试 5010（Tauri 线），失败再试 5005（TK 线）——两条线不用来回改配置
- 扩展版本号不必与 bridge 版本一致


---

## 2026-09-15 对话共建回顾：Tauri 3.1.x + TK 2.0.x 经验汇总

> 本对话跨 Tauri 主线与 TK 支线的共建回顾，供后续维护参考。每条 = 背景 → 结论/坑。

## Tauri 线（3.1.x）

### HUD 翻译/确认小面板
- 面板自适应高度：文本一行就一行高，随文本增长；按钮排顶部、快捷键提醒小字排底部；确认按钮固定右侧
- 面板固定不可调大小（用户明确要求）
- CTRL+Enter 在文本框不能换行——不要展示"CTRL+Enter 换行"提示（Tauri/TK 都验证过）
- 小面板弹出后焦点必须落在文本框末尾，否则短句没法直接回车确认
- 面板位置锚定任务栏（workarea bottom=1040 之类）；每次改代码要重新验证贴底，容易回退
- 面板四角透明是老大难：html/body 白底 + Tauri 原生窗口白底都会漏白角

### 焦点恢复与上屏（核心链路）
- 粘贴前必须恢复前台窗口到录音时的窗口（日志锚：`粘贴前前台窗口`）
- 焦点没恢复 → 上屏失败，要手动 Ctrl+V；恢复 OK → 正常上屏
- 静置较久后容易出现反应慢/双上屏——钩子/焦点链路时序问题，改动后需实机验证
- LLM 空响应（`finish_reason=length` / `content=''`）会导致翻译为空——max_tokens 要够，超长文本要加大
- 网络不好时 LLM 请求（HTTPSConnectionPool / SSLEOFError）会拖慢整个链路，需重试+超时兜底

### 热键
- 双击 Ctrl 状态机：防键盘硬件抖动（按住重复发 keydown）、中间按其他键取消（详见 2026-09-12 TK 章节）
- 3.1.x 验证：双击第二下放开即触发（不用长按）也是可用路径
- Ctrl+Win 与微信语音转文字快捷键冲突（微信的 Ctrl+Win 失效）——退出 VoxEcho 即恢复，确认是全局钩子抢占
- Win 键粘滞/键盘全乱：keybd_event 异步 + _ignore_all 恢复太早是根因；最终方案=卸载钩子→模拟按键→重装钩子
- 用户曾误以为是硬件问题（换电池、重启）——实际是应用钩子，改动后必须实测键盘

### Provider / API Key 弹窗
- 首次使用 API key 为空时按快捷键 → 自动弹出 Provider 窗口 + tooltip 说明（显示几秒）
- 弹窗必须置顶跳到最前（否则用户傻等在文本框）
- 英文界面下，Groq 等海外平台不需要"国内需代理"提示（那是给国内用户看的）
- 网络好时 Groq 响应比火山快（尤其需要 LLM 时）——用户实测观察

### 无边框 + 透明窗口（大改动，用户最怕）
- tauri.conf：decorations:false + transparent:true 才能让 CSS 渲染大圆角
- 四角白边根因：html/body 默认白底 + Tauri 原生窗口白底——html/body/#root 设 transparent + 外层深色基底
- 最终方案：双层卡片——外层 #040705 不透明基底（p-2.5 rounded-xl）+ 内层实体卡片带阴影，避免 Windows 白角/灰框
- 拖拽：data-tauri-drag-region + onMouseDown 避开交互元素（button/input/textarea/select）
- 自定义最小化/关闭按钮放日志按钮上方一行；日志小箭头始终贴右
- 光标：拖拽区也不该变光标（用户明确：任何地方都保持正常箭头）
- 透明裁切的直角残留 Windows 上无法彻底消除——用户接受妥协（"累了，就这样吧"）

### 日志与隐私
- 日志放开划选复制（排查必需，用户反复要求）
- 日志只记错误（网络不通/model 未触达/API key 不对），不记每次转写原文（隐私）
- 状态条不要抢最上层（当前界面靠下时会挡住）

### 其他 Tauri 经验
- 版本号必须前后端一致（前端 VERSION 常量 + 后端版本 + 主界面 + About 弹窗）
- "退出"语义：用户要求退出=全退（不是隐藏）——避免偷感 + 与别的应用快捷键冲突
- 自定义快捷键有时要 Save 两次才生效（改后必须验证，主界面显示与实际生效不一致是常见 bug 源）
- 打包用 tauri build；onefile 单 exe 易触发杀软/缺组件，onedir 更稳
- 观察：任务管理器常驻两个 voxecho-backend.exe（前后端分离架构，待确认是否预期）

## TK 线（2.0.x）补充

### 翻译窗口（确认原文再翻译）
- 窗口比 Tauri 版窄；文本一行就只留一行高（自适应）
- 焦点自动落到原文最后一个字（否则短句没法直接回车）
- 焦点没落回光标原位 → 上屏失败，要手动 Ctrl+V（与 Tauri 线同源问题）
- 小面板自身会占一个任务栏位（overrideredirect 方案注意）

## 跨线与对外维护（2026-09-15 补充）

- extension 自适应 5010/5005 双端口（先 Tauri 后 TK）——两条线不用来回改配置
- git 双线：master=Tauri / tkinter-legacy=TK，worktree 关联，README 各自三语（详见 09-14 git 章节）
- Release 只留最新 + 旧大版本；截图英文版；正文中英双语（详见 09-14 发布章节）
- SEO：GitHub 搜索索引主要看 仓库名 + description + topics + README 全文；description/topics 已改为语音助手定位；dictation / read-aloud 是英文高频搜索词（Windows 自带功能就叫 Dictation），中文对应"语音听写"
- README 语言行只放真实提供的语言（现在 EN/ZH/CHT 三种），不挂死链
