// 模拟验证：微信读书翻页替换 canvas 导致的字符残留污染 → 修复后干净
// 复现日志中的场景：旧章节（canvas A）→ 翻页 → 新章节（canvas B，索引被回收为 0）

"use strict";

// ---- 模拟对象 ----
function FakeCanvas(id) { this.id = id; this.rectTop = 0; }
FakeCanvas.prototype.getBoundingClientRect = function () { return { left: 100, top: this.rectTop }; };

// ---- 采集逻辑（与 content-weread-main.js 的 fillText hook 一致）----
let canvasElements = [];
let charIndex = [];

function fakeFillText(canvas, str, x, y) {
  let idx = canvasElements.indexOf(canvas);
  if (idx === -1) { idx = canvasElements.length; canvasElements.push(canvas); }
  for (let i = 0; i < str.length; i++) {
    const ch = str[i];
    if (ch === "\n") continue;
    charIndex.push({ canvasIdx: idx, el: canvas, x: x + i * 18, y: y, size: 18, ch: ch, scaleX: 2 });
  }
}

// ---- 修复后的 rebuild 过滤器（与改动后的 rebuildTextAndNotify 一致）----
function rebuildFilter() {
  const aliveEls = new Set(canvasElements);
  charIndex = charIndex.filter(function (c) {
    if (c.canvasIdx === -1) return false;                  // 旧虚拟标题句号
    if (!c.el || !aliveEls.has(c.el)) return false;        // canvas 已被替换
    if (c.canvasIdx >= canvasElements.length) return false; // 索引悬空
    return true;
  });
}

// ---- 修复后的排序（含虚拟字符防御）----
function rebuildSort() {
  charIndex.sort(function (a, b) {
    const aVirtual = a.canvasIdx === -1;
    const bVirtual = b.canvasIdx === -1;
    let leftA = 0, leftB = 0, topA = 0, topB = 0;
    if (!aVirtual) { const ra = canvasElements[a.canvasIdx].getBoundingClientRect(); leftA = ra.left; topA = ra.top; }
    if (!bVirtual) { const rb = canvasElements[b.canvasIdx].getBoundingClientRect(); leftB = rb.left; topB = rb.top; }
    if (!aVirtual && !bVirtual) {
      if (Math.abs(leftA - leftB) > 2) return leftA - leftB;
      if (Math.abs(topA - topB) > 2) return topA - topB;
    }
    if (Math.abs(a.y - b.y) > 3) return a.y - b.y;
    return a.x - b.x;
  });
}

// ---- 旧版行为（修复前）：观察器只删数组条目不删字符 ----
function legacyObserverRemove(canvasElements, removed) {
  const i = canvasElements.indexOf(removed);
  if (i === -1) return;
  canvasElements.splice(i, 1);
  // 修复前：charIndex 不清理，只递减 canvasIdx
  charIndex.forEach(function (c) { if (c.canvasIdx > i) c.canvasIdx--; });
}
// ---- 修复后行为：观察器按元素身份清理字符 ----
function fixedObserverRemove(removed) {
  const i = canvasElements.indexOf(removed);
  if (i === -1) return;
  charIndex = charIndex.filter(function (c) { return c.el !== removed; });
  canvasElements.splice(i, 1);
  charIndex.forEach(function (c) { if (c.canvasIdx > i) c.canvasIdx--; });
}

// ---- 场景模拟 ----
function runScenario(name, useFixedObserver) {
  canvasElements = [];
  charIndex = [];
  const canvasA = new FakeCanvas("A"); canvasA.rectTop = -13376;
  const canvasB = new FakeCanvas("B"); canvasB.rectTop = 182;

  // 1) 旧章节（三十而立一）：标题 30 而立 + "一" + 正文第一句
  fakeFillText(canvasA, "三十而立", 341, 21, 28.8); // size 参数在 hook 里来自 font，此处简化
  charIndex[0].size = 28.8; charIndex[1].size = 28.8; charIndex[2].size = 28.8; charIndex[3].size = 28.8;
  fakeFillText(canvasA, "一", 386, 92); charIndex[4].size = 28.8;
  fakeFillText(canvasA, "王二生在北京城，我就是王二。夏天的早上，我骑车子去上学。经过学校门口时，看着学校庄严的大门", 0, 207);

  // 2) 上一轮重建插入的虚拟标题句号（canvasIdx=-1，旧版会残留）
  charIndex.push({ canvasIdx: -1, el: null, x: 364, y: 21, size: 28.8, ch: "。", scaleX: 2, isVirtual: true });
  charIndex.push({ canvasIdx: -1, el: null, x: 386, y: 92, size: 28.8, ch: "。", scaleX: 2, isVirtual: true });

  const oldChars = charIndex.length;

  // 3) 翻页：canvas A 被移除，canvas B 加入（索引回收为 0）
  if (useFixedObserver) {
    fixedObserverRemove(canvasA);
  } else {
    legacyObserverRemove(canvasElements, canvasA);
  }

  // 4) 新章节（三十而立二）绘制在 canvas B
  fakeFillText(canvasB, "想到这件事，不知不觉喝了很多酒。", 0, 35);
  fakeFillText(canvasB, "好像孙二娘在看包子馅。我在恍惚之间被她拖进了厨房", 0, 63.8);

  // 5) 重建（过滤 + 排序 + 拼接）
  const before = charIndex.length;
  rebuildFilter();
  rebuildSort();
  const text = charIndex.map(c => c.ch).join("");

  console.log("=== " + name + " ===");
  console.log("旧章节字符数: " + oldChars + ", 重建前字符数: " + before + ", 过滤后: " + charIndex.length);
  console.log("重建文本: " + text);
  if (text.indexOf("王二生") !== -1) console.log(">>> 污染仍在（失败）");
  else if (text.indexOf("想到这件事") !== -1 && text.indexOf("王二生") === -1) console.log(">>> 干净（通过）");
  console.log("");
}

runScenario("修复前（旧版观察器）", false);
runScenario("修复后（按元素身份清理）", true);

// ---- 额外验证：旧虚拟句号在修复后不会顶到开头 ----
canvasElements = [];
charIndex = [];
const cB = new FakeCanvas("B2"); cB.rectTop = 182;
fakeFillText(cB, "三十而立", 341, 21); charIndex.forEach(c => c.size = 28.8);
// 模拟残留的两个旧虚拟句号
charIndex.push({ canvasIdx: -1, el: null, x: 364, y: 21, size: 28.8, ch: "。", scaleX: 2 });
charIndex.push({ canvasIdx: -1, el: null, x: 386, y: 92, size: 28.8, ch: "。", scaleX: 2 });
fakeFillText(cB, "想到这件事", 0, 35);
rebuildFilter();
rebuildSort();
const t2 = charIndex.map(c => c.ch).join("");
console.log("=== 旧虚拟句号清理 ===");
console.log("文本: " + t2);
if (t2.startsWith("。")) console.log(">>> 仍以句号开头（失败）");
else if (t2.startsWith("三十而立想到这件事")) console.log(">>> 无前缀句号（通过，标题行虚拟句号由重建时按布局重新插入）");
