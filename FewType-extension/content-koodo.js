// ---- Koodo Reader 内容脚本 ----
//
// 跟 content-playbooks.js 完全独立，DOM 结构、注入方式都不一样。
//
// 关键约束：
//   - 正文装在 <iframe id="kookit-iframe">，sandbox 没开 allow-scripts，不能注入，
//     只能从顶层页面跨边界读 contentDocument（sandbox 开了 allow-same-origin）
//   - 翻页不改变 DOM：整章内容用 CSS 多栏布局一次性渲染，只有跨章节才整体刷新//   - 正文装在 <iframe id="kookit-iframe">，src 是 about:blank，不能往它
//     内部注入脚本（sandbox 没开 allow-scripts），只能从顶层页面跨边界
//     读取 contentDocument（sandbox 开了 allow-same-origin，读取是被允许的）
//   - 翻页不改变 DOM：整章内容用 CSS 多栏布局一次性渲染，只有跨章节才会
//     整体刷新

(function () {
  function safeSendMessage(msg) {
    try {
      chrome.runtime.sendMessage(msg);
    } catch (e) {
      // 忽略：扩展上下文失效（重新加载扩展后旧页面还没刷新），等页面刷新后自愈
    }
  }

  function diagLog(message, data) {
    safeSendMessage({ type: "DIAG_LOG", source: "content-koodo", message, data });
  }

  const IFRAME_SELECTOR = "iframe#kookit-iframe";
  const SELECTOR = "p, h1, h2, h3, h4, h5, h6";

  // DOM 结构诊断：输出当前 kookit-iframe 的数量 / id 和 iframe body 的文本概况。
  // 用于定位"高亮索引章节 ≠ 朗读/选中章节"的根因——可能是多 iframe（content 只
  // 拿第一个），也可能是 Koodo 按视口渲染导致 body 内容随翻页/滚动变化。
  function domStructureInfo() {
    const iframes = document.querySelectorAll(IFRAME_SELECTOR);
    const info = {
      iframeCount: iframes.length,
      iframeIds: Array.from(iframes).map((f) => f.id || "(no-id)"),
    };
    const doc = getIframeDocument();
    if (doc && doc.body) {
      const t = doc.body.textContent.replace(/\s+/g, " ").trim();
      info.bodyLen = t.length;
      info.bodyHead = t.slice(0, 30);
    } else {
      info.bodyLen = -1;
      info.bodyHead = "(no body)";
    }
    return info;
  }

  // 跟 content-playbooks.js 里 TERMINAL_PUNCTUATION 同一份规则：判断一段文字
  // 是不是"正常收尾"，用来识别口号式短段落（不以标点结尾的短行当 heading 处理）。
  const TERMINAL_PUNCTUATION = /[。！？…—」』）)\]"'".!?;:''"]$/;
  const SHORT_LINE_MAX = 20;

  function pushParagraph(result, seen, el) {
    if (seen.has(el)) return false;
    seen.add(el);

    const text = el.textContent.replace(/\s+/g, " ").trim();
    if (!text) return false;

    const tag = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag)) {
      result.push({ type: "heading", level: Number(tag[1]), text });
      return true;
    }

    const isShortNopunct = !TERMINAL_PUNCTUATION.test(text) && Array.from(text).length <= SHORT_LINE_MAX;
    result.push({ type: isShortNopunct ? "heading" : "body", text });
    return true;
  }

  // 找到当前可访问的 iframe contentDocument。iframe 可能还没加载出来，
  // 或者这次找到的跟上次是同一个（没有变化）——调用方自己判断变没变。
  function getIframeDocument() {
    const iframe = document.querySelector(IFRAME_SELECTOR);
    if (!iframe) return null;
    try {
      // 跨边界访问：只要 sandbox 开了 allow-same-origin 就不会抛出跨域错误；
      // 如果哪天页面结构变了导致这里抛错，用 try/catch 兜住，不让整个脚本挂掉。
      return iframe.contentDocument || null;
    } catch (e) {
      return null;
    }
  }

  // 返回 { data, elements, doc }：
  //   data —— 跟 content-playbooks.js 一致的 {type, text} 数组
  //   elements —— data 每一项对应的 DOM 元素（顺序严格对齐），用于坐标定位
  //   doc —— iframe 内部的 document，坐标定位时要用它的视口尺寸
  function extractChapterText() {
    const doc = getIframeDocument();
    if (!doc || !doc.body) return null;

    const seen = new Set();
    const data = [];
    const elements = [];
    doc.body.querySelectorAll(SELECTOR).forEach((el) => {
      if (pushParagraph(data, seen, el)) elements.push(el);
    });

    return data.length > 0 ? { data, elements, doc } : null;
  }

  // 在候选段落内部定位"当前视口内可见文字范围里，第一个大标点之后"的
  // 字符偏移——找到了返回具体偏移；这个段落视口内可见部分自始至终没有
  // 大标点（比如只显示了半句就翻页了），返回 null，让调用方去检查下一个
  // 候选段落（继续往后找，因为下一个候选段落如果也跟视口重叠，大概率是
  // 从它自己的开头就开始显示了）。
  //
  // 做法：用 Range API 逐字测量实际渲染坐标——先大步长粗扫找到大致落入
  // 视口的范围，再逐字精细回退找到真正的起点，然后从起点继续往后逐字
  // 扫描，边扫边判断字符是否还在视口内，一旦遇到大标点就返回；如果先
  // 扫出了视口范围（说明这段可见文字里没有大标点），返回 null。
  //
  // 不用二分查找（假设字符位置到渲染坐标单调递增）——如果这个段落内部的
  // 文字本身也被 CSS 多栏布局拆到了不止一栏渲染（完全可能，多栏布局是对
  // 整个容器统一排版，不会因为文字属于同一个 <p> 就被限制在一栏内），
  // 字符位置到坐标的映射会有跳跃、不是单调的，二分查找会给出错误结果。
  const STEP = 8; // 粗扫步长——先隔几个字测一次，缩小范围，再逐字精细定位
  // 任意标点都算分界：句号/问号/叹号，以及逗号、顿号、分号、冒号、
  // 省略号、破折号，还有各类右括号/右引号（中英文都覆盖）。
  // 从这些标点之后开始念，避免从视口内半截词语中间起读。
  const MAJOR_PUNCT_RE = /[。！？，、；：…—～,.!?;:…—\-）\)\]\}】」』》〉"'"]/;

  function findSentenceStartInViewport(el, doc) {
    const view = doc.defaultView;
    if (!view) return null;

    const rawText = el.textContent;
    const len = rawText.length;
    if (len === 0) return null;

    // Range 只能操作具体的文本节点，不能直接对 <p> 元素本身定位——
    // 用 TreeWalker 把这个元素内部所有文本节点和累计字符偏移列出来。
    const walker = doc.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
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
          const range = doc.createRange();
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
      return rect && rect.left >= 0 && rect.left < view.innerWidth && rect.top >= 0 && rect.top < view.innerHeight;
    }

    // 大步长粗扫，找到第一个落入视口附近的位置
    let coarseHit = -1;
    for (let i = 0; i < len; i += STEP) {
      if (inViewport(rectAt(i))) {
        coarseHit = i;
        break;
      }
    }
    if (coarseHit === -1) return null; // 这段完全不在视口内

    // 从粗扫命中点往前，逐字符精细回退，找到真正第一个落入视口的字符
    let fineHit = coarseHit;
    for (let i = Math.max(0, coarseHit - STEP); i < coarseHit; i++) {
      if (inViewport(rectAt(i))) {
        fineHit = i;
        break;
      }
    }

    // 从命中位置继续往后扫描，边扫边判断是否还在视口内，遇到大标点就返回；
    // 先扫出了视口范围（这段可见文字里没有大标点）就返回 null。
    let rawOffset = null;
    for (let i = fineHit; i < len; i++) {
      const rect = rectAt(i);
      if (!inViewport(rect)) break; // 已经扫出当前页可见范围
      if (MAJOR_PUNCT_RE.test(rawText[i])) {
        rawOffset = i + 1;
        break;
      }
    }
    if (rawOffset === null) return null;

    // 提取阶段用的是规范化后的文本（连续空白压缩成一个空格、首尾去空白），
    // 这里算出的 rawOffset 是基于原始 el.textContent 的偏移，两者字符
    // 位置不完全对应，需要转换成规范化文本里的偏移，才能正确地在
    // extracted.data[i].text 上做 slice。
    const rawPrefix = rawText.slice(0, rawOffset);
    const normalizedPrefix = rawPrefix.replace(/\s+/g, " ").trimStart();
    return normalizedPrefix.length;
  }

  // 找到当前视口里第一个完整句子的起点，返回 { segmentIndex, charOffset }。
  //
  // 段落级别的候选定位试过两种更"精确"的方法都失败了，记录一下排除过程：
  //   1. 判断段落外接矩形是否跟视口相交——多栏布局下，一个很长的 <p> 标签
  //      内容会被浏览器自动断开渲染到好几栏里，getBoundingClientRect() 
  //      返回的是包住所有分裂部分的最小外接矩形，会横跨好几个视觉页面，
  //      导致早就翻过去的内容也被误判成"可见"。
  //   2. 用 caretRangeFromPoint 在某个采样点精确定位——试了 iframe 左上角、
  //      也试了去找 #page-area 容器再取它范围内的点，都不准。根源是我们
  //      对 Koodo 内部真正的坐标系统、容器层级缺乏可靠信息（#page-area 
  //      实际上是包裹 iframe 的外层容器，属于顶层文档，不在 iframe 内部，
  //      两者是不同的坐标系，贸然去 iframe 内部找它必然找不到；就算改成去
  //      顶层文档找，还需要做跨坐标系换算，继续在这条路上摸索、不断根据
  //      猜测调整，性价比很低）。
  //
  // 现在的做法：把所有跟视口 [0, viewportWidth] 有横向重叠的段落都当作
  // 候选（不再只取"重叠最大"的那一个）——用户反馈过：如果当前页顶部还
  // 残留着上一段落的收尾文字（哪怕只占一两行），应该优先从这部分文字里的
  // 下一个完整句子开始念，而不是跳过它、直接从"占据页面主体的那个新段落"
  // 开始（原来只取重叠最大的那一个段落，等于完全忽略了视口内其它段落的
  // 可见内容）。按正文顺序（对应阅读顺序）依次检查每个候选段落，在每个
  // 段落"视口内可见的那部分文字"里找标点——找到了就停，找不到就顺移到
  // 下一个候选段落继续找。
  function findVisibleStartIndex(extracted) {
    const doc = extracted.doc;
    const view = doc.defaultView;
    if (!view) return { segmentIndex: 0, charOffset: 0 };

    const viewportWidth = view.innerWidth;
    const candidates = [];
    let bestFallbackIndex = 0;
    let bestFallbackOverlap = -1;

    const geometry = extracted.elements.map((el, i) => {
      const rect = el.getBoundingClientRect();
      const overlap = Math.max(0, Math.min(rect.right, viewportWidth) - Math.max(rect.left, 0));
      if (overlap > 0) candidates.push(i);
      if (overlap > bestFallbackOverlap) {
        bestFallbackOverlap = overlap;
        bestFallbackIndex = i;
      }
      return {
        i,
        text: extracted.data[i].text.slice(0, 10),
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        overlap: Math.round(overlap),
      };
    });

    candidates.sort((a, b) => a - b); // 按正文顺序排列，对应阅读顺序

    for (const idx of candidates) {
      if (extracted.data[idx].type !== "body") {
        // heading 通常很短，不会跨页，不用找标点，直接作为起点
        diagLog(`坐标定位选中 segmentIndex=${idx}, charOffset=0（heading）`, { viewportWidth, geometry });
        return { segmentIndex: idx, charOffset: 0 };
      }
      const el = extracted.elements[idx];
      const found = findSentenceStartInViewport(el, doc);
      if (found !== null) {
        diagLog(`坐标定位选中 segmentIndex=${idx}, charOffset=${found}`, {
          viewportWidth,
          segmentTextPreview: extracted.data[idx].text.slice(found, found + 20),
          geometry,
        });
        return { segmentIndex: idx, charOffset: found };
      }
    }

    // 兜底：所有候选段落视口内可见部分都没找到大标点（比如整页文字是一句
    // 很长、没有句号的话），退回用重叠宽度最大的段落，从它的开头开始。
    diagLog(`所有候选段落都没找到视口内的大标点，兜底选 segmentIndex=${bestFallbackIndex}`, {
      viewportWidth,
      geometry,
    });
    return { segmentIndex: bestFallbackIndex, charOffset: 0 };
  }

  // 用户用鼠标选中了一段文字时，从这段文字的第一个字开始朗读。
  // 返回 { segmentIndex, charOffset }（与 findVisibleStartIndex 同一契约）。
  // 没有有效选中（无选区 / 空选区 / 选中起点不在提取段落里）返回 null，
  // 由调用方回退到视口定位。
  // 注意：选中发生在 iframe#kookit-iframe 内部，本脚本跑在顶层页面，
  // 所以必须用 extracted.doc（iframe 的 document）的 getSelection / createRange，
  // 不能直接用顶层页面的 window.getSelection()。
  function findSelectionStartIndex(extracted) {
    const doc = extracted.doc;
    const sel = doc.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const selText = sel.toString().replace(/\s+/g, " ").trim();
    if (!selText) return null;

    // 用 range 的 start 而不是 anchor/focus：无论正选还是反选，
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
    if (idx === -1) return null; // 起点不在本章提取到的段落里，交给视口兜底

    // 把"元素开头到选中起点"这段原始文本用 Range 量出来，得到原始字符偏移。
    // 提取阶段做过规范化（连续空白压缩成空格、首尾去空白），原始偏移要换算成
    // 规范化文本里的偏移，才能正确地在 extracted.data[idx].text 上做 slice。
    let rawOffset = null;
    try {
      const measure = doc.createRange();
      measure.selectNodeContents(targetEl);
      measure.setEnd(startNode, startOffset);
      rawOffset = measure.toString().length;
    } catch (e) {
      rawOffset = null;
    }
    if (rawOffset === null) return null;

    const rawPrefix = targetEl.textContent.slice(0, rawOffset);
    const normalizedOffset = rawPrefix.replace(/\s+/g, " ").trimStart().length;
    diagLog(`选中文本起点定位 segmentIndex=${idx}, charOffset=${normalizedOffset}`, {
      selLen: selText.length,
      preview: extracted.data[idx].text.slice(normalizedOffset, normalizedOffset + 20),
    });
    return { segmentIndex: idx, charOffset: normalizedOffset };
  }

  let lastReportedText = null;
  let bodyObserver = null;
  let currentDoc = null;

  // ---- 高亮当前朗读块 ----
  // 比 content-playbooks.js 简单：Koodo 整章内容一次性都在同一个 body 里，
  // 翻页只是视觉上位移切换显示哪一栏，不涉及内容增减，所以搜索范围直接是
  // 整个 iframe 的 body，不需要像 Play Books 那样处理"哪些是当前真正显示、
  // 哪些是预加载不可见"的过滤。高亮标记插入到正确的 DOM 位置后也不需要
  // 额外做滚动跟随——翻页翻到哪一栏，高亮自然出现在那一栏里，用户正好能
  // 用肉眼判断"朗读进度有没有超出当前显示范围、该翻页了"。

  let currentHighlightMarks = [];

  // ---- 高亮定位状态（方案A：单向推进 + 修对称性）----
  // searchIndex 与提取侧（pushParagraph + buildChunksFromChapterData）完全同规则构建：
  // body 段文本归一化后连续拼接（段落间不插空格），heading 段单独记录。这样 chunk
  // （来自 body 拼接串）必然是这个索引串的精确子串，完整匹配接近 100%，不再依赖
  // 容错缩短去碰运气。lastEndChar 记录上次 chunk 在索引串里的结束位置，下次从它
  // 之后开始找——保证高亮严格单向推进，即使文本重复也不会跳回前面（韩文高亮
  // 跳回前面的根因就是"全文 indexOf 命中前面重复处"）。
  let highlightIndexDoc = null;
  let highlightIndex = null; // { combined, charMap, headings: [{norm,map}] }
  let lastEndChar = -1;

  function resetHighlightState() {
    highlightIndexDoc = null;
    highlightIndex = null;
    lastEndChar = -1;
  }

  // 归一化单个元素文本：逐字符产出 normalized 串，并记录每个"真实字符"→(textNode, offset)
  // 映射。规则与提取侧 pushParagraph 的 text.replace(/\s+/g," ").trim() 完全一致。
  // 归一化过程中产生的空格映射到"它对应的原始空白字符位置"（而不是前一字符），
  // 这样按 map 重建 Range 时空格能被正确覆盖，不会在高亮区域内缺漏空格。
  function normalizeElementText(doc, el) {
    const walker = doc.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    const nodes = [];
    let node;
    while ((node = walker.nextNode())) nodes.push(node);
    let norm = "";
    const map = [];
    let pendingSpace = false;
    let pendingSpaceInfo = null;
    let inLeadingWs = true;
    for (const tn of nodes) {
      const s = tn.textContent;
      for (let j = 0; j < s.length; j++) {
        const ch = s[j];
        if (/\s/.test(ch)) {
          if (!inLeadingWs && !pendingSpace) {
            pendingSpace = true;
            pendingSpaceInfo = { node: tn, offset: j }; // 记住空白字符真实位置
          }
        } else {
          if (pendingSpace) {
            norm += " ";
            map.push(pendingSpaceInfo);
            pendingSpace = false;
            pendingSpaceInfo = null;
          }
          inLeadingWs = false;
          norm += ch;
          map.push({ node: tn, offset: j });
        }
      }
    }
    return { norm, map };
  }

  // 构建搜索索引：复刻提取侧的 body/heading 分类（与 pushParagraph 同规则）
  function buildHighlightIndex(doc) {
    const seen = new Set();
    const bodyPieces = [];
    const headings = [];
    doc.body.querySelectorAll(SELECTOR).forEach((el) => {
      if (seen.has(el)) return;
      seen.add(el);
      const { norm, map } = normalizeElementText(doc, el);
      if (!norm) return;
      const tag = el.tagName.toLowerCase();
      const isHeading =
        /^h[1-6]$/.test(tag) ||
        (!TERMINAL_PUNCTUATION.test(norm) && Array.from(norm).length <= SHORT_LINE_MAX);
      if (isHeading) headings.push({ norm, map });
      else bodyPieces.push({ norm, map });
    });
    let combined = "";
    const charMap = [];
    for (const piece of bodyPieces) {
      for (let k = 0; k < piece.norm.length; k++) {
        charMap.push(piece.map[k]);
      }
      combined += piece.norm;
    }
    // 索引构建快照：供与日志开头"提取到整章 N 段"的 preview 对比，
    // 定位"提取时刻 DOM ≠ 高亮时刻 DOM"是否导致未命中
    if (typeof diagLog === "function") {
      diagLog("索引构建快照", {
        bodyCount: bodyPieces.length,
        headingCount: headings.length,
        bodyPreview: bodyPieces.slice(0, 12).map((p) => p.norm.slice(0, 12)),
        headingPreview: headings.slice(0, 12).map((h) => h.norm.slice(0, 12)),
        dom: domStructureInfo(),
      });
    }
    return {
      combined,
      charMap,
      headings,
      bodyLen: doc.body.textContent.replace(/\s+/g, " ").trim().length,
    };
  }

  function clearHighlight() {
    for (const mark of currentHighlightMarks) {
      const parent = mark.parentNode;
      if (!parent) continue;
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
      parent.removeChild(mark);
      parent.normalize();
    }
    currentHighlightMarks = [];
  }

  // 用整段 chunk 文字在索引串里定位，拆成多个不跨元素的小 Range 分别高亮——
  // 搜索索引与提取侧完全同规则构建（body 连续拼接、heading 独立），因此 chunk
  // 必为索引串的精确子串，完整匹配优先；仅在极端边界（如 DOM 与提取瞬间不一致）
  // 时按旧规则逐步缩短容错。surroundContents 只能处理"起点终点落在同一父节点"
  // 的范围，拆成多段各自限定在单一文本节点内，避免跨段落导致整体失败。
  const MIN_MATCH_LEN = 6;
  // 记录最近一次 rangesForCharMap 建 Range 的失败次数。Koodo 在朗读过程中会
  // 重排/懒渲染 DOM（古籍长文多栏排版尤其频繁），首次构建索引时记下的 textNode
  // 引用可能失效，setStart/setEnd 抛 IndexSizeError。失败数 >0 说明 charMap 已过期，
  // 需要重建索引后再定位，否则高亮会缺失（ranges 空）或只盖住部分区间（高亮偏短）。
  let highlightRangeFailures = 0;

  function findRangesForText(doc, searchText) {
    const fullText = searchText.replace(/\s+/g, " ").trim();
    if (!fullText || !doc.body) return [];

    // 跨章节 / 首次：重建搜索索引（索引与提取侧同规则，chunk 必为其精确子串）。
    // Koodo 复用同一个 iframe 对象、按视口动态替换 body 内容（翻章/滚动都会换），
    // doc 引用永远不变，仅靠 doc!==highlightIndexDoc 检测不到内容变化 → 索引过期。
    // 因此额外用「索引串是否还包含 chunk 前缀」探测索引是否过期：索引里找不到
    // chunk（DOM 换章节/视口切换导致旧索引与当前内容不匹配）→ 重建并重置锚点
    // （新内容从头找）。注意不能用「当前 body 是否包含 chunk」判断——用户选中的
    // 文本一定在当前 body 里，但索引可能还是旧章节的，那样会误判"索引有效"
    // 而用旧章节索引找新章节文本 → 永久未命中。
    if (doc === highlightIndexDoc && highlightIndex) {
      const probe = fullText.slice(0, 20);
      if (highlightIndex.combined.indexOf(probe) === -1) {
        diagLog("DOM 内容变化检测：chunk 前缀不在索引串，重建索引并重置锚点", {
          oldBodyLen: highlightIndex.bodyLen,
          newBodyLen: doc.body.textContent.replace(/\s+/g, " ").trim().length,
        });
        highlightIndex = buildHighlightIndex(doc);
        highlightIndexDoc = doc;
        lastEndChar = -1;
      }
    }
    if (doc !== highlightIndexDoc || !highlightIndex) {
      highlightIndex = buildHighlightIndex(doc);
      highlightIndexDoc = doc;
      lastEndChar = -1;
    }
    const { combined, charMap, headings } = highlightIndex;
    if (!combined) return [];

    // 方案A：从上次 chunk 结束位置之后开始找（单向推进，杜绝跳回前面）
    const from = Math.max(0, lastEndChar);

    // 完整匹配优先（修对称性后接近 100% 成功）
    let idx = combined.indexOf(fullText, from);
    let matchedLen = fullText.length;

    // 完整匹配失败（极端边界）→ 按旧规则容错缩短前缀再找
    if (idx === -1) {
      let matchLen = fullText.length;
      while (matchLen >= MIN_MATCH_LEN) {
        const sub = fullText.slice(0, matchLen);
        idx = combined.indexOf(sub, from);
        if (idx !== -1) {
          matchedLen = matchLen;
          break;
        }
        matchLen = Math.floor(matchLen * 0.75);
      }
    }

    // body 主串里找不到 → 尝试 heading 块（heading 独立，不参与 body 单向链）
    if (idx === -1) {
      for (const h of headings) {
        if (h.norm === fullText || fullText.startsWith(h.norm) || h.norm.startsWith(fullText)) {
          return rangesForCharMap(doc, h.map, 0, h.norm.length);
        }
      }
      // 自愈兜底：单向推进（from=lastEndChar）失败时，锚点可能已被污染
      // （例如早期版本重开朗读未重置 lastEndChar），此时退回到全文 from=0
      // 再完整匹配一次。正常路径（锚点干净）不会走到这里。
      if (lastEndChar > 0) {
        const retryIdx = combined.indexOf(fullText);
        if (retryIdx !== -1) {
          const retryEnd = retryIdx + fullText.length;
          const retryRanges = rangesForCharMap(doc, charMap, retryIdx, retryEnd);
          if (retryRanges.length > 0) {
            lastEndChar = retryEnd; // 用正确位置重置锚点，后续继续单向推进
            return retryRanges;
          }
        }
      }
      // 未命中诊断：把 chunk 前缀在索引串里的真实位置 / 附近文本打出来，
      // 用于定位"提取侧文本 ≠ 搜索侧索引"的具体差异点
      if (typeof diagLog === "function") {
        const probe = fullText.slice(0, 16);
        const probeIdx0 = combined.indexOf(probe); // 全文首现位置
        const probeFrom = combined.indexOf(probe, from); // 从锚点起
        const aroundStart = Math.max(0, from);
        const around = combined.slice(aroundStart, Math.min(combined.length, aroundStart + 140));
        // 检查 chunk 前缀是否被搜索侧判成了 heading（不在 body 主串里）
        let headingHit = null;
        for (const h of headings) {
          if (h.norm.includes(probe)) { headingHit = h.norm.slice(0, 24); break; }
        }
        diagLog(
          "未命中诊断：前缀探针位置",
          {
            probe,
            probeIdx0,
            probeFrom,
            headingHit, // 若非 null：chunk 前缀其实在某个 heading 里 → 分类不对称
            combinedLen: combined.length,
            headingsCount: headings.length,
            lastEndChar,
            fullTextLen: fullText.length,
            around, // 搜索起点 from 之后约 140 字，供人工比对
          }
        );
      }
      return [];
    }

    const end = idx + matchedLen;
    // 向后扩展：完整匹配失败、走了容错缩短时，命中串可能只是 chunk 的前缀。
    // 这里沿 fullText 逐字符对齐扩展，把索引串里能对上的剩余文字也罩住——
    // 避免"高亮只盖住一截"（与 content-playbooks.js 的 coverLen 逻辑一致）。
    // matched 是实际命中的串（完整匹配时即 fullText，容错时是缩短后的前缀）。
    let coverLen = matchedLen;
    let fi = matchedLen === fullText.length ? 0 : fullText.indexOf(fullText.slice(0, matchedLen));
    if (fi < 0) fi = 0;
    let ni = idx + matchedLen;
    let ti = fi + matchedLen;
    while (ti < fullText.length && ni < combined.length) {
      const want = fullText[ti];
      const got = combined[ni];
      if (want === got) { ti++; ni++; coverLen++; continue; }
      // 任一侧是空格则跳过再比
      if (want === " ") { ti++; continue; }
      if (got === " ") { ni++; coverLen++; continue; }
      break;
    }
    const ranges = rangesForCharMap(doc, charMap, idx, idx + coverLen);
    if (ranges.length > 0 && highlightRangeFailures === 0) {
      lastEndChar = idx + coverLen; // 记录本次 chunk 结束位置，供下次单向推进
      return ranges;
    }
    // charMap 的 textNode 已被 Koodo 重排/懒渲染失效（Range 建失败）：
    // 命中文本正确，但映射到的 DOM 节点已变 → 用当前 DOM 重建索引后重试，
    // 否则高亮会缺失（ranges 空）或只盖住部分区间（部分失败）。
    return retryHighlightWithRebuild(doc, fullText, idx);
  }

  // charMap 失效时的兜底：基于当前 DOM 重建索引，优先从旧命中位置附近重找，
  // 避免重建后从 from=0 命中前面重复处导致高亮跳回。
  function retryHighlightWithRebuild(doc, fullText, oldIdx) {
    if (typeof diagLog === "function") {
      diagLog("高亮兜底：charMap 失效，重建索引重试", {
        oldIdx,
        text: fullText.slice(0, 16),
      });
    }
    resetHighlightState();
    highlightIndex = buildHighlightIndex(doc);
    highlightIndexDoc = doc;
    const c = highlightIndex.combined;
    if (!c) return [];
    const newFrom = Math.max(0, oldIdx - 3);
    let ri = c.indexOf(fullText, newFrom);
    let rl = fullText.length;
    if (ri === -1) {
      // 旧位置附近没有（DOM 重排可能移动了文本）→ 全文兜底
      ri = c.indexOf(fullText);
    }
    if (ri !== -1) {
      const rr = rangesForCharMap(doc, highlightIndex.charMap, ri, ri + rl);
      if (rr.length > 0) {
        lastEndChar = ri + rl;
        return rr;
      }
    }
    return [];
  }

  // 把 charMap 的 [start,end) 拆成不跨文本节点的 Range 数组（供 surroundContents）
  function rangesForCharMap(doc, charMap, start, end) {
    highlightRangeFailures = 0;
    const ranges = [];
    let curNode = null;
    let curStart = 0;
    let curEnd = 0;
    for (let p = start; p < end; p++) {
      const m = charMap[p];
      if (!m) continue;
      if (m.node !== curNode) {
        if (curNode) {
          try {
            const r = doc.createRange();
            r.setStart(curNode, curStart);
            r.setEnd(curNode, curEnd);
            ranges.push(r);
          } catch (e) {
            highlightRangeFailures++;
          }
        }
        curNode = m.node;
        curStart = m.offset;
        curEnd = m.offset + 1;
      } else {
        curEnd = m.offset + 1;
      }
    }
    if (curNode) {
      try {
        const r = doc.createRange();
        r.setStart(curNode, curStart);
        r.setEnd(curNode, curEnd);
        ranges.push(r);
      } catch (e) {
        highlightRangeFailures++;
      }
    }
    return ranges;
  }

  // ---- 自动翻页跟随 ----
  function simulateNextPageKey() {
    const iframe = document.querySelector(IFRAME_SELECTOR);
    const doc = iframe && iframe.contentDocument;
    if (!doc) {
      diagLog("自动翻页失败：拿不到 kookit-iframe");
      return;
    }
    doc.dispatchEvent(
      new WheelEvent("wheel", {
        deltaY: 120,
        deltaX: 0,
        deltaMode: 0,
        bubbles: true,
        cancelable: true,
      })
    );
  }

  function simulateNextChapter() {
    const btn = document.querySelector(".next-chapter");
    if (btn) {
      btn.click();
      diagLog("已点击 .next-chapter 切章");
      return;
    }
    diagLog("找不到 .next-chapter，切章失败");
  }

  const OVERFLOW_TURN_RATIO = 0.45;
  const AUTO_TURN_MAX_ATTEMPTS = 5;
  const AUTO_TURN_CHECK_DELAY_MS = 1100;

  function classifyHighlight(doc) {
    const view = doc.defaultView;
    if (!view || currentHighlightMarks.length === 0) {
      return { anyVisible: false, overflowRatio: 0 };
    }
    const W = view.innerWidth;
    const H = view.innerHeight;
    let visibleArea = 0;
    let pastArea = 0;

    for (const mark of currentHighlightMarks) {
      if (!mark.isConnected) continue;
      for (const rect of mark.getClientRects()) {
        if (rect.width <= 0 || rect.height <= 0) continue;
        const area = rect.width * rect.height;
        const inX = rect.right > 0 && rect.left < W;
        const inY = rect.bottom > 0 && rect.top < H;
        if (inX && inY) visibleArea += area;
        if (rect.left >= W - 1 || rect.top >= H - 1 || (rect.right > W + 8 && inY)) {
          pastArea += area;
        }
      }
    }
    const overflowRatio = pastArea / (visibleArea + pastArea || 1);
    return { anyVisible: visibleArea > 0, overflowRatio };
  }

let isTurningPage = false; // 翻页状态锁：防止动画与渲染未完成时重复触发

function ensureHighlightVisible(doc) {
  // 1. 正在翻页冷却中，直接跳过，防止动画未完成时排队触发
  if (isTurningPage) return;

  // 2. 高亮未命中（DOM未渲染或网络卡顿数据未到达），严禁翻页！直接返回
  if (!currentHighlightMarks || currentHighlightMarks.length === 0) return;

  const { anyVisible, overflowRatio } = classifyHighlight(doc);

  // 3. 高亮已经在视口内，且没有大面积右侧溢出，正常阅读，不翻页
  if (anyVisible && overflowRatio < OVERFLOW_TURN_RATIO) return;

  // 4. 仅当高亮确实存在、且确信它在屏幕右侧溢出时才触发单次翻页
  if (overflowRatio >= OVERFLOW_TURN_RATIO) {
    diagLog(`高亮溢出视口，执行翻页 overflow=${overflowRatio.toFixed(2)}`);
    
    isTurningPage = true;
    simulateNextPageKey();

    // 留出 800ms 冷却时间（兼容翻页动画与网络延迟），期间拒绝任何翻页指令
    setTimeout(() => {
      isTurningPage = false;
      // 冷却结束后重新校验一次，若高亮跨多栏仍未落入视口，再补翻一次
      if (currentDoc) {
        const checkAgain = classifyHighlight(currentDoc);
        if (!checkAgain.anyVisible || checkAgain.overflowRatio >= OVERFLOW_TURN_RATIO) {
          ensureHighlightVisible(currentDoc);
        }
      }
    }, 800);
  }
}

let highlightDebounceTimer = null;

function highlightChunk(text) {
  const doc = currentDoc;
  if (!doc) return;

  // 网络恢复时可能短时间内连续发来多个 Chunk，轻量防抖，只高亮最新的文本
  if (highlightDebounceTimer) clearTimeout(highlightDebounceTimer);

  highlightDebounceTimer = setTimeout(() => {
    if (bodyObserver) bodyObserver.disconnect();
    try {
      // 新 chunk 到达，取消上一 chunk 的未命中重试（避免旧文本延迟命中贴错位置）
      clearHighlightRetries();
      clearHighlight();
      if (!text) {
        // 停止/清空高亮：重置单向推进锚点，下次重新朗读从新索引起点开始
        resetHighlightState();
        diagLog("高亮清空：重置锚点", { lastEndChar: -1 });
        return;
      }

      if (applyHighlightRanges(doc, text)) {
        safeSendMessage({ type: "KOODO_HIGHLIGHT_HIT" });
      } else {
        diagLog("高亮未命中，安排延迟重试", { preview: text.slice(0, 20), lastEndChar });
        retryMissedHighlight(doc, text, 0);
      }
    } catch (e) {
    } finally {
      if (doc.body) watchIframeBody(doc);
    }
  }, 50);
}

// 用 chunk 文本在索引串里定位并创建高亮 mark。成功（至少建出一个 mark）返回 true。
// highlightChunk 首试与 retryMissedHighlight 延迟重试共用，保证两处行为一致。
function applyHighlightRanges(doc, text) {
  const ranges = findRangesForText(doc, text);
  diagLog(
    `高亮定位 ${ranges.length > 0 ? "命中" : "未命中"}，text=${text.slice(0, 24)}…`,
    {
      ranges: ranges.length,
      lastEndChar,
      from: Math.max(0, lastEndChar - (text.replace(/\s+/g, " ").trim().length || 1)),
    }
  );
  if (ranges.length === 0) return false;

  for (const range of ranges) {
    try {
      const mark = doc.createElement("mark");
      mark.style.cssText = [
        "background: rgba(255, 200, 0, 0.45)",
        "color: inherit",
        "border-radius: 3px",
        "padding: 0 1px",
      ].join(";");
      range.surroundContents(mark);
      currentHighlightMarks.push(mark);
    } catch (e) {}
  }
  ensureHighlightVisible(doc);
  return currentHighlightMarks.length > 0;
}

// 高亮未命中时的延迟重试：逐档等待（给 Koodo 渲染 / 数据到达留时间），
// 全部失败后通知 background 走"翻页跳过"兜底——对齐 Play Books 的空页逻辑，
// 避免"抓不到文本的页面"一直卡住无高亮。
const HIGHLIGHT_RETRY_MS = [300, 700, 1200, 2000, 3000];
let highlightRetryTimers = [];

function clearHighlightRetries() {
  for (const t of highlightRetryTimers) clearTimeout(t);
  highlightRetryTimers = [];
}

function retryMissedHighlight(doc, text, attempt) {
  if (attempt >= HIGHLIGHT_RETRY_MS.length) {
    // 全部重试仍失败：上报 background，由它决定翻页跳过或停止（连续翻页仍无改善）
    safeSendMessage({ type: "KOODO_HIGHLIGHT_MISS" });
    return;
  }
  const t = setTimeout(() => {
    if (!currentDoc) return;
    if (bodyObserver) bodyObserver.disconnect();
    try {
      if (applyHighlightRanges(currentDoc, text)) {
        safeSendMessage({ type: "KOODO_HIGHLIGHT_HIT" });
        return;
      }
      retryMissedHighlight(currentDoc, text, attempt + 1);
    } catch (e) {
    } finally {
      if (currentDoc.body) watchIframeBody(currentDoc);
    }
  }, HIGHLIGHT_RETRY_MS[attempt]);
  highlightRetryTimers.push(t);
}

  // ---- 空页（纯图片/空白页）检测与自动翻页上报 ----
  // Koodo 按视口懒加载章节，遇到没有 p/h 文本的页面（封面、插画章、空白页），
  // extractChapterText 返回 null → 不发 KOODO_CHAPTER_UPDATED → background 拿不到
  // 内容 → 既不高亮也不翻页，朗读卡死。这里把"当前是空页"连同页面指纹上报
  // background，由它（仅在朗读中）触发滚动翻页跳过；翻页后如果 body 恢复文本，
  // extractChapterText 有值 → 自动停止上报。
  let emptyPageTimer = null;

  // 空页指纹：文本特征 + 图片特征 + 滚动位置。background 用"翻页后内容有没有变化"
  // 判断是否到书尾——连续 N 次无变化才停。不能用"连续无文本"判断：书中间可能有
  // 连续插画页（不同内容但都无文本），后面还有正文；滚动到底时内容才稳定不变。
  // imgSrcs 同时是诊断锚：验证 Koodo 不同插画页的 img 占位符是否可区分。
  function getEmptyPageFingerprint(doc) {
    const body = doc.body;
    const text = (body.textContent || "").replace(/\s+/g, " ").trim();
    const imgs = body.querySelectorAll("img");
    const imgSrcs = [];
    for (const img of imgs) {
      imgSrcs.push(img.getAttribute("src") || img.getAttribute("data-src") || img.getAttribute("alt") || "");
    }
    let scrollY = 0;
    try {
      if (doc.defaultView) scrollY = doc.defaultView.scrollY || 0;
    } catch (e) {}
    return {
      textLen: text.length,
      textHead: text.slice(0, 40),
      imgCount: imgs.length,
      imgSrcs: imgSrcs.slice(0, 5),
      scrollY,
    };
  }

  function stopEmptyPageReport() {
    if (emptyPageTimer) {
      clearInterval(emptyPageTimer);
      emptyPageTimer = null;
    }
  }

  function scheduleEmptyPageReport() {
    if (emptyPageTimer) return; // 已在周期上报中
    const fire = () => {
      const d = getIframeDocument();
      if (!d || !d.body) {
        stopEmptyPageReport();
        return;
      }
      if (extractChapterText()) {
        // body 恢复文本了（滚动/翻页到正文），停止空页上报
        stopEmptyPageReport();
        return;
      }
      const fp = getEmptyPageFingerprint(d);
      diagLog("空页周期上报（无 p/h 文本，触发朗读中自动翻页）", fp);
      safeSendMessage({ type: "KOODO_EMPTY_PAGE", fingerprint: fp });
    };
    fire();
    emptyPageTimer = setInterval(fire, 1000);
  }

  function reportIfChanged() {
    const extracted = extractChapterText();
    if (!extracted) {
      // 空页：body 存在但无 p/h 文本（纯图片/空白页）。周期上报给 background，
      // 由它（仅在朗读中）触发滚动翻页跳过；恢复文本后自动停止上报。
      // 守卫：只有本实例之前成功提取过正文（__hasEverMatched）才启动空页上报，
      // 对齐 Play Books —— 用户只是手动翻到插画页浏览（从未提取过正文）时不上报，
      // 避免未朗读的 tab/页面也产生空页上报流，污染正在朗读的 tab（"插画后遇文本"跳页）。
      const doc = getIframeDocument();
      if (doc && doc.body && window.__hasEverMatched) scheduleEmptyPageReport();
      return;
    }
    stopEmptyPageReport();
    window.__hasEverMatched = true;

    const serialized = JSON.stringify(extracted.data);
    if (serialized === lastReportedText) return;
    lastReportedText = serialized;

    diagLog(`提取到整章 ${extracted.data.length} 段`, {
      preview: extracted.data.slice(0, 3).map((d) => d.text.slice(0, 15)),
      dom: domStructureInfo(),
    });

    safeSendMessage({
      type: "KOODO_CHAPTER_UPDATED",
      url: location.href,
      data: extracted.data,
    });
  }

  function watchIframeBody(doc) {
    if (bodyObserver) bodyObserver.disconnect();
    bodyObserver = new MutationObserver(() => {
      clearTimeout(window.__koodoExtractDebounce);
      window.__koodoExtractDebounce = setTimeout(reportIfChanged, 300);
    });
    bodyObserver.observe(doc.body, { childList: true, subtree: true, characterData: true });
  }

  function pollForIframe() {
    const doc = getIframeDocument();
    if (doc && doc.body && doc !== currentDoc) {
      currentDoc = doc;
      diagLog("找到 kookit-iframe，开始监听内容变化");
      watchIframeBody(doc);
      attachIframeKeyListener(doc);
      reportIfChanged();
    } else if (doc) {
      attachIframeKeyListener(doc);
    }
  }

  setInterval(pollForIframe, 1000);
  pollForIframe();

  // 暂停/继续：主键盘 . 和小键盘 . 都触发。
  // 只在页面本身有焦点时生效，输入框/可编辑区域里按 . 不拦截。
  // Koodo 正文在 iframe 里，焦点常在 iframe 内，顶层和 iframe 的 document/window 都挂监听。
  // 主键盘 . 在中文输入法下有时 key 会变成 "。" / keyCode 229，所以同时认 code / key / keyCode。
  function isTypingTarget(el) {
    if (!el || el === document.body || el === document.documentElement) return false;
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function isPeriodKey(e) {
    if (e.code === "Period" || e.code === "NumpadDecimal") return true;
    if (e.key === "." || e.key === "。" || e.key === "．") return true;
    // 190 = 主键盘 . ，110 = 小键盘 .
    const kc = e.keyCode || e.which;
    if (kc === 190 || kc === 110) return true;
    return false;
  }

  let lastToggleAt = 0;
  function onTogglePeriodKey(e) {
    if (!isPeriodKey(e)) return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (isTypingTarget(e.target) || isTypingTarget(document.activeElement)) return;

    const now = Date.now();
    if (now - lastToggleAt < 400) return; // 防抖：keydown+keyup、顶层+iframe 可能连发
    lastToggleAt = now;

    try {
      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === "function") e.stopImmediatePropagation();
    } catch (_) {}

    diagLog("快捷键 . 触发暂停/继续", { code: e.code, key: e.key, keyCode: e.keyCode, type: e.type });
    safeSendMessage({ type: "TOGGLE_PLAYBACK" });
  }

  function bindToggleKeys(target) {
    if (!target || target.__koodoToggleBound) return;
    target.__koodoToggleBound = true;
    // keydown 优先；若页面在 keydown 里拦截了主键盘 .，keyup 仍可能收到
    target.addEventListener("keydown", onTogglePeriodKey, true);
    target.addEventListener("keyup", onTogglePeriodKey, true);
  }

  bindToggleKeys(document);
  bindToggleKeys(window);

  function attachIframeKeyListener(doc) {
    if (!doc) return;
    bindToggleKeys(doc);
    try {
      if (doc.defaultView) bindToggleKeys(doc.defaultView);
    } catch (_) {}
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "KOODO_GET_START_INDEX") {
      const extracted = extractChapterText();
      // 优先：用户用鼠标选中了一段文字，就从选中文字的第一个字开始朗读；
      // 没有有效选中（或选中起点不在正文里）再回退到"视口内第一个完整句子"。
      const selectionResult = extracted ? findSelectionStartIndex(extracted) : null;
      const result =
        selectionResult ||
        (extracted ? findVisibleStartIndex(extracted) : { segmentIndex: 0, charOffset: 0 });
      diagLog(`回应起点查询：segmentIndex=${result.segmentIndex}, charOffset=${result.charOffset}`, {
        fromSelection: !!selectionResult,
        dom: domStructureInfo(),
      });
      sendResponse(result);
      return true;
    }
    if (message.type === "HIGHLIGHT_CHUNK") {
      highlightChunk(message.text);
    }
    if (message.type === "KOODO_START_READING" || message.type === "KOODO_STOP_READING") {
      // 朗读停止/重新开始时停掉空页周期上报：emptyPageTimer 只在"提取到文本"时
      // 自己停，朗读停止后它仍会继续上报旧页面指纹，残留上报会在下一次朗读开始时
      // 被 background 误判为"当前页是空页"而触发翻页（"有文本连续跳页"）。这里
      // background 在 stop/start 时都会广播一条，所有 content 实例（含旧实例）都停。
      stopEmptyPageReport();
    }
    if (message.type === "KOODO_TURN_PAGE") {
      // background 判定"连续多个 chunk 高亮未命中"后触发：滚动翻页跳过，
      // 让 Koodo 渲染出后续内容（对齐 Play Books 空页自动翻页兜底）。
      diagLog("收到 KOODO_TURN_PAGE，滚动翻页跳过");
      simulateNextPageKey();
    }
    if (message.type === "KOODO_NEXT_CHAPTER") {
      simulateNextChapter();
    }
  });
})();