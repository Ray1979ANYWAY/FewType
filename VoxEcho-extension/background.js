// ---- 路由入口 ----
// Manifest V3 规定一个扩展只能注册一个 service worker，没法让 Play Books 和
// Koodo 各自独立跑一份后台脚本。这个文件是唯一被注册的入口（manifest.json 里
// background.service_worker 指向这里），本身很薄——只做两件事：
//   1. 处理平台无关的诊断日志消息（DIAG_LOG / EXPORT_DIAG_LOG / CLEAR_DIAG_LOG）
//   2. 把其余消息分发给对应平台的模块处理
// 各平台的实际朗读逻辑（分块、翻页、状态机）都在各自的模块文件里，
// 彼此完全独立，改一个平台的逻辑不会影响另一个平台。
import { logEvent, exportDiagnosticLogText, diagnosticLogCount, clearDiagnosticLog } from "./diagnostics.js";
import { handlePlaybooksMessage } from "./background-playbooks.js";
import { handleKoodoMessage } from "./background-koodo.js";
import { handleWereadMessage } from "./background-weread.js";

function detectPlatformFromUrl(url) {
  if (!url) return null;
  if (url.startsWith("https://weread.qq.com/")) {
    return "weread";
  }
  if (url.startsWith("https://web.koodoreader.com/") || url.startsWith("https://web.koodoreader.cn/")) {
    return "koodo";
  }
  if (url.startsWith("https://play.google.com/books/reader") || url.startsWith("https://books.googleusercontent.com/")) {
    return "playbooks";
  }
  return null;
}

function dispatch(platform, message, sender, sendResponse) {
  if (platform === "weread") {
    return handleWereadMessage(message, sender, sendResponse);
  }
  if (platform === "koodo") {
    return handleKoodoMessage(message, sender, sendResponse);
  }
  return handlePlaybooksMessage(message, sender, sendResponse);
}

// 两个平台各自的朗读状态完全独立存储（不同的 chrome.storage.session key），
// 所以"暂停/继续/停止/查询状态"这类不重新查询标签页的操作，需要知道
// "现在到底是哪个平台正在朗读"——不能靠临时查询当前激活标签页的 url 来猜，
// 因为用户点暂停按钮时，当前激活的标签页完全可能已经切到别的网站去了，
// 如果重新猜，会把指令错误地发给不相关的模块，真正在读的那个平台反而收不到。
// 用一个独立存储的标记，只在 START_READING 真正发起时才更新。
const ACTIVE_PLATFORM_KEY = "ebookTtsActivePlatform";

async function getActivePlatform() {
  const result = await chrome.storage.session.get(ACTIVE_PLATFORM_KEY);
  return result[ACTIVE_PLATFORM_KEY] || "playbooks"; // 默认 Play Books，兼容还没开始过朗读的情况
}

async function setActivePlatform(platform) {
  await chrome.storage.session.set({ [ACTIVE_PLATFORM_KEY]: platform });
}

// 这两类消息代表"用户现在正在看的这个标签页"，需要重新查询当前激活标签页
// 来判断平台；其中 START_READING 还要把这次选定的平台记下来，供后续的
// 暂停/继续/停止操作使用。
const NEEDS_ACTIVE_TAB_LOOKUP = new Set(["START_READING", "GET_LATEST_FOR_ACTIVE_TAB"]);

// 需要给 popup 回包的会话消息（必须 return true + sendResponse）
const USES_EXISTING_SESSION_WITH_RESPONSE = new Set([
  "STOP_READING",
  "PAUSE_READING",
  "RESUME_READING",
  "SET_RATE",
  "GET_READING_STATE",
  "SEEK",
  "TOGGLE_PLAYBACK",
]);

// offscreen 状态通知：只处理、不回包。
// 若 return true 却不 sendResponse，Chrome 会在发送方（常是 offscreen）报
// "message channel closed before a response was received"。
const USES_EXISTING_SESSION_FIRE_AND_FORGET = new Set([
  "PLAYBACK_PROGRESS",
  "HIGHLIGHT_CHUNK",
  "AUDIO_STARTED",
  "AUDIO_ENDED",
  "AUDIO_ERROR",
]);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case "DIAG_LOG": {
      // content 脚本 / offscreen.js 转发过来的日志事件，统一汇总记录
      logEvent(message.source, message.message, message.data);
      return; // 不 return true，避免挂起 offscreen 的 sendMessage Promise
    }

    case "EXPORT_DIAG_LOG": {
      sendResponse({ text: exportDiagnosticLogText(), count: diagnosticLogCount() });
      return true;
    }

    case "CLEAR_DIAG_LOG": {
      clearDiagnosticLog();
      sendResponse({ ok: true });
      return true;
    }
  }

  // 来自 content script 的消息自带 sender.tab，可以直接用它的 url 判断平台，
  // 同步分发——这些消息本身就是各平台自己的 content script 主动发的，
  // 天然知道自己是谁（比如 PAGE_TEXT_UPDATED / KOODO_CHAPTER_UPDATED）。
  if (sender.tab && sender.tab.url) {
    const platform = detectPlatformFromUrl(sender.tab.url) || "playbooks";
    return dispatch(platform, message, sender, sendResponse);
  }

  // 来自 popup 的消息没有 sender.tab。
  if (NEEDS_ACTIVE_TAB_LOOKUP.has(message.type)) {
    (async () => {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const platform = detectPlatformFromUrl(tab?.url) || "playbooks";
      if (message.type === "START_READING") {
        await setActivePlatform(platform);
      }
      dispatch(platform, message, sender, sendResponse);
    })();
    return true;
  }

  if (USES_EXISTING_SESSION_WITH_RESPONSE.has(message.type)) {
    (async () => {
      const platform = await getActivePlatform();
      dispatch(platform, message, sender, sendResponse);
    })();
    return true;
  }

  if (USES_EXISTING_SESSION_FIRE_AND_FORGET.has(message.type)) {
    // 异步分发，但不声明“会 sendResponse”
    (async () => {
      const platform = await getActivePlatform();
      dispatch(platform, message, sender, () => {});
    })();
    return;
  }

  // 兜底：未知消息类型，走 Play Books（保持原来"什么都转发过去"的行为，
  // 不会因为出现一个没预料到的消息类型就完全没有响应）。
  return handlePlaybooksMessage(message, sender, sendResponse);
});

// ---- 本地桥接服务心跳检测 ----
// 扩展向本地服务 (127.0.0.1:5010) 发心跳，本地服务据此判断扩展是否在线。
// 为什么用 chrome.alarms 而不是 setInterval：
//   MV3 的 service worker 是事件驱动的，Chrome 会在不活跃时挂起 service worker，
//   挂起后 setInterval 不会继续执行，导致心跳停止。chrome.alarms 是浏览器级别的
//   定时器，即使 service worker 被挂起也能按时触发并唤醒 worker。
// 注意：chrome.alarms 的最小间隔是 1 分钟（periodInMinutes 最小为 1），
//       所以本地服务的心跳超时也相应设为 90 秒（大于 1 分钟，避免状态抖动）。
const BRIDGE_BASE_URL = "http://127.0.0.1:5010";
const HEARTBEAT_ALARM_NAME = "voxecho_heartbeat";

async function sendHeartbeat() {
  try {
    await fetch(BRIDGE_BASE_URL + "/extension_heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timestamp: Date.now() }),
    });
  } catch (e) {
    // 本地服务未启动时静默失败，不影响扩展其他功能
  }
}

// 启动心跳：service worker 激活时立即发一次，然后创建每分钟触发的闹钟
sendHeartbeat();
chrome.alarms.create(HEARTBEAT_ALARM_NAME, { periodInMinutes: 1 });

// 监听闹钟触发，发送心跳
// 注意：必须 return sendHeartbeat()（Promise），让 Chrome 等待异步 fetch 完成，
// 否则 service worker 可能在心跳发出去前就被挂起，导致心跳丢失。
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === HEARTBEAT_ALARM_NAME) {
    return sendHeartbeat();
  }
});

// popup 打开时也发送一次心跳（用户主动交互时立即更新状态）
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "POPUP_OPENED") {
    sendHeartbeat();
    sendResponse({ ok: true });
  }
});
