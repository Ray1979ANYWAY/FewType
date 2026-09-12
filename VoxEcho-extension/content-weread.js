// ---- 微信读书 (weread.qq.com) content script (isolated world) ----
// isolated world 运行，与 background 通信、转发指令给 main world、模拟键盘翻页
// fillText hook 和高亮在 content-weread-main.js（main world），两者通过 window.postMessage 通信
// 两个脚本都在 manifest.json 注册为 document_start，main.js 用 world: "MAIN"

(function () {
  "use strict";

  console.log("[VoxEcho] content-weread.js (isolated) injected at", document.readyState);

  const MAIN_SOURCE = "vox-weread-main";
  const CONTENT_SOURCE = "vox-weread-content";

  function safeSendMessage(msg) {
    try { chrome.runtime.sendMessage(msg); } catch (e) {}
  }

  function diagLog(message, data) {
    safeSendMessage({ type: "DIAG_LOG", source: "content-weread", message, data });
  }

  // ---------- 与 main world 通信 ----------
  function postToMain(type, payload) {
    window.postMessage(Object.assign({ source: CONTENT_SOURCE, type }, payload || {}), "*");
  }

  let pendingStartIndexCallback = null;
  let isReading = false;
  let emptyPageTimer = null;

  window.addEventListener("message", function (event) {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.source !== MAIN_SOURCE) return;

    switch (d.type) {
      case "chapter-updated":
        safeSendMessage({
          type: "WEREAD_CHAPTER_UPDATED",
          url: d.url,
          data: [{ type: "body", text: d.text }],
        });
        diagLog("main world 重建正文", { len: d.text.length, chars: d.charCount, firstTransform: d.firstTransform || null });
        break;

      case "highlight-hit":
        safeSendMessage({ type: "WEREAD_HIGHLIGHT_HIT" });
        break;

      case "highlight-miss":
        safeSendMessage({ type: "WEREAD_HIGHLIGHT_MISS" });
        break;

      case "start-index":
        if (pendingStartIndexCallback) {
          pendingStartIndexCallback(d.result);
          pendingStartIndexCallback = null;
        }
        break;

      case "empty-fingerprint":
        // main world 返回 null 表示当前页有文本，不是空页，不上报
        if (isReading && d.result) {
          safeSendMessage({ type: "WEREAD_EMPTY_PAGE", fingerprint: d.result });
        }
        break;

      case "debug-info":
        diagLog("视口定位调试", d.result);
        break;
    }
  });

  // ---------- 翻页 / 切章 ----------
  const KEY_CODE_MAP = { ArrowRight: 39, ArrowLeft: 37, PageDown: 34, PageUp: 33 };

  function dispatchKey(key) {
    const opts = {
      key: key, code: key,
      keyCode: KEY_CODE_MAP[key] || 0,
      which: KEY_CODE_MAP[key] || 0,
      bubbles: true, cancelable: true,
    };
    document.dispatchEvent(new KeyboardEvent("keydown", opts));
    document.dispatchEvent(new KeyboardEvent("keyup", opts));
  }

  function detectMode() {
    const els = [document.documentElement, document.body, document.getElementById("app")];
    for (let i = 0; i < els.length; i++) {
      const el = els[i];
      if (el && el.scrollHeight > el.clientHeight + 100) return "scroll";
    }
    return "paged";
  }

  function turnPage() {
    postToMain("clear-highlight"); // 翻页前先清掉上一页高亮，避免旧高亮停留在新页
    dispatchKey(detectMode() === "scroll" ? "PageDown" : "ArrowRight");
  }

  function nextChapter() {
    postToMain("clear-highlight");
    dispatchKey("ArrowRight");
  }

  // ---------- 空页周期上报 ----------
  function scheduleEmptyPageReport() {
    if (emptyPageTimer) return;
    emptyPageTimer = setInterval(function () {
      if (!isReading) return;
      postToMain("get-empty-fingerprint");
    }, 1000);
  }

  function stopEmptyPageReport() {
    if (emptyPageTimer) { clearInterval(emptyPageTimer); emptyPageTimer = null; }
  }

  // ---------- "." 键暂停/恢复（与其他平台统一，主键盘+小键盘） ----------
  document.addEventListener("keydown", function (e) {
    if (e.key !== "." && e.keyCode !== 190 && e.keyCode !== 110) return;
    const target = e.target;
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
      return;
    }
    e.preventDefault();
    safeSendMessage({ type: "TOGGLE_PLAYBACK" });
  });

  // ---------- 监听 background 指令 ----------
  chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
    switch (message.type) {
      case "WEREAD_GET_START_INDEX":
        pendingStartIndexCallback = sendResponse;
        postToMain("get-start-index", { fromViewportStart: !!message.fromViewportStart });
        return true; // 异步回包

      case "WEREAD_TURN_PAGE":
        turnPage();
        return;

      case "WEREAD_NEXT_CHAPTER":
        nextChapter();
        return;

      case "WEREAD_START_READING":
        isReading = true;
        scheduleEmptyPageReport();
        return;

      case "WEREAD_STOP_READING":
        isReading = false;
        stopEmptyPageReport();
        postToMain("clear-highlight");
        return;

      case "HIGHLIGHT_CHUNK":
        postToMain("highlight-chunk", { text: message.text });
        return;
    }
  });

  diagLog("content-weread 初始化完成", { url: location.href });
})();
