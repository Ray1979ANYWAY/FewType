// footnote.js —— Play Books / Koodo 共用的脚注角标判定（DOM 平台）。
//
// 内容门槛：元素去空白后必须是纯数字 1~3 位，或方括号数字 [n]；不满足直接排除。
// 在门槛之上，满足任一特征即认定为脚注号：
//   1) <sub>/<sup> 语义标签；
//   2) vertical-align 为 super / sub / top（上下偏移）；
//   3) 字号 ≤ 段落基准字号的 85%（明显小字号）。
// 同字号、baseline、非 sup/sub 的普通数字（正文计数、文末注释条目）三条件都不命中，保留朗读。
(function () {
  const FN_DIGIT = /^\[?\d{1,3}\]?$/;

  function fnIsAnchor(el, win, baseFs) {
    if (!el) return false;
    const t = (el.textContent || "").replace(/\s+/g, "");
    if (!FN_DIGIT.test(t)) return false;
    let s;
    try {
      s = win.getComputedStyle(el);
    } catch (e) {
      return false;
    }
    const fs = parseFloat(s.fontSize) || 0;
    const va = (s.verticalAlign || "").toLowerCase();
    const tagHit = el.tagName === "SUP" || el.tagName === "SUB";
    const shifted = va === "super" || va === "sub" || va === "top";
    const small = Boolean(baseFs && fs <= baseFs * 0.85);
    return Boolean(tagHit || shifted || small);
  }

  // 收集段落 el 内（含自身）所有角标元素，供文本提取与高亮索引共同跳过，保证偏移一致。
  function fnCollectAnchors(el, win) {
    const set = new Set();
    let baseFs = 0;
    try {
      baseFs = parseFloat(win.getComputedStyle(el).fontSize) || 0;
    } catch (e) {
      baseFs = 0;
    }
    const consider = (c) => {
      if (fnIsAnchor(c, win, baseFs)) set.add(c);
    };
    consider(el);
    el.querySelectorAll("*").forEach(consider);
    return set;
  }

  window.__fewtypeFn = {
    isAnchor: fnIsAnchor,
    collectAnchors: fnCollectAnchors,
    DIGIT: FN_DIGIT,
  };
})();
