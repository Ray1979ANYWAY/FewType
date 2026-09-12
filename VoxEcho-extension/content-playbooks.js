// 这个脚本会被注入到 play.google.com 顶层页面，以及 books.googleusercontent.com 的正文 iframe 里。
// 顶层页面没有 .scribe_body / .scribe_callout-body，所以会静默跳过，不会误报。

(function () {
  // 诊断日志：这个脚本运行在书页的 iframe/顶层页面里，console 单独开一个 devtools 才看得到，
  // 跟 background/offscreen 的日志分散在三个地方，排查时间线很麻烦。统一转发给 background.js
  // 汇总记录，用 popup 的"导出诊断日志"按钮一次性拿到完整时间线。
  // 扩展被重新加载/更新后，页面里原来注入的这份 content script 还在运行，
  // 但它的"扩展上下文"已经失效了——这时候任何 chrome.runtime.sendMessage 调用
  // 都会抛出 "Extension context invalidated"。这是预期内的正常现象（开发时重新
  // 加载扩展、旧页面还没刷新就会出现），不代表提取或朗读逻辑有问题，静默吞掉即可，
  // 不需要在控制台/错误面板里报出来吓用户一跳。
  function safeSendMessage(msg) {
    try {
      chrome.runtime.sendMessage(msg);
    } catch (e) {
      // 忽略：扩展上下文失效，等页面刷新后这个实例自然会被新的 content script 取代
    }
  }

  function diagLog(message, data) {
    safeSendMessage({ type: "DIAG_LOG", source: "content", message, data });
  }

  // 不同书用的渲染引擎不一样（观察到至少两套：一套段落带 "scribe_body" 之类的 class，
  // 一套段落只有 "ocean-sliced-element" 属性、没有语义 class，标题用原生 <h1>~<h6>）。
  // 靠记 class 名字没法跨书复用，改成认标签本身：正文终归是 <p>，标题终归是 <h1>~<h6> 或整段加粗。
  const SELECTOR = "p, h1, h2, h3, h4, h5, h6";

  // 中文/英文的句子和段落，几乎必然以这类"收尾符号"结束。如果一段提取出来的文字结尾不是
  // 这些符号，大概率是被硬切了（mergeSplitFragments 会把这类碎片合并），
  // 或者是口号式短段落（pushParagraph 里识别为 heading）。
  const TERMINAL_PUNCTUATION = /[。！？…—」』）)\]"'".!?;:''"]$/;

  function pushParagraph(result, seen, el) {
    if (seen.has(el)) return;
    seen.add(el);

    const text = el.textContent.replace(/\s+/g, " ").trim();
    if (!text) return;
    // 按内容过滤"分页延续箭头"，不依赖具体 class 名字（scribe_custom-N 这种编号是每本书动态分配的，
    // 换书之后同一个编号可能对应完全不同的样式，写死排除某个 class 会有把正文误删的风险）。
    if (/^[↓↑→←]+$/.test(text)) return;

    const tag = el.tagName.toLowerCase();

    if (/^h[1-6]$/.test(tag)) {
      // 原生语义化标题标签（比如 <h3>第1节</h3>）
      result.push({ type: "heading", level: Number(tag[1]), text });
      return;
    }

    // 走到这里说明是 <p>。判断是否为"整段被加粗包裹"的小标题写法（比如 scribe_bold 那种）
    const onlyChild = el.children.length === 1 ? el.children[0] : null;
    const boldClassNames = ["scribe_bold"]; // 目前只见过这一种，遇到新书再补充
    const isBoldWrapped =
      onlyChild &&
      boldClassNames.some((c) => onlyChild.classList.contains(c)) &&
      onlyChild.textContent.replace(/\s+/g, " ").trim() === text;

    const isCallout = el.classList.contains("scribe_callout-body");

    // 口号式短段落识别：不以任何标点结尾、且内容较短（≤20字），单独成块，
    // 不和前后正文合并。典型场景："战争即和平 / 自由即奴役 / 无知即力量。"那种
    // 每行一个短句但结尾没有标点的展示段落。
    const SHORT_LINE_MAX = 20;
    const isShortNopunct =
      !isBoldWrapped &&
      !isCallout &&
      !TERMINAL_PUNCTUATION.test(text) &&
      Array.from(text).length <= SHORT_LINE_MAX;

    result.push({
      type: isBoldWrapped || isShortNopunct ? "heading" : isCallout ? "callout" : "body",
      text,
    });
  }

  function mergeSplitFragments(result) {
    const merged = [];
    for (const cur of result) {
      const prev = merged[merged.length - 1];
      const canMergeWithPrev =
        prev &&
        prev.type === cur.type &&
        prev.type !== "heading" && // 标题不参与合并，本来就该是独立的一小段
        prev.text.length > 0 &&
        !TERMINAL_PUNCTUATION.test(prev.text);

      if (canMergeWithPrev) {
        prev.text += cur.text; // 直接拼接，中间不加任何字符——很可能是断在词语中间，加了反而错
      } else {
        merged.push({ ...cur });
      }
    }
    return merged;
  }

  // 找出当前"真正可见、激活状态"的页面容器——这份逻辑同时给正文提取
  // (extractCurrentPageText) 和高亮定位 (findRangesForText) 用，保证两者看到的
  // DOM 范围完全一致：提取到什么内容，就应该能在同样的范围里找到并高亮它。
  // Play Books 会把相邻页预加载进 DOM（用于翻页动画/顺滑滚动），只有带 "shown"
  // class 的 reader-page 才是当前真正显示的那一份；同一个可见页容器内，翻页动画
  // 期间可能同时存在正在淡出的旧版本，用 computed opacity 过滤，只保留真正
  // 可见（opacity > 0.5）的那一份。
  function getActiveRenderedPageScopes() {
    const shownPages = document.querySelectorAll("reader-page.shown");
    const scopeNodes = shownPages.length > 0 ? Array.from(shownPages) : [document];

    const activeScopes = [];
    scopeNodes.forEach((scope) => {
      const renderedPages = scope.querySelectorAll("reader-rendered-page");
      let activeRenderedPages = Array.from(renderedPages).filter((rp) => {
        const opacity = parseFloat(getComputedStyle(rp).opacity || "1");
        return opacity > 0.5;
      });
      if (activeRenderedPages.length === 0) activeRenderedPages = [scope]; // 兜底
      activeScopes.push(...activeRenderedPages);
    });
    return activeScopes;
  }

  function extractCurrentPageText() {
    const activeRenderedPages = getActiveRenderedPageScopes();
    const seen = new Set();
    const result = [];
    activeRenderedPages.forEach((rp) => {
      rp.querySelectorAll(SELECTOR).forEach((el) => pushParagraph(result, seen, el));
    });
    return result.length > 0 ? mergeSplitFragments(result) : null;
  }

  // ---- 朗读起点：与 Koodo 一致，从视口内第一个任意标点之后开始 ----
  // 任意标点：句号/问号/叹号、逗号、顿号、分号、冒号、省略号、破折号、右括号/右引号
  const MAJOR_PUNCT_RE = /[。！？，、；：…—～,.!?;:…—\-）\)\]\}】」』》〉"'"]/;
  const START_STEP = 8;

  function extractWithElements() {
    const activeRenderedPages = getActiveRenderedPageScopes();
    const seen = new Set();
    const data = [];
    const elements = [];
    activeRenderedPages.forEach((rp) => {
      rp.querySelectorAll(SELECTOR).forEach((el) => {
        if (seen.has(el)) return;
        const before = data.length;
        pushParagraph(data, seen, el);
        if (data.length > before) elements.push(el);
      });
    });
    // 起点定位必须跟元素一一对应，不能用 merge 后的结果（合并会打乱下标）
    return data.length > 0 ? { data, elements } : null;
  }

  function findSentenceStartInViewport(el) {
    const view = el.ownerDocument && el.ownerDocument.defaultView;
    if (!view) return null;

    const rawText = el.textContent;
    const len = rawText.length;
    if (len === 0) return null;

    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    const textNodes = [];
    let combined = 0;
    let node;
    while ((node = walker.nextNode())) {
      textNodes.push({ node, start: combined, length: node.textContent.length });
      combined += node.textContent.length;
    }
    if (textNodes.length === 0) return null;

    function rectAt(charIndex) {
      for (const tn of textNodes) {
        if (charIndex >= tn.start && charIndex < tn.start + tn.length) {
          const range = document.createRange();
          const offset = charIndex - tn.start;
          range.setStart(tn.node, offset);
          range.setEnd(tn.node, offset + 1);
          const rects = range.getClientRects();
          return rects.length > 0 ? rects[0] : null;
        }
      }
      return null;
    }

    function inViewport(rect) {
      return (
        rect &&
        rect.left >= 0 &&
        rect.left < view.innerWidth &&
        rect.top >= 0 &&
        rect.top < view.innerHeight
      );
    }

    let coarseHit = -1;
    for (let i = 0; i < len; i += START_STEP) {
      if (inViewport(rectAt(i))) {
        coarseHit = i;
        break;
      }
    }
    if (coarseHit === -1) return null;

    let fineHit = coarseHit;
    for (let i = Math.max(0, coarseHit - START_STEP); i < coarseHit; i++) {
      if (inViewport(rectAt(i))) {
        fineHit = i;
        break;
      }
    }

    let rawOffset = null;
    for (let i = fineHit; i < len; i++) {
      const rect = rectAt(i);
      if (!inViewport(rect)) break;
      if (MAJOR_PUNCT_RE.test(rawText[i])) {
        rawOffset = i + 1;
        break;
      }
    }
    if (rawOffset === null) return null;

    const rawPrefix = rawText.slice(0, rawOffset);
    const normalizedPrefix = rawPrefix.replace(/\s+/g, " ").trimStart();
    return normalizedPrefix.length;
  }

  // 把起点之前的未合并段落拼成 skipPrefix（与 mergeSplitFragments 规则一致），
  // background 用这段前缀从缓存正文里裁掉页首半句，避免 merge 前后下标错位。
  function buildSkipPrefix(data, segmentIndex, charOffset) {
    const parts = [];
    for (let i = 0; i < segmentIndex && i < data.length; i++) {
      parts.push({ ...data[i] });
    }
    if (segmentIndex < data.length && charOffset > 0) {
      const cur = data[segmentIndex];
      parts.push({ ...cur, text: cur.text.slice(0, charOffset) });
    }
    if (parts.length === 0) return "";
    const merged = mergeSplitFragments(parts);
    return merged.map((m) => m.text).join("");
  }

  function findVisibleStartIndex() {
    const extracted = extractWithElements();
    if (!extracted) return { skipPrefix: "" };

    const view = document.defaultView;
    if (!view) return { skipPrefix: "" };

    const viewportWidth = view.innerWidth;
    const candidates = [];
    let bestFallbackIndex = 0;
    let bestFallbackOverlap = -1;

    extracted.elements.forEach((el, i) => {
      const rect = el.getBoundingClientRect();
      const overlap = Math.max(0, Math.min(rect.right, viewportWidth) - Math.max(rect.left, 0));
      const vOverlap = Math.max(0, Math.min(rect.bottom, view.innerHeight) - Math.max(rect.top, 0));
      if (overlap > 0 && vOverlap > 0) candidates.push(i);
      const score = overlap * vOverlap;
      if (score > bestFallbackOverlap) {
        bestFallbackOverlap = score;
        bestFallbackIndex = i;
      }
    });

    candidates.sort((a, b) => a - b);

    for (const idx of candidates) {
      if (extracted.data[idx].type === "heading") {
        const skipPrefix = buildSkipPrefix(extracted.data, idx, 0);
        diagLog(`起点定位 heading segmentIndex=${idx}`, { skipLen: skipPrefix.length });
        return { skipPrefix };
      }
      const found = findSentenceStartInViewport(extracted.elements[idx]);
      if (found !== null) {
        const skipPrefix = buildSkipPrefix(extracted.data, idx, found);
        diagLog(`起点定位 segmentIndex=${idx}, charOffset=${found}`, {
          skipLen: skipPrefix.length,
          preview: extracted.data[idx].text.slice(found, found + 20),
        });
        return { skipPrefix };
      }
    }

    const skipPrefix = buildSkipPrefix(extracted.data, bestFallbackIndex, 0);
    diagLog(`起点定位兜底 segmentIndex=${bestFallbackIndex}`, { skipLen: skipPrefix.length });
    return { skipPrefix };
  }

  // 用户用鼠标选中了一段文字时，从这段文字的第一个字开始朗读。
  // 返回 { skipPrefix }（与 findVisibleStartIndex 同一契约）：起点之前的全部正文文字。
  // 没有有效选中（无选区 / 空选区 / 选中起点不在本帧正文里）返回 null，由调用方
  // 回退到视口定位。这里读的是本帧自己的 window.getSelection()——本书正文所在的那个
  // iframe 才是真正提取文字、回应起点查询的帧，选中文字也只会发生在那个帧里。
  function findSelectionStartIndex() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const selText = sel.toString().replace(/\s+/g, " ").trim();
    if (!selText) return null;

    const extracted = extractWithElements();
    if (!extracted) return null;

    // 用 range 的 start 而不是 anchor/focus：无论用户是正选还是反选，
    // start 始终是选中文字的真正起点（第一个字）。
    const range = sel.getRangeAt(0);
    const startNode = range.startContainer;
    const startOffset = range.startOffset;

    // 从选中起点向上找它所在的正文元素（跟提取用同一套 SELECTOR 对齐）
    let targetEl = startNode.nodeType === Node.ELEMENT_NODE ? startNode : startNode.parentElement;
    while (targetEl && !targetEl.matches(SELECTOR)) {
      targetEl = targetEl.parentElement;
    }
    if (!targetEl) return null;

    const idx = extracted.elements.indexOf(targetEl);
    if (idx === -1) return null; // 起点不在当前这一页提取到的段落里，交给视口兜底

    // 把"元素开头到选中起点"这段原始文本用 Range 量出来，得到原始字符偏移。
    // 提取阶段做过规范化（连续空白压缩成空格、首尾去空白），原始偏移要换算成
    // 规范化文本里的偏移，才能正确地在 extracted.data[idx].text 上做 slice。
    let rawOffset = null;
    try {
      const measure = document.createRange();
      measure.selectNodeContents(targetEl);
      measure.setEnd(startNode, startOffset);
      rawOffset = measure.toString().length;
    } catch (e) {
      rawOffset = null;
    }
    if (rawOffset === null) return null;

    const rawPrefix = targetEl.textContent.slice(0, rawOffset);
    const normalizedOffset = rawPrefix.replace(/\s+/g, " ").trimStart().length;
    const skipPrefix = buildSkipPrefix(extracted.data, idx, normalizedOffset);
    diagLog(`选中文本起点定位 segmentIndex=${idx}, charOffset=${normalizedOffset}`, {
      selLen: selText.length,
      preview: extracted.data[idx].text.slice(normalizedOffset, normalizedOffset + 20),
    });
    return { skipPrefix };
  }

  // ---- 空页（纯图片/空白页）检测与自动翻页上报 ----
  // 与 Koodo 同理：这一页提取不到任何 <p>/<h> 文本（封面、插图页、空白页）时，
  // 周期性上报"空页 + 页面指纹"，让 background 判断"翻页后内容有没有变化"——
  // 连续插画页（内容不同但都无文本）不应误停，只有翻到书尾（翻页后内容不再变）
  // 才停止朗读。不能用"连续 N 次空页"判断（书中间可能连续很多页插画）。
  let emptyPageTimer = null;

  function getEmptyPageFingerprint() {
    const text = (document.body ? document.body.textContent : "").replace(/\s+/g, " ").trim();
    const imgs = document.querySelectorAll("img");
    const imgSrcs = [];
    for (const img of imgs) {
      imgSrcs.push(img.getAttribute("src") || img.getAttribute("data-src") || img.getAttribute("alt") || "");
    }
    return {
      textLen: text.length,
      textHead: text.slice(0, 40),
      imgCount: imgs.length,
      imgSrcs: imgSrcs.slice(0, 5),
    };
  }

  function stopEmptyPageReport() {
    if (emptyPageTimer) {
      clearInterval(emptyPageTimer);
      emptyPageTimer = null;
    }
  }

  function scheduleEmptyPageReport() {
    if (emptyPageTimer) return;
    const fire = () => {
      if (!window.__hasEverMatched) {
        stopEmptyPageReport();
        return;
      }
      if (extractCurrentPageText()) {
        // 翻页/滚动到正文了，停止空页上报
        stopEmptyPageReport();
        return;
      }
      const fp = getEmptyPageFingerprint();
      diagLog("空页周期上报（无正文，触发朗读中自动翻页）", fp);
      safeSendMessage({
        type: "PAGE_TEXT_UPDATED",
        url: location.href,
        data: null,
        fingerprint: fp,
      });
    };
    fire();
    emptyPageTimer = setInterval(fire, 1000);
  }

  function reportIfChanged() {
    const data = extractCurrentPageText();

    if (!data) {
      // 顶层 play.google.com 页面本来就永远匹配不到正文，不应该发送任何消息（避免用无意义的
      // "没有正文"覆盖掉真正的正文 iframe 发来的数据）。
      // 只有"这个 frame 之前确实成功提取过、现在突然匹配不到了"才主动上报空页——
      // 常见场景：封面/插图/空白页，或同一个 iframe 内用 SPA 方式切换到了另一本书。
      // 空页走周期上报（带指纹），让 background 判断翻页后内容有无变化，而不是只报一次。
      if (window.__hasEverMatched) scheduleEmptyPageReport();
      return;
    }
    stopEmptyPageReport();
    window.__hasEverMatched = true;

    const serialized = JSON.stringify(data);

    // 翻页刚触发、还在等新页渲染完成期间：如果这次抓到的内容跟翻页前最后一次上报的
    // 一模一样，说明这是动画中间态、新页还没真正渲染出来（旧的 reader-page 可能还
    // 短暂留在 DOM 里、或者 opacity 判断有一瞬间的过渡态），直接丢弃，不当成"新内容"
    // 上报给 background——否则会被误判成"这一页内容变化了"，触发一次不必要的整页重算
    // 和对齐校验，徒增一次可能出错的机会。
    if (window.__pageTurnPending && serialized === window.__pageTurnOldSnapshot) {
      diagLog("提取到内容但等于翻页前快照，判定为旧页中间态，丢弃");
      return;
    }
    window.__pageTurnPending = false;

    if (serialized === window.__lastReportedText) return; // 内容没变化，不重复上报
    window.__lastReportedText = serialized;

    diagLog(`提取到 ${data.length} 段，正式上报`, {
      preview: data.slice(0, 3).map((d) => (d.text || "").slice(0, 15)),
    });

    safeSendMessage({
      type: "PAGE_TEXT_UPDATED",
      url: location.href,
      data,
    });
  }

  // 模拟翻页动作：Play Books 的 DOM 里能看到 readerkeyboardnavigationhandler 这类属性，
  // 说明它支持键盘翻页。不确定这个监听器具体挂在外层 play.google.com 还是内层
  // books.googleusercontent.com 的 iframe，所以两层都模拟一次按键，没监听的那层不会有反应，无害。
  function simulateNextPageKey() {
    const opts = {
      key: "ArrowRight",
      code: "ArrowRight",
      keyCode: 39,
      which: 39,
      bubbles: true,
      cancelable: true,
    };
    document.dispatchEvent(new KeyboardEvent("keydown", opts));
    document.dispatchEvent(new KeyboardEvent("keyup", opts));
  }


  // ---- 高亮当前朗读块 ----
  // 优先用 CSS Custom Highlight API（不改 DOM，不触发布局重排/位移）。
  // 不支持时再回退到 <mark>，但 mark 仍可能在双栏排版里挤字。
  const HIGHLIGHT_NAME = "ebook-tts-chunk";
  const canUseCssHighlight = !!(window.CSS && CSS.highlights && window.Highlight);

  if (canUseCssHighlight && !document.getElementById("ebook-tts-highlight-style")) {
    const style = document.createElement("style");
    style.id = "ebook-tts-highlight-style";
    style.textContent = `::highlight(${HIGHLIGHT_NAME}) { background-color: rgba(255, 200, 0, 0.4); color: inherit; }`;
    (document.head || document.documentElement).appendChild(style);
  }

  // 回退路径才用到的 <mark> 列表
  let currentHighlightMarks = [];
  // CSS Highlight 路径下保存 Range，供清除
  let currentHighlightRanges = [];

  const HIGHLIGHT_MARK_CSS = [
    "background-color: rgba(255, 200, 0, 0.4)",
    "color: inherit",
    "padding: 0",
    "margin: 0",
    "border: none",
    "border-radius: 0",
    "font: inherit",
    "line-height: inherit",
    "display: inline",
  ].join(";");

  function clearHighlight() {
    if (canUseCssHighlight) {
      try {
        CSS.highlights.delete(HIGHLIGHT_NAME);
      } catch (e) {}
    }
    currentHighlightRanges = [];
    for (const mark of currentHighlightMarks) {
      const parent = mark.parentNode;
      if (!parent) continue;
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
      parent.removeChild(mark);
      parent.normalize();
    }
    currentHighlightMarks = [];
  }

  // 用整段 chunk 文字在页面文本里定位，高亮范围覆盖整个 chunk（不再像之前那样
  // 只覆盖前 15 个字），这样朗读到哪块、页面上高亮到哪块，视觉上完全对应，
  // 用户能直观看到文本实际是怎么切分的。
  // 定位起点优先用完整文字去匹配；如果因为 DOM 结构差异导致完整匹配失败（比如
  // 空白字符规范化后仍有细微出入、或者跨越了某些不参与提取的元素），逐步缩短
  // 匹配长度重试，直到找到一个能匹配上的起点为止——即使不能做到百分百精确，
  // 也尽量高亮到位，而不是直接放弃整段不高亮。
  const MIN_MATCH_LEN = 6; // 最短还能接受的匹配长度，太短容易在页面里误中别的位置

  // 高亮锚点（方案A：单向推进）：记录上次 chunk 在页面文本里的结束位置，下次从它
  // 之后开始搜——防止"全文 indexOf 命中前面重复处"导致高亮跳回。Play Books 翻页后
  // 当前页文本会整体变化，这里用"页面文本（normalizedCombined）"做指纹——同一页内
  // 页面文本保持不变（chunk 在变但页面没变），指纹稳定，锚点得以单向推进；翻页后
  // 页面文本整体替换，指纹变化，锚点自动重置（否则旧锚点会让新页找不到文本）。
  let lastEndChar = -1;
  let lastPageFingerprint = null;

  function fingerprintForText(t) {
    // 页面文本的首尾各 24 字符 + 长度，足以区分相邻页；同页内该值稳定。
    const s = t.replace(/\s+/g, " ").trim();
    return s.length + ":" + s.slice(0, 24) + "|" + s.slice(-24);
  }

  // 返回一组 Range，而不是单个跨节点的大 Range：surroundContents 只能处理
  // "起点和终点落在同一个父节点结构内"的范围，一旦这段文字跨越了段落边界
  // （比如从一个 <p> 延伸到下一个 <p>，中间隔着分页符），构造一个横跨它们的
  // 单一 Range 交给 surroundContents 包裹，规范不允许、会直接抛错，导致整段
  // 高亮失败或者提前截断在段落边界处。
  // 拆成多个小 Range，每个都完整落在单一文本节点内，分别包裹——这样即使
  // 中间跨过了段落/分页符，能高亮的每一段文字都会正常显示，只是在段落间隙处
  // 自然断开（这是排版本身的间距，不是高亮逻辑能消除的）。
  function findRangesForText(searchText) {
    const fullText = searchText.replace(/\s+/g, " ").trim();
    if (!fullText) return [];

    // 只在真正可见、激活的 reader-rendered-page 里查找（含左右两栏）。
    const activeRenderedPages = getActiveRenderedPageScopes();
    if (activeRenderedPages.length === 0) return [];

    // 建「规范化字符 → 原始 DOM 位置」映射，避免空白压缩后偏移错位，
    // 导致一整块里后半段（跨栏/接近页尾）贴不上高亮。
    const normMap = []; // normIndex -> { node, offset }
    let combined = "";
    for (const scope of activeRenderedPages) {
      const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, null);
      let node;
      while ((node = walker.nextNode())) {
        const t = node.textContent;
        combined += t;
        for (let i = 0; i < t.length; i++) {
          const ch = t[i];
          if (/\s/.test(ch)) {
            if (normMap.length > 0 && normMap[normMap.length - 1]._space) continue;
            normMap.push({ node, offset: i, _space: true, ch: " " });
          } else {
            normMap.push({ node, offset: i, _space: false, ch });
          }
        }
      }
    }
    const normalizedCombined = normMap.map((m) => m.ch).join("");
    if (!normalizedCombined) return [];

    // 匹配策略（按优先级）：
    // 1) 整段  2) 从前往后缩短前缀  3) 从后往前缩短后缀（跨页时页首只有后半句）
    // 找到后只高亮「当前页 DOM 里实际存在」的那一段，不再按 fullText.length 盲取。
    // fromIndex：方案A单向推进——从上次 chunk 结束位置之后开始搜，防止命中前面重复处。
    function tryFind(str, fromIndex) {
      if (str.length < MIN_MATCH_LEN) return -1;
      return normalizedCombined.indexOf(str, fromIndex || 0);
    }

    // 翻页检测：基于"页面文本 normalizedCombined"做指纹。同一页内页面文本稳定，
    // 指纹不变 → 锚点继续单向推进；翻页后页面文本整体替换，指纹变化 → 重置锚点。
    const pageFp = fingerprintForText(normalizedCombined);
    if (pageFp !== lastPageFingerprint) {
      lastPageFingerprint = pageFp;
      lastEndChar = -1;
    }
    const from = Math.max(0, lastEndChar);

    let idx = tryFind(fullText, from);
    let matched = fullText;

    if (idx === -1) {
      let matchLen = fullText.length;
      while (matchLen >= MIN_MATCH_LEN) {
        matchLen = Math.floor(matchLen * 0.85);
        const prefix = fullText.slice(0, matchLen);
        idx = tryFind(prefix, from);
        if (idx !== -1) {
          matched = prefix;
          break;
        }
      }
    }

    if (idx === -1) {
      let matchLen = fullText.length;
      while (matchLen >= MIN_MATCH_LEN) {
        matchLen = Math.floor(matchLen * 0.85);
        const suffix = fullText.slice(fullText.length - matchLen);
        idx = tryFind(suffix, from);
        if (idx !== -1) {
          matched = suffix;
          break;
        }
      }
    }

    // 自愈兜底：单向推进失败时，锚点可能已被污染（比如旧页面残留的 lastEndChar），
    // 回退到全文 from=0 再完整匹配一次；命中后用正确位置重置锚点。
    if (idx === -1 && lastEndChar > 0) {
      const retryIdx = normalizedCombined.indexOf(fullText);
      if (retryIdx !== -1) {
        idx = retryIdx;
        matched = fullText;
      }
    }

    if (idx === -1) return [];

    // 从命中点起，尽量沿 fullText 向后对齐扩展，把当前页上同一块的剩余文字也罩住
    // （解决：前缀命中后半截因空白/换行对不齐而漏高亮）
    let coverLen = matched.length;
    let fi = matched === fullText ? 0 : fullText.indexOf(matched);
    if (fi < 0) fi = 0;
    let ni = idx + matched.length;
    let ti = fi + matched.length;
    while (ti < fullText.length && ni < normalizedCombined.length) {
      const want = fullText[ti];
      const got = normalizedCombined[ni];
      if (want === got) {
        ti++;
        ni++;
        coverLen++;
        continue;
      }
      // 任一侧是空格则跳过再比
      if (want === " ") {
        ti++;
        continue;
      }
      if (got === " ") {
        ni++;
        coverLen++;
        continue;
      }
      break;
    }

    // 用 normMap 把 [idx, idx+coverLen) 转成若干不跨节点的 Range
    const ranges = [];
    let rangeStart = null;
    let prev = null;
    const end = Math.min(idx + coverLen, normMap.length);
    // 更新锚点：本 chunk 在页面文本里的结束位置，供下一次单向推进。
    // 注意：这里先记录 idx 起始位置，若最终没生成任何 Range（匹配串都是空白之类
    // 的极端情况）则不该推进锚点——所以 anchor 更新放在返回前的条件判断里。
    const anchorFrom = idx;
    const anchorTo = end;
    for (let i = idx; i < end; i++) {
      const m = normMap[i];
      if (m._space && m.ch === " ") {
        // 空白：结束当前 range，不单独包 mark（避免空方块）
        if (rangeStart) {
          try {
            const r = document.createRange();
            r.setStart(rangeStart.node, rangeStart.offset);
            r.setEnd(prev.node, prev.offset + 1);
            if (r.toString().trim().length > 0) ranges.push(r);
          } catch (e) {}
          rangeStart = null;
        }
        prev = m;
        continue;
      }
      if (
        !rangeStart ||
        prev.node !== m.node ||
        m.offset !== prev.offset + 1
      ) {
        if (rangeStart) {
          try {
            const r = document.createRange();
            r.setStart(rangeStart.node, rangeStart.offset);
            r.setEnd(prev.node, prev.offset + 1);
            if (r.toString().trim().length > 0) ranges.push(r);
          } catch (e) {}
        }
        rangeStart = m;
      }
      prev = m;
    }
    if (rangeStart && prev) {
      try {
        const r = document.createRange();
        r.setStart(rangeStart.node, rangeStart.offset);
        r.setEnd(prev.node, prev.offset + 1);
        if (r.toString().trim().length > 0) ranges.push(r);
      } catch (e) {}
    }
    // 有实际高亮范围才推进锚点；空匹配不推进（防止锚点被空白串污染）
    if (ranges.length > 0) lastEndChar = anchorTo;
    return ranges;
  }

  // 跨页高亮：翻页后 DOM 尚未稳定时第一次匹配常失败，记住最近一次文案并延迟重试。
  let lastHighlightText = null;
  let highlightRetryTimers = [];

  function clearHighlightRetries() {
    for (const t of highlightRetryTimers) clearTimeout(t);
    highlightRetryTimers = [];
  }

  function applyHighlightRanges(text) {
    const ranges = findRangesForText(text);
    if (ranges.length === 0) return false;

    // 先清掉旧高亮（CSS 路径 set 会覆盖同名，仍显式 delete 更干净）
    clearHighlight();

    if (canUseCssHighlight) {
      try {
        // 不插入任何节点，只注册 Range → 零布局影响
        const highlight = new Highlight(...ranges);
        CSS.highlights.set(HIGHLIGHT_NAME, highlight);
        currentHighlightRanges = ranges;
        return true;
      } catch (e) {
        diagLog("CSS Highlight 失败，回退 mark", { error: String(e) });
      }
    }

    for (const range of ranges) {
      try {
        const mark = document.createElement("mark");
        mark.style.cssText = HIGHLIGHT_MARK_CSS;
        range.surroundContents(mark);
        currentHighlightMarks.push(mark);
      } catch (e) {}
    }
    return currentHighlightMarks.length > 0;
  }

  function highlightChunk(text) {
    // 高亮涉及的 DOM 操作会触发 MutationObserver，暂停以免误报内容变化。
    clearHighlightRetries();
    observer.disconnect();
    try {
      // 先尝试新高亮，成功后再清旧 mark，减少跨页瞬间「完全无高亮」
      lastHighlightText = text || null;
      if (!text) {
        clearHighlight();
        // 停止/清空：重置高亮锚点，下次重新朗读从头搜（避免旧锚点污染新 session）
        lastEndChar = -1;
        lastPageFingerprint = null;
        diagLog("高亮清空：重置锚点");
        return;
      }

      // CSS Highlight 不改 DOM；仍 disconnect 以兼容 mark 回退路径
      if (!applyHighlightRanges(text)) {
        diagLog("高亮未命中，安排延迟重试", { preview: text.slice(0, 20), lastEndChar });
        [300, 700, 1200, 2000, 3000].forEach((ms) => {
          const t = setTimeout(() => {
            if (lastHighlightText !== text) return;
            observer.disconnect();
            try {
              if (applyHighlightRanges(text)) {
                diagLog(`高亮延迟 ${ms}ms 后命中`);
              }
            } finally {
              observer.observe(document.body, {
                childList: true,
                subtree: true,
                characterData: true,
              });
            }
          }, ms);
          highlightRetryTimers.push(t);
        });
      }
    } catch (e) {
      // 兜底
    } finally {
      observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    }
  }

  let lastTurnAt = 0;

  // 暂停/继续：主键盘 . 和小键盘 .（NumpadDecimal）都触发。
  // 只在页面本身有焦点时生效（鼠标点过书页区域），输入框/可编辑区域里按 . 不拦截。
  // 本脚本会同时注入顶层 + 正文 iframe，同一按键可能两边都收到，用短防抖避免连发两次。
  function isTypingTarget(el) {
    if (!el || el === document.body || el === document.documentElement) return false;
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.isContentEditable) return true;
    return false;
  }

  let lastToggleAt = 0;
  document.addEventListener(
    "keydown",
    (e) => {
      const isPeriod =
        e.code === "Period" ||
        e.code === "NumpadDecimal" ||
        e.key === ".";
      if (!isPeriod) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTypingTarget(e.target) || isTypingTarget(document.activeElement)) return;

      const now = Date.now();
      if (now - lastToggleAt < 300) return;
      lastToggleAt = now;

      e.preventDefault();
      e.stopPropagation();
      diagLog("快捷键 . 触发暂停/继续");
      safeSendMessage({ type: "TOGGLE_PLAYBACK" });
    },
    true
  );

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "PLAYBOOKS_GET_START_INDEX") {
      // 顶层 play.google.com frame 没有正文，不回应，让真正有正文的 iframe 回答
      if (!window.__hasEverMatched) return;
      // 优先：用户用鼠标选中了一段文字，就从选中文字的第一个字开始朗读；
      // 没有有效选中（或选中的起点不在正文里）再回退到"视口内第一个完整句子"。
      const selectionResult = findSelectionStartIndex();
      const result = selectionResult || findVisibleStartIndex();
      diagLog(`回应起点查询：skipPrefix 长度=${(result.skipPrefix || "").length}`, {
        fromSelection: !!selectionResult,
      });
      sendResponse(result);
      return true;
    }
    if (message.type === "PB_START_READING" || message.type === "PB_STOP_READING") {
      // 朗读停止/重新开始时停掉空页周期上报：emptyPageTimer 只在提取到正文时
      // 自己停，朗读停止后残留实例会继续上报旧页面指纹，在下一次朗读开始时被
      // background 误判为"当前页是空页"而误翻页。background 在 stop/start 时
      // 都会广播一条，所有 content 实例（含旧实例）都停。
      stopEmptyPageReport();
    }
    if (message.type === "TURN_PAGE") {
      const now = Date.now();
      // 去重：chrome.tabs.sendMessage 会广播给这个标签页里的所有 frame（顶层页面 +
      // 所有 iframe），观察到同一次翻页请求会收到好几条"TURN_PAGE"日志，如果这是因为
      // 同一个 frame 内消息被重复投递（而不是不同 frame 各自响应），这个时间窗口能挡住；
      // 如果是不同 frame 各自都在响应，这里挡不住——每个 frame 是独立的 window，
      // 这个变量各自维护，不能跨 frame 共享去重状态。
      if (now - lastTurnAt < 1500) {
        diagLog("TURN_PAGE 被去重挡下（1.5s 内已经响应过）");
        return;
      }
      lastTurnAt = now;
      diagLog("收到 TURN_PAGE");
      // 翻页动画期间 DOM 会经历多次中间态变化（旧页淡出、新页淡入、内容逐步渲染），
      // 先暂停 observer 再做任何 DOM 操作（清高亮、模拟按键），避免这些中间态和
      // 我们自己触发的 DOM 变化混在一起，引出一堆无意义的上报；
      // 同时记下"翻页前最后一次上报的内容"，翻页后如果还抓到跟这份一样的内容
      // （说明还是旧页、新页没渲染完），reportIfChanged 里会直接丢弃，不当新内容上报。
      // 不在这里 clearHighlight：新页渲染后 HIGHLIGHT_CHUNK 会重试贴上；过早清掉
      // 会导致跨页那一两秒完全没有高亮。
      observer.disconnect();
      window.__pageTurnPending = true;
      window.__pageTurnOldSnapshot = window.__lastReportedText;
      simulateNextPageKey();
      observer.observe(document.body, { childList: true, subtree: true, characterData: true });
      // 翻页后若仍有待高亮文案，主动再试一次（与 HIGHLIGHT_CHUNK 延迟重试互补）
      if (lastHighlightText) {
        const pending = lastHighlightText;
        setTimeout(() => {
          if (lastHighlightText === pending) highlightChunk(pending);
        }, 600);
      }
    }
    if (message.type === "HIGHLIGHT_CHUNK") {
      highlightChunk(message.text);
    }
  });


  // 首次加载执行一次
  reportIfChanged();

  // Play Books 翻页时是异步替换 DOM，用 MutationObserver 监听变化，防抖 700ms 避免中间态多次触发。
  // 这个值之前是 300ms，翻页动画+分批渲染经常还没稳定就被抽到中间态，
  // 拉长防抖窗口能让大部分情况在真正稳定之后才抽取一次，减少不必要的多次上报。
  const EXTRACT_DEBOUNCE_MS = 700;

  const observer = new MutationObserver(() => {
    clearTimeout(window.__extractDebounce);
    window.__extractDebounce = setTimeout(reportIfChanged, EXTRACT_DEBOUNCE_MS);
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
})();
