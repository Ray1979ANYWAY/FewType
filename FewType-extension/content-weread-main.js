// ---- 微信读书 main world 脚本 ----
// MAIN world 注册，document_start 运行，不受页面 CSP 限制，直接 hook CanvasRenderingContext2D.prototype
// 通过 window.postMessage 与 isolated world 的 content-weread.js 通信

(function () {
  "use strict";

  const SOURCE = "vox-weread-main";
  const CONTENT_SOURCE = "vox-weread-content";

  let charIndex = [];       // [{canvasIdx, x, y, size, ch, scaleX, scaleY, tx, ty}]
  let fullText = "";
  let canvasElements = [];
  let overlays = [];
  let rebuildTimer = null;
  let pageReported = false; // 当前 charIndex 是否已上报，下一页第一个 fillText 到来时清空

  // 绘制结束后静默多久认为一页完成（防抖）
  const REBUILD_DEBOUNCE_MS = 300;

  // 缓存每个 canvas 的完整 transform，避免每次 fillText 都调用 getTransform()
  const canvasTransformCache = new WeakMap();

  function getContextTransform(ctx, canvas) {
    let cached = canvasTransformCache.get(canvas);
    if (!cached) {
      try {
        const t = ctx.getTransform();
        cached = {
          scaleX: t.a || 1,
          scaleY: t.d || 1,
          translateX: t.e || 0,
          translateY: t.f || 0,
        };
      } catch (e) {
        cached = { scaleX: 2, scaleY: 2, translateX: 0, translateY: 0 };
      }
      canvasTransformCache.set(canvas, cached);
    }
    return cached;
  }

  // 逻辑坐标（fillText 的 x/y）转 CSS 坐标比例
  // 微信读书用 ctx.scale(2,2) 绘制，逻辑宽度 = canvas.width / scaleX
  // 用 getBoundingClientRect().width 作为 CSS 宽度，与覆盖层尺寸保持一致
  function logicalToCss(canvas, scaleX) {
    const r = canvas.getBoundingClientRect();
    const logicalWidth = canvas.width / (scaleX || 2);
    return r.width / logicalWidth;
  }

  // 离屏 canvas 测量字符实际宽度（解决西语/英语字符宽度不统一导致高亮重叠）
  // 中文等宽字符不受影响；i/l/j 窄字符和 m/w 宽字符用 measureText 精确测量
  const _measureCanvas = document.createElement("canvas");
  const _measureCtx = _measureCanvas.getContext("2d");
  const _measureCache = new Map(); // key: font + "|" + ch, value: width (logical px)
  function measureCharWidth(font, ch) {
    if (!font || !ch) return 0;
    const key = font + "|" + ch;
    const cached = _measureCache.get(key);
    if (cached !== undefined) return cached;
    try {
      _measureCtx.font = font;
      const w = _measureCtx.measureText(ch).width;
      _measureCache.set(key, w);
      return w;
    } catch (e) {
      return 0;
    }
  }

  // ---------- fillText hook ----------
  function installFillTextHook() {
    const R = CanvasRenderingContext2D.prototype;
    if (R.__voxWereadHooked) return;
    R.__voxWereadHooked = true;

    const origFillText = R.fillText;
    R.fillText = function (text, x, y, maxWidth) {
      try {
        const canvas = this.canvas;
        if (!canvas) return origFillText.apply(this, arguments);

        let idx = canvasElements.indexOf(canvas);
        if (idx === -1) {
          idx = canvasElements.length;
          canvasElements.push(canvas);
          ensureOverlayFor(canvas);
        }

        // 新页检测不在此处做——改为 hook clearRect / canvas.width 重置时清空
        // （见 installFillTextHook 末尾），避免零星绘制误清完整文本

        // 防抖：持续绘制时不断重置定时器，停手 300ms 后才重建
        if (rebuildTimer) clearTimeout(rebuildTimer);
        rebuildTimer = setTimeout(function () {
          rebuildTextAndNotify();
          pageReported = true;
        }, REBUILD_DEBOUNCE_MS);

        const str = String(text);
        const size = parseFloat(((this.font || "").match(/(\d+(?:\.\d+)?)px/) || [])[1]) || 16;
        const transform = getContextTransform(this, canvas);

        for (let i = 0; i < str.length; i++) {
          const ch = str[i];
          if (ch === "\n" || ch === "\r") continue;
          charIndex.push({
            canvasIdx: idx,
            x: str.length === 1 ? x : x + i * size,
            y: y,
            size: size,
            ch: ch,
            font: this.font || "",
            scaleX: transform.scaleX,
            scaleY: transform.scaleY,
            tx: transform.translateX,
            ty: transform.translateY,
            el: canvas,
          });
        }
      } catch (e) {
        // hook 出错不影响原绘制
      }
      return origFillText.apply(this, arguments);
    };

    console.log("[FewType] fillText hook installed (debounce 300ms + clearRect/width new-page detection)");

    // hook clearRect：区分模式
    // - 滚动模式：任何 clearRect 都清空全部字符（虚拟滚动重绘，旧内容已失效）
    // - 双栏模式：只清空对应 canvas 的字符（左右栏独立，清除右栏不影响左栏）
    const origClearRect = R.clearRect;
    R.clearRect = function (x, y, w, h) {
      try {
        const canvas = this.canvas;
        if (canvas && charIndex.length > 0) {
          const isScroll = document.documentElement.scrollHeight > document.documentElement.clientHeight + 100;
          if (isScroll) {
            charIndex = [];
          } else {
            const idx = canvasElements.indexOf(canvas);
            if (idx !== -1 && charIndex.some(function (c) { return c.canvasIdx === idx; })) {
              charIndex = charIndex.filter(function (c) { return c.canvasIdx !== idx; });
            }
          }
          if (rebuildTimer) { clearTimeout(rebuildTimer); rebuildTimer = null; }
        }
      } catch (e) {}
      return origClearRect.apply(this, arguments);
    };

    // hook fillRect：检测整画布不透明填充（微信读书切章时用白底 fillRect 覆盖整个画布来清屏，
    // 而不是 clearRect 或 canvas.width 重置）。整画布 + 不透明填充色 = 清屏，执行和 clearRect 一样的清理。
    const origFillRect = R.fillRect;
    R.fillRect = function (x, y, w, h) {
      try {
        const canvas = this.canvas;
        if (canvas && charIndex.length > 0) {
          const cw = canvas.width;
          const ch = canvas.height;
          // 整画布覆盖：x<=0, y<=0, w>=画布宽, h>=画布高
          const isFullCanvas = (x <= 0 && y <= 0 && w >= cw && h >= ch);
          if (isFullCanvas) {
            // 填充色不透明判断：不是 rgba，或 rgba 的 alpha=1
            const fs = String(this.fillStyle || "");
            const isOpaque = (!fs.startsWith("rgba")) || (fs.indexOf(",1)") !== -1) || (fs.indexOf(", 1)") !== -1);
            if (isOpaque) {
              const isScroll = document.documentElement.scrollHeight > document.documentElement.clientHeight + 100;
              if (isScroll) {
                charIndex = [];
              } else {
                const idx = canvasElements.indexOf(canvas);
                if (idx !== -1 && charIndex.some(function (c) { return c.canvasIdx === idx; })) {
                  charIndex = charIndex.filter(function (c) { return c.canvasIdx !== idx; });
                }
              }
              if (rebuildTimer) { clearTimeout(rebuildTimer); rebuildTimer = null; }
            }
          }
        }
      } catch (e) {}
      return origFillRect.apply(this, arguments);
    };

    // hook canvas.width 重置：同样区分模式
    const canvasWidthDesc = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, "width");
    if (canvasWidthDesc && typeof canvasWidthDesc.set === "function") {
      const origWidthSet = canvasWidthDesc.set;
      Object.defineProperty(HTMLCanvasElement.prototype, "width", {
        set: function (v) {
          try {
            if (charIndex.length > 0) {
              const isScroll = document.documentElement.scrollHeight > document.documentElement.clientHeight + 100;
              if (isScroll) {
                charIndex = [];
              } else {
                const idx = canvasElements.indexOf(this);
                if (idx !== -1 && charIndex.some(function (c) { return c.canvasIdx === idx; })) {
                  charIndex = charIndex.filter(function (c) { return c.canvasIdx !== idx; });
                }
              }
              if (rebuildTimer) { clearTimeout(rebuildTimer); rebuildTimer = null; }
            }
          } catch (e) {}
          return origWidthSet.call(this, v);
        },
        get: canvasWidthDesc.get,
        configurable: true,
      });
    }

  }

  // ---------- 文本重建 ----------

  // 从 SPAN.wr_absolute 提取 DOM 文本（滚动模式下后半部分正文用 DOM 渲染）
  // 直接按排序后的 span 逐个拼接，确保文本顺序和 getSortedDomSpans() 的索引严格对应
  function extractDomText() {
    const sorted = getSortedDomSpans();
    if (sorted.length === 0) return "";
    return sorted.map(function (s) { return s.text; }).join("");
  }

  // 获取排序后的 SPAN.wr_absolute 元素列表（用于 DOM 高亮）
  function getSortedDomSpans() {
    try {
      const spans = document.querySelectorAll("span.wr_absolute");
      if (spans.length === 0) return [];
      const all = [];
      for (let i = 0; i < spans.length; i++) {
        const el = spans[i];
        const txt = (el.textContent || "").trim();
        if (!txt) continue;
        const r = el.getBoundingClientRect();
        all.push({ text: txt, top: Math.round(r.top), left: Math.round(r.left), el: el });
      }
      all.sort(function (a, b) { return a.top - b.top || a.left - b.left; });
      return all;
    } catch (e) {
      return [];
    }
  }

  // 清除 DOM 高亮（移除所有 span 的背景色）
  function clearDomHighlight() {
    try {
      const spans = document.querySelectorAll("span.wr_absolute");
      for (let i = 0; i < spans.length; i++) {
        spans[i].style.backgroundColor = "";
        spans[i].style.borderRadius = "";
      }
    } catch (e) {}
  }

  // DOM 文本在合并后 fullText 中的起始位置（用于高亮时区分 canvas 部分和 DOM 部分）
  let domTextStart = -1;

  let domRetryCount = 0;
  let domRenderMonitor = null; // 滚动模式下监听 DOM 延迟渲染
  function rebuildTextAndNotify() {
    if (charIndex.length === 0) return;

    // 换章/重建时清理旧高亮，避免上一章的高亮 rect 残留到新页面
    clearHighlight();

    // 统计正文字号（出现频率最高的字号），用于标题行检测
    const rebuildSizeCount = {};
    for (let i = 0; i < Math.min(300, charIndex.length); i++) {
      const c = charIndex[i];
      if (c.isVirtual || c.canvasIdx === -1) continue;
      const s = Math.round(c.size * 10) / 10;
      rebuildSizeCount[s] = (rebuildSizeCount[s] || 0) + 1;
    }
    let bodySizeForTitle = 0;
    let rebuildBodyCount = 0;
    for (const s in rebuildSizeCount) {
      if (rebuildSizeCount[s] > rebuildBodyCount) {
        rebuildBodyCount = rebuildSizeCount[s];
        bodySizeForTitle = parseFloat(s);
      }
    }

    // 重建前过滤过期字符：
    // 1. 归属已移除 canvas 的字符（翻页/换章时旧 canvas 被移除，其字符残留会导致文本污染）
    // 2. 上一轮重建插入的虚拟标题句号（本轮会按新布局重新插入）
    // 3. canvasIdx 悬空的字符（超出当前 canvasElements 范围）
    const beforeFilterLen = charIndex.length;
    charIndex = charIndex.filter(function (c) {
      if (c.el && canvasElements.indexOf(c.el) === -1) return false;
      if (c.isVirtual) return false;
      if (c.canvasIdx < 0 || c.canvasIdx >= canvasElements.length) return false;
      return true;
    });
    if (charIndex.length === 0) {
      console.log("[FewType] 重建前过滤后为空，等待下一轮 fillText");
      return;
    }
    if (charIndex.length < beforeFilterLen) {
      console.log("[FewType] 重建前过滤过期字符: " + beforeFilterLen + " -> " + charIndex.length);
    }

    // 排序：
    // - 双栏模式：两个 canvas 左右排列，left 不同，按 left 排序（左栏在前）
    // - 滚动模式：两个 canvas 上下排列，left 相同，按 top 排序（上面的在前）
    // - 同一 canvas 内按 y → x
    // 性能优化：排序前缓存所有 canvas 的 getBoundingClientRect()，避免 comparator 内
    // 每次比较都触发 reflow（N log N 次 reflow 会导致大章节页面卡死）。
    const _canvasRects = canvasElements.map(function (c) {
      try { return c.getBoundingClientRect(); } catch (e) { return null; }
    });
    charIndex.sort(function (a, b) {
      const ra = _canvasRects[a.canvasIdx];
      const rb = _canvasRects[b.canvasIdx];
      const leftA = ra ? ra.left : a.canvasIdx;
      const leftB = rb ? rb.left : b.canvasIdx;
      const topA = ra ? ra.top : 0;
      const topB = rb ? rb.top : 0;
      if (Math.abs(leftA - leftB) > 2) return leftA - leftB;       // 双栏：左右
      if (Math.abs(topA - topB) > 2) return topA - topB;           // 滚动：上下
      if (Math.abs(a.y - b.y) > 3) return a.y - b.y;               // 同行
      return a.x - b.x;
    });

    // 去重：canvas 重绘时每个字可能被 fillText 两次（双缓冲/重绘机制），
    // 同一个 canvasIdx + x + y + ch 只保留一个。叠词的 x 不同不会被误删。
    const seen = {};
    const deduped = [];
    for (let i = 0; i < charIndex.length; i++) {
      const c = charIndex[i];
      const key = c.canvasIdx + "_" + Math.round(c.x) + "_" + Math.round(c.y) + "_" + c.ch;
      if (!seen[key]) {
        seen[key] = true;
        deduped.push(c);
      }
    }
    if (deduped.length < charIndex.length) {
      console.log("[FewType] charIndex 去重: " + charIndex.length + " → " + deduped.length);
    }
    charIndex = deduped;

    // 调试：输出排序后前20个字符
    try {
      const debugSort = charIndex.slice(0, 20).map(function(c, i) {
        const cv = canvasElements[c.canvasIdx];
        let cvTop = -1;
        try { cvTop = Math.round(cv.getBoundingClientRect().top); } catch (e) {}
        return { i: i, canvasIdx: c.canvasIdx, cvTop: cvTop, y: Math.round(c.y), x: Math.round(c.x), ch: c.ch };
      });
      window.postMessage({ source: SOURCE, type: "debug-info", result: { sortDebug: debugSort } }, "*");
    } catch (e) {}

    // 按行检测标题：如果某行字体明显比下一行大（>20%），视为标题行，在末尾插入句号，
    // 使 chunking 在标题后分段，朗读时有停顿。在 charIndex 里插入虚拟字符保持索引一致。
    const charIndexWithTitles = [];
    let lineStart = 0;
    while (lineStart < charIndex.length) {
      const lineY = charIndex[lineStart].y;
      const lineSize = charIndex[lineStart].size;
      let lineEnd = lineStart;
      while (lineEnd < charIndex.length && Math.abs(charIndex[lineEnd].y - lineY) < 3) {
        lineEnd++;
      }
      // 把当前行的字符加入
      for (let i = lineStart; i < lineEnd; i++) {
        charIndexWithTitles.push(charIndex[i]);
      }
      // 检测是否是标题行：用正文字号作为基准（比正文大20%以上），
      // 而非只比下一行（多级标题时相邻标题字号接近，会漏判）
      if (lineEnd < charIndex.length) {
        const nextSize = charIndex[lineEnd].size;
        const isTitleByNext = lineSize > nextSize * 1.2;
        const isTitleByBody = bodySizeForTitle > 0 && lineSize > bodySizeForTitle * 1.2;
        if (isTitleByNext || isTitleByBody) {
          // 插入一个 \u0001 占位符（正文绝对不会出现的不可见控制字符），
          // 构建 fullText 时替换为"。\uE000"：句号负责断句，\uE000 是私有区标记，
          // background chunking 后会把它转成 pause chunk（静音占位），offscreen 播放时等待对应时长。
          // isVirtual 标记确保每轮重建前被过滤，不会残留污染。
          charIndexWithTitles.push({
            ch: "\u0001",
            x: charIndex[lineEnd - 1].x,
            y: lineY,
            size: lineSize,
            canvasIdx: -1,
            scaleX: charIndex[lineStart].scaleX || 2,
            isVirtual: true,
          });
        }
      }
      lineStart = lineEnd;
    }
    charIndex = charIndexWithTitles;

    const canvasText = charIndex.map(function (c) { return c.ch === "\u0001" ? "。\uE000" : c.ch; }).join("");
    fullText = canvasText;
    domTextStart = -1;

    // 滚动模式：canvas 只覆盖前半部分，后半部分是 DOM 渲染的 SPAN.wr_absolute
    // 把 DOM 文本合并进 fullText，使视口定位能搜索到当前视口的文本
    const isScroll = document.documentElement.scrollHeight > document.documentElement.clientHeight + 100;
    if (isScroll) {
      const domSpans = document.querySelectorAll("span.wr_absolute");
      const domText = extractDomText();
      console.log("[FewType] 滚动模式DOM探测: spanCount=" + domSpans.length + " domTextLen=" + (domText ? domText.length : 0) + " retry=" + domRetryCount);
      if (domText && domText.length > 10) {
        domRetryCount = 0; // 重置重试计数
        // 找 DOM 文本前 10 个字在 canvas 文本里的位置（处理重叠）
        let overlapPos = -1;
        for (let len = Math.min(domText.length, 12); len >= 4; len--) {
          const snippet = domText.slice(0, len);
          overlapPos = canvasText.indexOf(snippet);
          if (overlapPos !== -1) break;
        }
        if (overlapPos !== -1) {
          // 有重叠：canvas 文本截断到重叠位置，再拼接 DOM 文本
          fullText = canvasText.slice(0, overlapPos) + domText;
          domTextStart = overlapPos;
          console.log("[FewType] 合并DOM文本: canvasLen=" + canvasText.length +
            " domLen=" + domText.length + " overlapPos=" + overlapPos +
            " mergedLen=" + fullText.length);
        } else {
          // 无重叠：直接拼接（可能有少量重复或遗漏）
          fullText = canvasText + domText;
          domTextStart = canvasText.length;
          console.log("[FewType] 拼接DOM文本(无重叠): canvasLen=" + canvasText.length +
            " domLen=" + domText.length + " mergedLen=" + fullText.length);
        }
      } else if (domRetryCount < 3) {
        // DOM 还没渲染，延迟 500ms 重试（翻页后 canvas 重绘快，DOM 渲染有延迟）
        domRetryCount++;
        console.log("[FewType] DOM未渲染，500ms后重试 (第" + domRetryCount + "次)");
        setTimeout(rebuildTextAndNotify, 500);
        return;
      } else {
        domRetryCount = 0; // 重试耗尽，重置
        console.log("[FewType] DOM重试耗尽，使用纯canvas文本");
      }
    }

    if (fullText.length > 0) {
      const firstChar = charIndex.length > 0 ? charIndex[0] : null;
      console.log("[FewType] 重建正文 len=" + fullText.length +
        " 前20字=" + fullText.slice(0, 20) +
        " canvas数=" + canvasElements.length +
        (domTextStart >= 0 ? " domTextStart=" + domTextStart : "") +
        (firstChar ? " firstTransform{tx:" + (firstChar.tx || 0) + ",ty:" + (firstChar.ty || 0) + "}" : ""));
      window.postMessage({
        source: SOURCE,
        type: "chapter-updated",
        url: location.href,
        text: fullText,
        charCount: charIndex.length,
        firstTransform: firstChar ? { tx: firstChar.tx || 0, ty: firstChar.ty || 0 } : null,
      }, "*");
    }

    // 滚动模式下如果当前是纯 canvas（domTextStart=-1），定期检查 DOM 是否渲染出来。
    // 翻页后 canvas 先重绘，DOM span 可能延迟渲染；DOM 渲染后重新合并，高亮就能切到 DOM 模式。
    if (isScroll && domTextStart === -1 && !domRenderMonitor) {
      let monitorCount = 0;
      domRenderMonitor = setInterval(function () {
        monitorCount++;
        const spans = document.querySelectorAll("span.wr_absolute");
        if (spans.length > 0) {
          console.log("[FewType] DOM渲染监听: 检测到" + spans.length + "个span，重新合并文本");
          clearInterval(domRenderMonitor);
          domRenderMonitor = null;
          rebuildTextAndNotify();
        } else if (monitorCount >= 10) {
          // 最多检查10次（约10秒），超时停止
          console.log("[FewType] DOM渲染监听: 超时未检测到DOM，停止监听");
          clearInterval(domRenderMonitor);
          domRenderMonitor = null;
        }
      }, 1000);
    }
  }

  // ---------- 覆盖层 ----------
  function ensureOverlayFor(canvas) {
    const idx = canvasElements.indexOf(canvas);
    if (overlays[idx]) return overlays[idx];
    const parent = canvas.parentElement;
    if (!parent) return null;
    if (getComputedStyle(parent).position === "static") {
      parent.style.position = "relative";
    }
    const overlay = document.createElement("div");
    overlay.className = "vox-weread-overlay";
    overlay.style.cssText = "position:absolute;pointer-events:none;z-index:2147483647;overflow:visible;";
    function syncSize() {
      overlay.style.left = canvas.offsetLeft + "px";
      overlay.style.top = canvas.offsetTop + "px";
      overlay.style.width = canvas.offsetWidth + "px";
      overlay.style.height = canvas.offsetHeight + "px";
    }
    syncSize();
    parent.appendChild(overlay);
    overlays[idx] = { el: overlay, syncSize: syncSize };
    return overlays[idx];
  }

  function clearHighlight() {
    overlays.forEach(function (o) { if (o && o.el) o.el.innerHTML = ""; });
    clearDomHighlight();
  }

  // ---------- 高亮 ----------
  function highlightChunk(text) {
    if (!text || charIndex.length === 0) {
      window.postMessage({ source: SOURCE, type: "highlight-miss" }, "*");
      return;
    }

    clearHighlight();

    // chunk 是从 fullText 切出来的，直接 indexOf 即可
    let pos = fullText.indexOf(text);
    if (pos === -1) {
      pos = fullText.indexOf(text.trim());
    }
    if (pos === -1) {
      window.postMessage({ source: SOURCE, type: "highlight-miss" }, "*");
      return;
    }

    const endPos = Math.min(pos + text.length, fullText.length);

    // DOM 部分高亮：如果 chunk 完全在 DOM 文本区域（pos >= domTextStart），
    // 直接给对应的 SPAN.wr_absolute 加背景色
    if (domTextStart >= 0 && pos >= domTextStart) {
      const sortedSpans = getSortedDomSpans();
      if (sortedSpans.length === 0) {
        window.postMessage({ source: SOURCE, type: "highlight-miss" }, "*");
        return;
      }
      // 字符偏移 → span 索引（有些 span 包含多个字，不能直接用字符偏移当 span 索引）
      const domCharOffset = pos - domTextStart;
      const domCharEnd = endPos - domTextStart;
      let startSpanIdx = -1;
      let endSpanIdx = -1;
      let charCount = 0;
      for (let i = 0; i < sortedSpans.length; i++) {
        const spanLen = sortedSpans[i].text.length;
        if (startSpanIdx === -1 && charCount + spanLen > domCharOffset) {
          startSpanIdx = i;
        }
        if (charCount + spanLen >= domCharEnd) {
          endSpanIdx = i + 1;
          break;
        }
        charCount += spanLen;
      }
      if (startSpanIdx === -1) {
        window.postMessage({ source: SOURCE, type: "highlight-miss" }, "*");
        return;
      }
      if (endSpanIdx === -1) endSpanIdx = sortedSpans.length;
      const highlighted = [];
      for (let i = startSpanIdx; i < endSpanIdx && i < sortedSpans.length; i++) {
        sortedSpans[i].el.style.backgroundColor = "rgba(255,215,0,0.2)";
        sortedSpans[i].el.style.borderRadius = "3px";
        highlighted.push(sortedSpans[i].el);
      }
      // 自动滚动到高亮位置
      if (highlighted.length > 0 && isScrollMode()) {
        const first = highlighted[0].getBoundingClientRect();
        const viewportH = window.innerHeight;
        if (first.top < 80 || first.top > viewportH - 40) {
          const scrollDelta = first.top - 80;
          if (Math.abs(scrollDelta) > 5) {
            window.scrollBy(0, scrollDelta);
          }
        }
      }
      window.postMessage({ source: SOURCE, type: "highlight-hit" }, "*");
      return;
    }

    // Canvas 部分高亮：用 charIndex + overlay
    // 保护：charIndex 只覆盖 canvas 部分，如果 chunk 超出 charIndex 范围，只高亮 canvas 部分
    if (pos >= charIndex.length) {
      window.postMessage({ source: SOURCE, type: "highlight-miss" }, "*");
      return;
    }

    const canvasEndPos = Math.min(endPos, charIndex.length);
    const chars = charIndex.slice(pos, canvasEndPos);
    if (chars.length === 0) {
      window.postMessage({ source: SOURCE, type: "highlight-miss" }, "*");
      return;
    }

    // 滚动模式：两个 canvas 共用全局坐标系，所有高亮统一画在第一个 canvas 的 overlay 上
    // 双栏模式：各自 canvas 独立坐标系，画在各自 overlay 上
    const scrollMode = isScrollMode();
    const baseOv = scrollMode ? overlays[0] : null;
    const baseCanvas = scrollMode ? canvasElements[0] : null;

    const createdRects = [];
    let debugAnchorDrawn = false;
    // 逐字画矩形（和 DOM 逐字高亮视觉一致），不按行合并
    chars.forEach(function (c) {
      // 跳过虚拟句号（标题后插入的标点，没有实际渲染位置）
      if (c.isVirtual || c.canvasIdx === -1) return;
      const ov = scrollMode ? baseOv : overlays[c.canvasIdx];
      const canvas = scrollMode ? baseCanvas : canvasElements[c.canvasIdx];
      if (!ov || !canvas) return;
      ov.syncSize();
      const ratio = logicalToCss(canvas, c.scaleX);
      const sx = c.scaleX || 2;
      const sy = c.scaleY || 2;
      // 应用 ctx.translate() 的平移：tx/ty 是 getTransform() 的 e/f 分量（设备像素级别），
      // 除以 scale 得到逻辑坐标平移。不同文章/章节的 translate 不同，这是换文章后高亮错位的根因。
      const logicalX = c.x + (c.tx || 0) / sx;
      const logicalY = c.y + (c.ty || 0) / sy;
      const cssX = logicalX * ratio;
      // 用离屏 canvas measureText 测字符实际宽度（西语/英语字符不等宽，统一用 size 会重叠）
      // font 为空或测量失败时回退到 size*ratio（中文等宽场景）
      const measuredW = c.font ? measureCharWidth(c.font, c.ch) : 0;
      const charW = measuredW > 0 ? measuredW : c.size;
      const cssW = Math.max(charW * ratio, 2);
      // 高亮垂直偏移：textBaseline=middle，文字顶部 = 中点 - size*0.5
      const cssY = (logicalY - c.size * 0.5) * ratio;
      const cssH = c.size * ratio;
      const rect = document.createElement("div");
      rect.style.cssText =
        "position:absolute;left:" + cssX + "px;top:" + cssY + "px;width:" + cssW +
        "px;height:" + cssH + "px;background:rgba(255,215,0,0.2);border-radius:2px;";
      ov.el.appendChild(rect);
      createdRects.push(rect);

      // === 诊断锚：对第一个高亮字符输出完整坐标链路 + 画红色参考框 ===
      if (!debugAnchorDrawn) {
        debugAnchorDrawn = true;
        try {
          const cr = canvas.getBoundingClientRect();
          const or = ov.el.getBoundingClientRect();
          const rectR = rect.getBoundingClientRect();
          console.log("[FewType][高亮诊断锚] char=" + JSON.stringify(c.ch) +
            " fillText{x:" + Math.round(c.x) + ",y:" + Math.round(c.y) + ",size:" + c.size +
            "} scale{sx:" + sx + ",sy:" + sy + "} translate{tx:" + (c.tx||0) + ",ty:" + (c.ty||0) +
            "} logical{x:" + Math.round(logicalX) + ",y:" + Math.round(logicalY) +
            "} ratio:" + ratio.toFixed(3) +
            " css{x:" + Math.round(cssX) + ",y:" + Math.round(cssY) + ",w:" + Math.round(cssW) + ",h:" + Math.round(cssH) + "}" +
            " canvasRect{top:" + Math.round(cr.top) + ",left:" + Math.round(cr.left) + ",w:" + Math.round(cr.width) + ",h:" + Math.round(cr.height) + "}" +
            " overlayRect{top:" + Math.round(or.top) + ",left:" + Math.round(or.left) + ",w:" + Math.round(or.width) + ",h:" + Math.round(or.height) + "}" +
            " rectActual{top:" + Math.round(rectR.top) + ",left:" + Math.round(rectR.left) + ",w:" + Math.round(rectR.width) + ",h:" + Math.round(rectR.height) + "}" +
            " scrollY:" + Math.round(window.scrollY || 0));
        } catch (e) {
          console.log("[FewType][高亮诊断锚] 异常:", e);
        }
      }
    });

    // 滚动模式：高亮不在视口内时，自动滚动到高亮位置
    // 无论高亮在视口上方还是下方，都滚到高亮第一行可见的位置
    if (createdRects.length > 0 && isScrollMode()) {
      // 按 top 排序，取最上面的高亮矩形（Object.keys 遍历顺序不保证按 y 排序）
      createdRects.sort(function (a, b) {
        return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
      });
      const firstRect = createdRects[0];
      const fr = firstRect.getBoundingClientRect();
      const viewportH = window.innerHeight;
      if (fr.top < 80 || fr.bottom > viewportH - 40) {
        const scrollDelta = fr.top - 80;
        if (Math.abs(scrollDelta) > 5) {
          window.scrollBy(0, scrollDelta);
        }
      }

      // 方案1+3：滚动到目标位置后，假滚动触发微信读书的 DOM 实例化，
      // 等 DOM 渲染出来后切换到 DOM 逐字高亮（和起读时一致）
      const chunkText = text;
      setTimeout(function () {
        // 假滚动 1px 触发视口驱动的 DOM 渲染
        window.scrollBy(0, 1);
        window.scrollBy(0, -1);
        setTimeout(function () {
          const spans = document.querySelectorAll("span.wr_absolute");
          window.postMessage({
            source: SOURCE, type: "debug-info",
            result: { domHydrationCheck: { spanCount: spans.length, domTextStart: domTextStart } },
          }, "*");
          if (spans.length > 0 && domTextStart === -1) {
            // 重新合并 DOM 文本
            const domText = extractDomText();
            if (domText && domText.length > 10) {
              const canvasText = charIndex.map(function (c) { return c.ch; }).join("");
              let overlapPos = -1;
              for (let len = Math.min(domText.length, 12); len >= 4; len--) {
                overlapPos = canvasText.indexOf(domText.slice(0, len));
                if (overlapPos !== -1) break;
              }
              if (overlapPos !== -1) {
                fullText = canvasText.slice(0, overlapPos) + domText;
                domTextStart = overlapPos;
              } else {
                fullText = canvasText + domText;
                domTextStart = canvasText.length;
              }
              window.postMessage({
                source: SOURCE, type: "debug-info",
                result: { domHydration: "switched-to-dom", domTextStart: domTextStart, fullTextLen: fullText.length },
              }, "*");
              // 清除 canvas overlay，重新用 DOM 高亮
              overlays.forEach(function (o) { if (o && o.el) o.el.innerHTML = ""; });
              highlightChunk(chunkText);
              return;
            }
          }
        }, 300);
      }, 200);
    }

    window.postMessage({ source: SOURCE, type: "highlight-hit" }, "*");
  }

  // 判断是否为滚动模式（页面有纵向滚动条）
  function isScrollMode() {
    return document.documentElement.scrollHeight > document.documentElement.clientHeight + 100;
  }

  // ---------- 起始位置定位 ----------
  // 与 Play Books / Koodo 对齐：
  //   1. 用户拖拽选中了文字 → 从选中起点字符开始读
  //   2. 没有选中 → 从视口内第一个可见字符往后找第一个大标点，从标点后开始
  //
  // 微信读书是 canvas 渲染，window.getSelection() 拿不到 canvas 上的选区，
  // 所以用鼠标坐标 + 我们自己采集的 charIndex 坐标来反查选中起点。
  const MAJOR_PUNCT_RE = /[。！？，、；：…—～,.!?;:…—\-）\)\]\}】」』》〉"'"]/;

  let mouseDownPos = null;
  let selectionStartCharIdx = null;

  // 用视口坐标在 charIndex 或 DOM span 里找对应字符的索引
  // 重要：返回的索引必须和 fullText 的索引一致
  // 滚动模式下，一次调用 getSortedDomSpans()，用同一批 span 既拼接 domText 又找鼠标位置
  function findCharAtPosition(clientX, clientY) {
    const scrollMode = isScrollMode();

    // 滚动模式下优先在 DOM span 里找
    const sm = isScrollMode();
    const debugMsg = { type: "findChar-debug", scrollMode: sm, mouseX: clientX, mouseY: clientY };
    if (sm) {
      // 一次获取排序后的 span，用同一批数据既合并文本又找位置
      const sortedSpans = getSortedDomSpans();
      debugMsg.spanCount = sortedSpans.length;
      if (sortedSpans.length > 0) {
        // 用这批 span 拼接 domText 并合并，同时记录每个 span 的起始字符位置
        const domTextParts = [];
        const spanCharOffsets = []; // spanCharOffsets[i] = 第 i 个 span 在 domText 中的起始字符位置
        let charPos = 0;
        for (let i = 0; i < sortedSpans.length; i++) {
          spanCharOffsets.push(charPos);
          domTextParts.push(sortedSpans[i].text);
          charPos += sortedSpans[i].text.length;
        }
        const domText = domTextParts.join("");
        const canvasText = charIndex.map(function (c) { return c.ch; }).join("");
        let overlapPos = -1;
        for (let len = Math.min(domText.length, 12); len >= 4; len--) {
          const snippet = domText.slice(0, len);
          overlapPos = canvasText.indexOf(snippet);
          if (overlapPos !== -1) break;
        }
        if (overlapPos !== -1) {
          fullText = canvasText.slice(0, overlapPos) + domText;
          domTextStart = overlapPos;
        } else {
          fullText = canvasText + domText;
          domTextStart = canvasText.length;
        }
        window.postMessage({
          source: SOURCE, type: "chapter-updated",
          url: location.href, text: fullText, charCount: charIndex.length,
        }, "*");
        debugMsg.overlapPos = overlapPos;
        debugMsg.domTextStart = domTextStart;
        debugMsg.fullTextLen = fullText.length;
        debugMsg.domTextLen = domText.length;

        // 用缓存的 s.top/s.left 匹配
        let matchedSpanIdx = -1;
        for (let i = 0; i < sortedSpans.length; i++) {
          const s = sortedSpans[i];
          if (clientX >= s.left - 6 && clientX <= s.left + 30 &&
              clientY >= s.top - 6 && clientY <= s.top + 30) {
            matchedSpanIdx = i;
            break;
          }
        }
        if (matchedSpanIdx !== -1) {
          // 用字符位置而不是 span 索引（有些 span 包含多个字）
          const matchedIdx = domTextStart + spanCharOffsets[matchedSpanIdx];
          const matchedSpan = sortedSpans[matchedSpanIdx];
          debugMsg.result = "dom-hit";
          debugMsg.charIdx = matchedIdx;
          debugMsg.spanIndex = matchedSpanIdx;
          debugMsg.spanCharOffset = spanCharOffsets[matchedSpanIdx];
          debugMsg.spanText = matchedSpan.text;
          debugMsg.fullTextChar = fullText[matchedIdx] || "?";
          debugMsg.spanTop = matchedSpan.top;
          debugMsg.spanLeft = matchedSpan.left;
          window.postMessage({ source: SOURCE, type: "debug-info", result: debugMsg }, "*");
          return matchedIdx;
        } else {
          debugMsg.result = "dom-miss";
          // 找最近的 span
          let minDist = Infinity;
          let nearest = null;
          for (let i = 0; i < sortedSpans.length; i++) {
            const s = sortedSpans[i];
            const d = Math.abs(s.top - clientY) + Math.abs(s.left - clientX);
            if (d < minDist) { minDist = d; nearest = s; }
          }
          if (nearest) {
            debugMsg.nearestText = nearest.text;
            debugMsg.nearestTop = nearest.top;
            debugMsg.nearestLeft = nearest.left;
          }
        }
      }
    }
    // DOM 未命中，走 canvas 分支前也发调试
    if (debugMsg.result !== "dom-hit") {
      window.postMessage({ source: SOURCE, type: "debug-info", result: debugMsg }, "*");
    }

    // DOM 里没找到，在 canvas charIndex 里找
    // 注意：只有 idx < domTextStart 时才有效（重叠区域 charIndex 和 fullText 索引不一致）
    let baseR = null;
    let baseRatio = 1;
    if (sm && canvasElements.length > 0) {
      try {
        baseR = canvasElements[0].getBoundingClientRect();
        baseRatio = logicalToCss(canvasElements[0], charIndex.length > 0 ? charIndex[0].scaleX : 2);
      } catch (e) {}
    }
    for (let i = 0; i < charIndex.length; i++) {
      const c = charIndex[i];
      if (domTextStart >= 0 && i >= domTextStart) continue;
      let r, ratio;
      if (scrollMode && baseR) {
        r = baseR;
        ratio = baseRatio;
      } else {
        const canvas = canvasElements[c.canvasIdx];
        if (!canvas) continue;
        r = canvas.getBoundingClientRect();
        ratio = logicalToCss(canvas, c.scaleX);
      }
      if (r.width === 0 || r.height === 0) continue;
      const charLeft = r.left + (c.x + (c.tx || 0)) * ratio;
      const charRight = charLeft + c.size * ratio;
      const charTop = r.top + (c.y + (c.ty || 0) - c.size / 2) * ratio;
      const charBottom = charTop + c.size * ratio;
      if (clientX >= charLeft - 2 && clientX <= charRight + 2 &&
          clientY >= charTop - 2 && clientY <= charBottom + 2) {
        console.log("[FewType] findCharAtPosition canvas命中: mouse=(" + clientX + "," + clientY +
          ") idx=" + i + " char=" + c.ch);
        return i;
      }
    }
    return -1;
  }

  // 监听鼠标拖拽：mousedown 记录起点，mouseup 时如果移动距离超过阈值，
  // 认为是拖拽选中，用起点坐标定位字符。
  document.addEventListener("mousedown", function (e) {
    mouseDownPos = { x: e.clientX, y: e.clientY };
  });

  document.addEventListener("mouseup", function (e) {
    if (!mouseDownPos) return;
    const dx = e.clientX - mouseDownPos.x;
    const dy = e.clientY - mouseDownPos.y;
    if (Math.abs(dx) < 5 && Math.abs(dy) < 5) {
      mouseDownPos = null;
      return; // 纯点击，不是拖拽选中
    }
    const idx = findCharAtPosition(mouseDownPos.x, mouseDownPos.y);
    // 总是更新 selectionStartCharIdx：找到设索引，没找到设 null，不能保留上次的值
    if (idx !== -1) {
      selectionStartCharIdx = idx;
      const ch = fullText[idx] || "?";
      const debugInfo = {
        type: "selection-start",
        mouseX: mouseDownPos.x,
        mouseY: mouseDownPos.y,
        charIdx: idx,
        char: ch,
        inCanvas: idx < charIndex.length,
        inDom: domTextStart >= 0 && idx >= domTextStart,
        charIndexLen: charIndex.length,
        domTextStart: domTextStart,
        fullTextLen: fullText.length,
      };
      // 如果是 DOM 部分，遍历 span 累加字符长度找到对应 span（不能直接用 idx-domTextStart 当 span 索引）
      if (domTextStart >= 0 && idx >= domTextStart) {
        const sorted = getSortedDomSpans();
        const targetCharOffset = idx - domTextStart;
        let charCount = 0;
        for (let si = 0; si < sorted.length; si++) {
          const spanLen = sorted[si].text.length;
          if (charCount + spanLen > targetCharOffset) {
            debugInfo.domSpanText = sorted[si].text;
            debugInfo.domSpanTop = sorted[si].top;
            debugInfo.domSpanLeft = sorted[si].left;
            debugInfo.domSpanIndex = si;
            break;
          }
          charCount += spanLen;
        }
      }
      console.log("[FewType] 划选起点: " + JSON.stringify(debugInfo));
      window.postMessage({ source: SOURCE, type: "debug-info", result: debugInfo }, "*");
    } else {
      selectionStartCharIdx = null;
      console.log("[FewType] 划选起点未找到: mouse=(" + mouseDownPos.x + "," + mouseDownPos.y + ") charIndexLen=" + charIndex.length);
      window.postMessage({
        source: SOURCE, type: "debug-info",
        result: { type: "selection-start", mouseX: mouseDownPos.x, mouseY: mouseDownPos.y, charIdx: -1, reason: "not-found", charIndexLen: charIndex.length, domTextStart: domTextStart },
      }, "*");
    }
    mouseDownPos = null;
  });

  // 尝试 window.getSelection()（canvas 渲染通常拿不到，但目录等 DOM 文本可能命中）
  function getSelectionStartOffset() {
    try {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return null;
      const selText = sel.toString().replace(/\s+/g, " ").trim();
      if (!selText || selText.length < 2) return null;
      const pos = fullText.indexOf(selText);
      if (pos !== -1) return pos;
      const short = selText.slice(0, 6);
      const pos2 = fullText.indexOf(short);
      return pos2 !== -1 ? pos2 : null;
    } catch (e) {
      return null;
    }
  }

  // 合并 DOM 文本进 fullText（滚动模式下 canvas 只覆盖前半部分，后半部分是 DOM 渲染）
  // 合并后通过 postMessage 通知 background 更新 fullText
  // force=true 时总是更新（即使长度变短），确保 domText 和当前 span 列表一致
  function mergeDomText(force) {
    try {
      const domText = extractDomText();
      if (!domText || domText.length < 10) return false;
      const canvasText = charIndex.map(function (c) { return c.ch; }).join("");
      // 找 DOM 文本前 12 个字在 canvas 文本里的位置（处理重叠）
      let overlapPos = -1;
      for (let len = Math.min(domText.length, 12); len >= 4; len--) {
        const snippet = domText.slice(0, len);
        overlapPos = canvasText.indexOf(snippet);
        if (overlapPos !== -1) break;
      }
      let merged;
      if (overlapPos !== -1) {
        merged = canvasText.slice(0, overlapPos) + domText;
        domTextStart = overlapPos;
      } else {
        merged = canvasText + domText;
        domTextStart = canvasText.length;
      }
      // force=true 或合并后更长时更新
      if (force || merged.length > fullText.length) {
        fullText = merged;
        console.log("[FewType] 合并DOM文本: canvasLen=" + canvasText.length +
          " domLen=" + domText.length + " mergedLen=" + fullText.length +
          " overlapPos=" + overlapPos + " force=" + !!force);
        window.postMessage({
          source: SOURCE,
          type: "chapter-updated",
          url: location.href,
          text: fullText,
          charCount: charIndex.length,
        }, "*");
        return true;
      }
      return false;
    } catch (e) {
      console.log("[FewType] 合并DOM文本异常: " + e);
      return false;
    }
  }

  // 用 DOM（SPAN.wr_absolute）做视口定位：找视口内第一行文本，在 fullText 里搜索位置
  // 滚动模式下 canvas 只覆盖前半部分，后半部分是 DOM 渲染的绝对定位 span
  function findViewportStartFromDom(skipPunct) {
    const debug = { step: "start", spansCount: 0, inViewCount: 0, firstLine: "", searchPos: -1, fullTextLen: 0, skipPunct: !!skipPunct };
    try {
      const spans = document.querySelectorAll("span.wr_absolute");
      debug.spansCount = spans.length;
      if (spans.length === 0) { debug.step = "no-spans"; postDomDebug(debug); return null; }
      const viewportH = window.innerHeight;
      const viewportW = window.innerWidth;
      const inView = [];
      for (let i = 0; i < spans.length; i++) {
        const el = spans[i];
        const txt = (el.textContent || "").trim();
        if (!txt) continue;
        const r = el.getBoundingClientRect();
        if (r.top < -10 || r.top > viewportH - 10) continue;
        if (r.left < -10 || r.left > viewportW + 10) continue;
        inView.push({ text: txt, top: Math.round(r.top), left: Math.round(r.left) });
      }
      debug.inViewCount = inView.length;
      if (inView.length === 0) { debug.step = "no-inview"; postDomDebug(debug); return null; }
      // 按 top 分组（差值<15 视为同一行），每组内按 left 排序拼文本
      inView.sort(function (a, b) { return a.top - b.top || a.left - b.left; });
      const rows = [];
      let currentRow = null;
      for (let i = 0; i < inView.length; i++) {
        const s = inView[i];
        if (!currentRow || Math.abs(s.top - currentRow.top) > 15) {
          if (currentRow) rows.push(currentRow);
          currentRow = { top: s.top, chars: [] };
        }
        currentRow.chars.push(s.text);
      }
      if (currentRow) rows.push(currentRow);
      debug.rowsCount = rows.length;
      if (rows.length === 0) { debug.step = "no-rows"; postDomDebug(debug); return null; }
      // 取第一行文本
      const firstLineText = rows[0].chars.join("");
      debug.firstLine = firstLineText.slice(0, 50);
      debug.firstLineTop = rows[0].top;
      if (!firstLineText) { debug.step = "empty-firstline"; postDomDebug(debug); return null; }
      // 在 fullText 里搜索，用前 10 个字逐步缩短
      debug.fullTextLen = fullText.length;
      let searchPos = -1;
      let usedLen = 0;
      for (let len = Math.min(firstLineText.length, 10); len >= 3; len--) {
        const snippet = firstLineText.slice(0, len);
        searchPos = fullText.indexOf(snippet);
        if (searchPos !== -1) { usedLen = len; break; }
      }
      debug.searchPos = searchPos;
      debug.usedLen = usedLen;
      if (searchPos === -1) {
        debug.step = "not-found-in-fulltext";
        postDomDebug(debug);
        return null;
      }
      // skipPunct=true 时（翻页后）直接从视口第一行开始，不往后找标点
      if (skipPunct) {
        debug.step = "ok-skip-punct";
        debug.charOffset = searchPos;
        postDomDebug(debug);
        return searchPos;
      }
      // 从搜索位置往后找第一个大标点
      for (let i = searchPos; i < fullText.length && i < searchPos + 200; i++) {
        if (MAJOR_PUNCT_RE.test(fullText[i])) {
          debug.step = "ok";
          debug.punctAt = i;
          debug.charOffset = i + 1;
          postDomDebug(debug);
          return i + 1;
        }
      }
      debug.step = "ok-no-punct";
      debug.charOffset = searchPos;
      postDomDebug(debug);
      return searchPos;
    } catch (e) {
      debug.step = "exception";
      debug.error = String(e);
      postDomDebug(debug);
      return null;
    }
  }

  function postDomDebug(debug) {
    try {
      window.postMessage({ source: SOURCE, type: "debug-info", result: { domLocate: debug } }, "*");
    } catch (e) {}
  }

  // 用 charIndex 坐标找视口内第一个可见字符，再往后找第一个大标点
  function findViewportSentenceStart(skipPunct) {
    if (charIndex.length === 0) return 0;

    // 滚动模式下优先用 DOM 定位（canvas 只覆盖前半部分，后半部分是 DOM 渲染）
    const scrollMode = isScrollMode();
    let domDebug = { tried: false, result: null, reason: "" };
    if (scrollMode) {
      // 先合并最新的 DOM 文本进 fullText（canvas 滚出视口后不会自动重建）
      mergeDomText();
      domDebug.tried = true;
      const domOffset = findViewportStartFromDom(skipPunct);
      domDebug.result = domOffset;
      if (domOffset !== null && domOffset > 0) {
        // DOM 定位成功，通过诊断日志输出
        window.postMessage({
          source: SOURCE, type: "debug-info",
          result: { result: "dom-ok", scrollMode: true, domOffset: domOffset, charIndexLen: charIndex.length, fullTextLen: fullText.length },
        }, "*");
        return domOffset;
      }
      domDebug.reason = domOffset === null ? "dom-returned-null" : "dom-returned-zero";
    }

    const scrollY = window.scrollY || window.pageYOffset || 0;
    const viewportH = window.innerHeight;
    const viewportW = window.innerWidth;

    // 两个 canvas 共用同一个全局坐标系（fillText 的 y 是相对于第一个 canvas 顶部的）
    // 统一用第一个 canvas 的位置作为基准，不能用各自 canvas 的位置（否则 canvas[1] 会偏下约 4000px）
    let baseDocTop = 0;
    let baseDocLeft = 0;
    let baseRatio = 1;
    try {
      if (canvasElements.length > 0) {
        const r0 = canvasElements[0].getBoundingClientRect();
        baseDocTop = r0.top + scrollY;
        baseDocLeft = r0.left + (window.scrollX || 0);
        baseRatio = logicalToCss(canvasElements[0], charIndex.length > 0 ? charIndex[0].scaleX : 2);
      }
    } catch (e) {}

    let firstVisible = -1;
    let checked = 0;
    // 智能章节标题检测：用正文字号作为基准，而非只比第一行vs第二行。
    // 先统计前200个字符的字号分布，找到出现频率最高的正文字号；
    // 任何比正文字号大20%以上的行都是标题行（支持多级标题，如"三十而立"+"一"）。
    const sizeCount = {};
    for (let i = 0; i < Math.min(200, charIndex.length); i++) {
      const c = charIndex[i];
      if (c.isVirtual || c.canvasIdx === -1) continue;
      const s = Math.round(c.size * 10) / 10;
      sizeCount[s] = (sizeCount[s] || 0) + 1;
    }
    let bodySize = 0;
    let bodySizeCount = 0;
    for (const s in sizeCount) {
      if (sizeCount[s] > bodySizeCount) {
        bodySizeCount = sizeCount[s];
        bodySize = parseFloat(s);
      }
    }
    let firstLineY = null;
    let firstLineSize = 0;
    let skipFirstLine = false;
    for (let i = 0; i < Math.min(100, charIndex.length); i++) {
      const c = charIndex[i];
      if (c.isVirtual || c.canvasIdx === -1) continue;
      if (firstLineY === null) {
        firstLineY = c.y;
        firstLineSize = c.size;
        if (bodySize > 0 && firstLineSize > bodySize * 1.2) skipFirstLine = true;
        break;
      }
    }

    let debugCoords = [];
    for (let i = 0; i < charIndex.length; i++) {
      const c = charIndex[i];
      // 跳过虚拟句号
      if (c.isVirtual || c.canvasIdx === -1) continue;
      checked++;
      const cv = canvasElements[c.canvasIdx];
      let cvDocTop = baseDocTop;
      let cvDocLeft = baseDocLeft;
      let cvRatio = baseRatio;
      if (cv) {
        try {
          const cr = cv.getBoundingClientRect();
          cvDocTop = cr.top + scrollY;
          cvDocLeft = cr.left + (window.scrollX || 0);
          cvRatio = logicalToCss(cv, c.scaleX);
        } catch (e) {}
      }
      const charAbsTop = cvDocTop + (c.y - c.size / 2) * cvRatio;
      const charAbsLeft = cvDocLeft + c.x * cvRatio;

      if (i < 15) {
        debugCoords.push({ i: i, ch: c.ch, canvasIdx: c.canvasIdx, y: Math.round(c.y), size: c.size, absTop: Math.round(charAbsTop), isTitle: skipFirstLine && Math.abs(c.y - firstLineY) < 3 });
      }

      // 章节标题行不跳过：检测到 heading 时从标题首字开始读（划选逻辑），标题后有 \u0001 占位符产生停顿

      if (charAbsTop >= scrollY - 5 && charAbsTop < scrollY + viewportH &&
          charAbsLeft >= -5 && charAbsLeft < viewportW + 5) {
        firstVisible = i;
        break;
      }
    }

    // 调试：输出坐标计算详情
    try {
      window.postMessage({
        source: SOURCE, type: "debug-info",
        result: {
          canvasViewportDebug: {
            scrollY: Math.round(scrollY),
            viewportH: viewportH,
            baseDocTop: Math.round(baseDocTop),
            firstLineY: firstLineY !== null ? Math.round(firstLineY) : null,
            firstLineSize: firstLineSize,
            secondLineSize: secondLineSize,
            skipFirstLine: skipFirstLine,
            baseRatio: baseRatio,
            firstVisible: firstVisible,
            checked: checked,
            sampleCoords: debugCoords,
          },
        },
      }, "*");
    } catch (e) {}

    if (firstVisible === -1) {
      // 组织调试信息，通过诊断日志通道输出
      const byCanvas = {};
      charIndex.forEach(function (c) {
        if (!byCanvas[c.canvasIdx]) byCanvas[c.canvasIdx] = { minY: Infinity, maxY: -Infinity, count: 0 };
        byCanvas[c.canvasIdx].minY = Math.min(byCanvas[c.canvasIdx].minY, c.y);
        byCanvas[c.canvasIdx].maxY = Math.max(byCanvas[c.canvasIdx].maxY, c.y);
        byCanvas[c.canvasIdx].count++;
      });
      const canvasInfo = canvasElements.map(function (cv, ci) {
        try {
          const r = cv.getBoundingClientRect();
          const info = byCanvas[ci] || { count: 0 };
          return {
            idx: ci,
            docTop: Math.round(r.top + scrollY),
            docBottom: Math.round(r.bottom + scrollY),
            cssH: Math.round(r.height),
            cssW: Math.round(r.width),
            chars: info.count,
            yRange: info.count ? Math.round(info.minY) + "~" + Math.round(info.maxY) : null,
          };
        } catch (e) {
          return { idx: ci, error: String(e) };
        }
      });
      // 枚举页面上所有 canvas，看是否有未被采集的新 canvas 在视口内
      const allCanvases = [];
      try {
        document.querySelectorAll("canvas").forEach(function (cv, ci) {
          const r = cv.getBoundingClientRect();
          const inViewport = r.bottom > 0 && r.top < viewportH;
          allCanvases.push({
            idx: ci,
            docTop: Math.round(r.top + scrollY),
            docBottom: Math.round(r.bottom + scrollY),
            cssH: Math.round(r.height),
            cssW: Math.round(r.width),
            inViewport: inViewport,
            known: canvasElements.indexOf(cv) !== -1,
          });
        });
      } catch (e) {}

      // 探测视口中心的元素，看文字到底渲染在哪里
      let centerElInfo = null;
      try {
        const cx = Math.round(viewportW / 2);
        const cy = Math.round(viewportH / 2);
        const el = document.elementFromPoint(cx, cy);
        if (el) {
          const chain = [];
          let node = el;
          for (let i = 0; i < 6 && node; i++) {
            chain.push({
              tag: node.tagName,
              cls: node.className || "",
              id: node.id || "",
            });
            node = node.parentElement;
          }
          centerElInfo = {
            tag: el.tagName,
            cls: el.className || "",
            textPreview: (el.textContent || "").slice(0, 100),
            parentChain: chain,
          };
        }
      } catch (e) {
        centerElInfo = { error: String(e) };
      }

      // 探测 .passage-content DOM 结构（滚动模式下正文可能在 DOM 里）
      let passageInfo = null;
      try {
        const passageContent = document.querySelector(".passage-content");
        if (passageContent) {
          const children = passageContent.children;
          const texts = [];
          for (let i = 0; i < Math.min(children.length, 10); i++) {
            const t = children[i].textContent || "";
            texts.push(t.slice(0, 50));
          }
          let fullLen = 0;
          for (let i = 0; i < children.length; i++) {
            fullLen += (children[i].textContent || "").length;
          }
          passageInfo = {
            childCount: children.length,
            totalTextLen: fullLen,
            firstChildrenPreview: texts,
            firstChildClass: children.length > 0 ? (children[0].className || "") : "",
            firstChildTag: children.length > 0 ? children[0].tagName : "",
          };
        }
      } catch (e) {
        passageInfo = { error: String(e) };
      }

      // 全面探测：枚举视口内所有有文本的可见元素
      let visibleTextElements = [];
      try {
        const allElements = document.querySelectorAll("div, span, p");
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        for (let i = 0; i < allElements.length && visibleTextElements.length < 20; i++) {
          const el = allElements[i];
          const txt = (el.textContent || "").trim();
          if (!txt || txt.length < 1) continue;
          // 叶子节点：没有子元素，或子元素都是非元素节点
          const hasElementChild = Array.from(el.children).some(function(c) { return c.textContent.trim(); });
          if (hasElementChild) continue;
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) continue;
          if (r.bottom < 0 || r.top > vh) continue;
          if (r.right < 0 || r.left > vw) continue;
          visibleTextElements.push({
            tag: el.tagName,
            cls: (el.className || "").toString().slice(0, 60),
            text: txt.slice(0, 30),
            top: Math.round(r.top),
            left: Math.round(r.left),
            w: Math.round(r.width),
            h: Math.round(r.height),
          });
        }
      } catch (e) {
        visibleTextElements = [{ error: String(e) }];
      }

      // 专门探测视口内所有 SPAN.wr_absolute（DOM 渲染区域的正文元素）
      let wrAbsoluteSpans = [];
      let wrAbsoluteStats = null;
      try {
        const spans = document.querySelectorAll("span.wr_absolute");
        const vh = window.innerHeight;
        let totalLen = 0;
        let inViewportCount = 0;
        const allSpans = [];
        for (let i = 0; i < spans.length; i++) {
          const el = spans[i];
          const txt = (el.textContent || "").trim();
          totalLen += txt.length;
          const r = el.getBoundingClientRect();
          allSpans.push({ text: txt, top: Math.round(r.top), left: Math.round(r.left) });
          if (r.bottom >= -50 && r.top <= vh + 50) inViewportCount++;
        }
        // 按 top 分组，每组内按 left 排序，输出前 5 行
        allSpans.sort(function(a, b) { return a.top - b.top || a.left - b.left; });
        const rows = [];
        let currentRow = null;
        for (let i = 0; i < allSpans.length && rows.length < 6; i++) {
          const s = allSpans[i];
          if (!currentRow || Math.abs(s.top - currentRow.top) > 15) {
            if (currentRow) rows.push(currentRow);
            currentRow = { top: s.top, chars: [] };
          }
          currentRow.chars.push(s.text);
        }
        if (currentRow) rows.push(currentRow);
        wrAbsoluteStats = {
          totalCount: spans.length,
          totalTextLen: totalLen,
          inViewportCount: inViewportCount,
          firstRowsPreview: rows.map(function(r) { return r.top + ": " + r.chars.join(""); }),
        };
        // 视口内的 span（限制 25 个）
        for (let i = 0; i < allSpans.length && wrAbsoluteSpans.length < 25; i++) {
          const s = allSpans[i];
          if (s.top >= -50 && s.top <= vh + 50) {
            wrAbsoluteSpans.push(s);
          }
        }
      } catch (e) {
        wrAbsoluteStats = { error: String(e) };
      }

      // 探测 #renderTargetContent 的子元素结构
      let renderTargetInfo = null;
      try {
        const rtc = document.getElementById("renderTargetContent");
        if (rtc) {
          const children = [];
          for (let i = 0; i < rtc.children.length; i++) {
            const el = rtc.children[i];
            const r = el.getBoundingClientRect();
            children.push({
              tag: el.tagName,
              cls: (el.className || "").toString().slice(0, 50),
              childCount: el.children.length,
              textLen: (el.textContent || "").length,
              top: Math.round(r.top),
              h: Math.round(r.height),
              inViewport: r.bottom > 0 && r.top < viewportH,
            });
          }
          renderTargetInfo = { childCount: rtc.children.length, children: children };
        }
      } catch (e) {
        renderTargetInfo = { error: String(e) };
      }

      const debugInfo = {
        result: "no-visible-char",
        charIndexLen: charIndex.length,
        checked: checked,
        scrollY: Math.round(scrollY),
        viewportH: viewportH,
        viewportRange: Math.round(scrollY) + "~" + Math.round(scrollY + viewportH),
        scrollMode: scrollMode,
        domDebug: domDebug,
        canvases: canvasInfo,
        allCanvasesOnPage: allCanvases,
        centerElement: centerElInfo,
        passageContent: passageInfo,
        visibleTextElements: visibleTextElements,
        wrAbsoluteSpans: wrAbsoluteSpans,
        wrAbsoluteStats: wrAbsoluteStats,
        renderTargetContent: renderTargetInfo,
      };
      console.log("[FewType] 视口定位未找到可见字符", JSON.stringify(debugInfo));
      window.postMessage({ source: SOURCE, type: "debug-info", result: debugInfo }, "*");
      return 0;
    }

    // 检测到章节标题行（heading）：从标题首字开始读（划选逻辑），不往后找标点
    if (skipFirstLine && firstVisible >= 0) {
      const okInfo = {
        result: "ok-heading",
        firstVisible: firstVisible,
        firstChar: charIndex[firstVisible].ch,
        charOffset: firstVisible,
        heading: true,
      };
      console.log("[FewType] 视口定位成功(章节标题，从标题开始读)", JSON.stringify(okInfo));
      window.postMessage({ source: SOURCE, type: "debug-info", result: okInfo }, "*");
      return firstVisible;
    }

    // skipPunct=true 时（翻页后）直接从视口第一个可见字符开始，不往后找标点
    if (skipPunct) {
      const okInfo = {
        result: "ok-skip-punct",
        firstVisible: firstVisible,
        firstChar: charIndex[firstVisible].ch,
        charOffset: firstVisible,
      };
      console.log("[FewType] 视口定位成功(翻页跳过标点)", JSON.stringify(okInfo));
      window.postMessage({ source: SOURCE, type: "debug-info", result: okInfo }, "*");
      return firstVisible;
    }

    for (let i = firstVisible; i < charIndex.length; i++) {
      if (MAJOR_PUNCT_RE.test(charIndex[i].ch)) {
        const okInfo = {
          result: "ok",
          firstVisible: firstVisible,
          firstChar: charIndex[firstVisible].ch,
          punctAt: i,
          charOffset: i + 1,
        };
        console.log("[FewType] 视口定位成功", JSON.stringify(okInfo));
        window.postMessage({ source: SOURCE, type: "debug-info", result: okInfo }, "*");
        return i + 1;
      }
    }

    const okInfo = {
      result: "ok-no-punct",
      firstVisible: firstVisible,
      firstChar: charIndex[firstVisible].ch,
      charOffset: firstVisible,
    };
    console.log("[FewType] 视口定位成功(无标点)", JSON.stringify(okInfo));
    window.postMessage({ source: SOURCE, type: "debug-info", result: okInfo }, "*");
    return firstVisible;
  }

  function getStartIndex(fromViewportStart) {
    // 优先：鼠标拖拽选中的起点
    if (selectionStartCharIdx !== null && selectionStartCharIdx > 0) {
      const offset = selectionStartCharIdx;
      selectionStartCharIdx = null; // 消费一次，避免下次还从这里开始
      // 如果划选位置在 DOM 部分（超出 charIndex 范围），先合并 DOM 文本确保 fullText 完整
      if (offset >= charIndex.length && isScrollMode()) {
        mergeDomText();
      }
      return { segmentIndex: 0, charOffset: offset };
    }
    // 其次：DOM 选区（canvas 渲染通常拿不到）
    const selOffset = getSelectionStartOffset();
    if (selOffset !== null && selOffset > 0) {
      return { segmentIndex: 0, charOffset: selOffset };
    }
    // 兜底：视口首个标点后（fromViewportStart=true 时跳过标点，直接从视口第一句开始）
    return { segmentIndex: 0, charOffset: findViewportSentenceStart(fromViewportStart) };
  }

  // ---------- 空页指纹 ----------
  // 空页/插图页检测：判断当前视口内是否有可见字符。
  // 只要视口内有一个可见字符就不是空页（保守判断，避免误判正文页）。
  // 标题页（有标题文字但无正文）由 background 侧的 isTitleOrCoverPage 判断处理。
  // 不能用全局 charIndex.length 判断——charIndex 是整章采集的（含视口外正文），
  // 即使当前页是纯插图，全局 charIndex 也可能 >0，会被误判为"有文本"而不翻页。
  function getEmptyPageFingerprint() {
    const scrollY = window.scrollY || document.documentElement.scrollTop || 0;
    const viewportH = window.innerHeight;
    let hasVisibleChar = false;

    // 预缓存 canvas rect，避免循环里重复 getBoundingClientRect
    const canvasRects = canvasElements.map(function (c) {
      try { return c.getBoundingClientRect(); } catch (e) { return null; }
    });

    for (let i = 0; i < charIndex.length; i++) {
      const c = charIndex[i];
      if (c.isVirtual || c.canvasIdx === -1) continue;
      const cr = canvasRects[c.canvasIdx];
      if (!cr) continue;
      try {
        const cv = canvasElements[c.canvasIdx];
        const ratio = cr.width / (cv.width / (c.scaleX || 2));
        const charAbsTop = cr.top + scrollY + (c.y + (c.ty || 0) / (c.scaleY || 2)) * ratio;
        if (charAbsTop >= scrollY - 30 && charAbsTop < scrollY + viewportH + 30) {
          hasVisibleChar = true;
          break; // 只要有一个可见字符就不是空页，提前退出
        }
      } catch (e) {}
    }

    if (hasVisibleChar) {
      return null; // 有可见文本，不是空页
    }

    return {
      textLen: 0,
      canvasCount: canvasElements.length,
      scrollY: Math.round(scrollY),
      url: location.href,
    };
  }

  // ---------- 监听 isolated world 指令 ----------
  window.addEventListener("message", function (event) {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.source !== CONTENT_SOURCE) return;

    switch (d.type) {
      case "highlight-chunk":
        highlightChunk(d.text);
        break;
      case "clear-highlight":
        clearHighlight();
        break;
      case "get-start-index":
        window.postMessage({ source: SOURCE, type: "start-index", result: getStartIndex(d.fromViewportStart) }, "*");
        break;
      case "get-empty-fingerprint":
        window.postMessage({ source: SOURCE, type: "empty-fingerprint", result: getEmptyPageFingerprint() }, "*");
        break;
      case "get-debug":
        window.postMessage({
          source: SOURCE, type: "debug",
          result: {
            hooked: !!CanvasRenderingContext2D.prototype.__voxWereadHooked,
            chars: charIndex.length,
            textLen: fullText.length,
            canvases: canvasElements.length,
          },
        }, "*");
        break;
    }
  });

  // ---------- 监听 canvas 变化 ----------
  const canvasObserver = new MutationObserver(function () {
    for (let i = canvasElements.length - 1; i >= 0; i--) {
      if (!document.body || !document.body.contains(canvasElements[i])) {
        if (overlays[i] && overlays[i].el && overlays[i].el.parentNode) {
          overlays[i].el.parentNode.removeChild(overlays[i].el);
        }
        const removedEl = canvasElements[i];
        charIndex = charIndex.filter(function (c) { return c.el !== removedEl; });
        canvasElements.splice(i, 1);
        overlays.splice(i, 1);
        charIndex.forEach(function (c) { if (c.canvasIdx > i) c.canvasIdx--; });
      }
    }
  });

  function init() {
    canvasObserver.observe(document.body || document.documentElement, { childList: true, subtree: true });
    window.addEventListener("resize", function () {
      overlays.forEach(function (o) { if (o && o.syncSize) o.syncSize(); });
    });
  }

  // hook 立即安装，不等 DOMContentLoaded
  installFillTextHook();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
