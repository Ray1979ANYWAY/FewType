// ---- Koodo Reader 平台专属的朗读逻辑 ----
//
// 跟 background-playbooks.js 是完全独立的模块，用独立的 storage key 存放
// 朗读状态，不共享任何内部状态，只共用 diagnostics.js / chunking.js /
// offscreen-client.js 这几个真正平台无关的工具模块。
//
// 跟 Play Books 的核心架构差异：整章内容通过 content-koodo.js 一次性提取
// 完成（不是分批异步到达的），所以完全不需要 Play Books 那一整套"翻页等待/
// 重试/内容对齐校验/防止缩水循环重播"的复杂状态机——章节数据一旦确定，
// 直接一次性切块、一次性发给 offscreen 播放就够了。
//
// 当前实现范围：
//   - 缓存 content-koodo.js 推送的整章内容
//   - 开始朗读时向 content-koodo.js 查询"当前可见的是哪一段"，从那里开始读
//   - 基础的暂停/继续/停止/语速调整/进度状态
// 还没做的部分（下一轮）：
//   - 高亮当前朗读位置（跨 iframe 高亮定位是全新技术点，留到基础朗读验证
//     通过后再做）
//   - 自动翻页跟随朗读进度
//   - 章节读完后自动跳到下一章（"怎么触发下一章"这件事完全没有验证过，
//     这一版章节读完就正常停止朗读，不臆造自动跳章的实现）

import { logEvent, textPreview } from "./diagnostics.js";
import { chunkTextByWords } from "./chunking.js";
import { sendToOffscreen } from "./offscreen-client.js";

// ---- 章节内容缓存：每个 tabId 存一份最新提取到的整章数据 ----
function chapterKey(tabId) {
  return `koodoChapter:${tabId}`;
}

async function saveChapterForTab(tabId, url, data) {
  await chrome.storage.session.set({
    [chapterKey(tabId)]: { url, data, updatedAt: Date.now() },
  });
}

async function getChapterForTab(tabId) {
  const result = await chrome.storage.session.get(chapterKey(tabId));
  return result[chapterKey(tabId)] || null;
}

// ---- 朗读状态：跟 Play Books 完全独立存储 ----
const KOODO_STATE_KEY = "koodoReadingState";

const DEFAULT_STATE = {
  isReading: false,
  isPaused: false,
  tabId: null,
  voice: null,
  rate: 1,
  connectionStatus: "idle", // idle | ok | error
  waitingForNextChapter: false,
  // 高亮未命中 → 自动翻页兜底（对齐 Play Books 空页逻辑）：
  highlightMissStreak: 0, // 连续未命中的 chunk 数
  pageTurnStreak: 0, // 连续"翻页仍抓不到文本"的次数
  // 空页（纯图片/空白页，无 p/h 文本）→ 朗读中自动翻页跳过：
  emptyPageStallCount: 0, // 连续"翻页后页面内容无变化"的次数
  emptyLastFingerprint: null, // 上次空页翻页时的页面指纹
  emptyLastTurnAt: 0, // 上次空页翻页的时间戳（节流用）
};

// 连续 N 个 chunk 高亮未命中才触发一次翻页（单个未命中多为瞬态抖动，不翻）
const MAX_MISS_BEFORE_TURN = 3;
// 连续翻 N 次页仍抓不到文本（大概率图片区段 / 章节末尾无正文），停止朗读防无限翻
const MAX_PAGE_TURNS = 3;
// 空页翻页的结束判断：连续 N 次"翻页后页面内容无变化"才认为到书尾。
// 不能用"连续 N 次无文本"判断——书中间可能有连续插画页（内容不同但都无文本），
// 后面还有正文；滚动到底时页面内容才稳定不变，那才是真正的终点。
const MAX_EMPTY_STALL = 4;
// 两次空页翻页的最小间隔：给 Koodo 翻页动画 / 章节懒加载留出时间，避免疯狂连翻
const EMPTY_TURN_INTERVAL_MS = 1200;

// "读完一章 → 切下一章"的等待超时句柄：只有真的没有下一章（或切章后没等到内容）
// 才停止朗读。空页翻页（在找下一页/下一章内容）时必须重置它，否则空页翻页耗时
// 超过该窗口会被误判成"没有下一章"而提前停止。
let nextChapterTimeoutHandle = null;

// 武装"等下一章"超时：切章后若 MAX_NEXT_CHAPTER_WAIT_MS 内既没等到新章节内容、
// 也没有继续翻页的动作，判定为已到全书最后一章，停止朗读。
const MAX_NEXT_CHAPTER_WAIT_MS = 8000;

function clearNextChapterTimeout() {
  if (nextChapterTimeoutHandle) {
    clearTimeout(nextChapterTimeoutHandle);
    nextChapterTimeoutHandle = null;
  }
}

function armNextChapterTimeout(tabId) {
  clearNextChapterTimeout();
  nextChapterTimeoutHandle = setTimeout(async () => {
    const s = await getReadingState();
    if (s.isReading && s.waitingForNextChapter && s.tabId === tabId) {
      logEvent("background", "[koodo] 没有等到下一章（可能已是最后一章），停止朗读");
      await stopReading();
    }
  }, MAX_NEXT_CHAPTER_WAIT_MS);
}

async function getReadingState() {
  const result = await chrome.storage.session.get(KOODO_STATE_KEY);
  return { ...DEFAULT_STATE, ...(result[KOODO_STATE_KEY] || {}) };
}

async function setReadingState(patch) {
  const current = await getReadingState();
  const next = { ...current, ...patch };
  await chrome.storage.session.set({ [KOODO_STATE_KEY]: next });
  return next;
}

// ---- 把整章数据切成 chunks ----
// 跟 Play Books 不同：这里不需要处理"跨页续接"，因为整章内容已经一次性
// 完整拿到了，不存在"这一段还没读完、下一段还没来"的等待场景。
// heading 独立成块；body 之间连续拼接后按 30 字规则切分；每段收尾不满 30 字
// 的部分直接归到这一段自己的最后一块，不用像 Play Books 那样留着等下一批。
function buildChunksFromChapterData(items) {
  const chunks = [];
  let bodyBuffer = "";

  function flushBodyBuffer() {
    if (!bodyBuffer) return;
    const { chunks: pieces, trailing } = chunkTextByWords(bodyBuffer);
    chunks.push(...pieces);
    if (trailing) chunks.push(trailing);
    bodyBuffer = "";
  }

  for (const item of items) {
    if (item.type === "heading") {
      flushBodyBuffer();
      chunks.push(item.text);
    } else {
      bodyBuffer += item.text;
    }
  }
  flushBodyBuffer();

  return chunks;
}

// ---- 开始朗读：从当前可见的位置开始 ----
async function startReading(tabId, voice, rate, { fromStart = false } = {}) {
  clearNextChapterTimeout(); // 新一轮朗读开始，任何"等下一章"超时都不再适用
  let segmentIndex = 0;
  let charOffset = 0;

  if (!fromStart) {
    try {
      const result = await chrome.tabs.sendMessage(tabId, { type: "KOODO_GET_START_INDEX" });
      if (result) {
        if (typeof result.segmentIndex === "number") segmentIndex = result.segmentIndex;
        if (typeof result.charOffset === "number") charOffset = result.charOffset;
      }
    } catch (e) {
      logEvent("background", `[koodo] 查询起始位置失败，从头开始朗读: ${e.message}`);
    }
  }

  const chapter = await getChapterForTab(tabId);
  if (!chapter || !chapter.data || chapter.data.length === 0) {
    return { ok: false, error: "还没有提取到章节内容，请稍等页面加载完成或刷新页面" };
  }

  const items = chapter.data.slice(segmentIndex);
  // 起始段落（items[0]，如果它是 body 类型）如果这个段落横跨了好几页，
  // charOffset 会指向"当前视口内第一个完整句子"在这段文字里的字符位置，
  // 需要把它截断，只保留从这个位置开始的部分——这样才能真正从用户当前
  // 正在看的内容开始朗读，而不是从这整个段落的开头念起（那样会重复念一遍
  // 用户已经看过、翻过去的内容）。
  if (items.length > 0 && charOffset > 0 && items[0].type === "body") {
    items[0] = { ...items[0], text: items[0].text.slice(charOffset) };
  }
  const chunks = buildChunksFromChapterData(items);

  if (chunks.length === 0) {
    return { ok: false, error: "没有可朗读的内容" };
  }

  logEvent(
    "background",
    `[koodo] 开始朗读，从 segmentIndex=${segmentIndex}, charOffset=${charOffset} 起，共 ${chunks.length} 块`,
    { preview: chunks.slice(0, 3).map((c) => textPreview(c, 12)) }
  );

  try {
    await sendToOffscreen({ type: "OFFSCREEN_PLAY", chunks, voice, rate });
  } catch (e) {
    return { ok: false, error: `无法连接朗读播放器：${e.message}` };
  }

  await setReadingState({
    isReading: true,
    isPaused: false,
    tabId,
    voice,
    rate,
    connectionStatus: "ok",
	waitingForNextChapter: false,
	highlightMissStreak: 0,
	pageTurnStreak: 0,
	emptyPageStallCount: 0,
	emptyLastFingerprint: null,
	emptyLastTurnAt: 0
  });

  // 通知 content 停掉旧的空页周期上报：换书/切页后可能残留上一本书的空页上报实例，
  // 开始朗读时统一清零，新朗读从当前页重新检测空页状态。
  chrome.tabs.sendMessage(tabId, { type: "KOODO_START_READING" }).catch(() => {});

  return { ok: true };
}

async function stopReading() {
  clearNextChapterTimeout();
  const prev = await getReadingState();
  if (prev.tabId) {
    // 通知 content 停止空页周期上报：停止朗读后 emptyPageTimer 不会自己停，
    // 残留实例的旧页面空页上报会在下一次朗读开始时被误当成"当前页是空页"处理，
    // 导致刚点开始朗读就把正在读的正文页翻走（"有文本连续跳页"）。
    chrome.tabs.sendMessage(prev.tabId, { type: "KOODO_STOP_READING" }).catch(() => {});
  }
  await setReadingState({ ...DEFAULT_STATE });
  sendToOffscreen({ type: "OFFSCREEN_STOP" }, 1).catch(() => {});
}

async function pauseReading() {
  await setReadingState({ isPaused: true });
  sendToOffscreen({ type: "OFFSCREEN_PAUSE" }, 1).catch(() => {});
}

async function resumeReading() {
  await setReadingState({ isPaused: false });
  sendToOffscreen({ type: "OFFSCREEN_RESUME" }, 1).catch(() => {});
}

async function setRateLive(rate) {
  const state = await setReadingState({ rate });
  if (state.isReading) {
    sendToOffscreen({ type: "OFFSCREEN_SET_RATE", rate }, 1).catch(() => {});
  }
}

export function handleKoodoMessage(message, sender, sendResponse) {
  switch (message.type) {
    case "KOODO_CHAPTER_UPDATED": {
  if (!sender.tab) return;
  (async () => {
    await saveChapterForTab(sender.tab.id, message.url, message.data);
    const state = await getReadingState();
    // 翻到有文本的章节（可能是"插画区空页翻页"后终于翻到正文页）：重置
    // 空页翻页的指纹/停滞计数，避免上一段的空页状态残留到这一章——否则空页
    // 翻页的"内容无变化→书尾"误判会把新章的朗读提前停掉（"插画后遇文本"跳页）。
    if (state.tabId === sender.tab.id && message.data && message.data.length > 0) {
      if (state.emptyLastFingerprint || state.emptyPageStallCount) {
        logEvent("background", "[koodo] 提取到有文本章节，重置空页翻页状态");
        await setReadingState({ emptyPageStallCount: 0, emptyLastFingerprint: null });
      }
    }
    if (
      state.isReading &&
      state.waitingForNextChapter &&
      state.tabId === sender.tab.id &&
      message.data &&
      message.data.length > 0
    ) {
      logEvent("background", `[koodo] 下一章已提取 ${message.data.length} 段，从章首继续朗读`);
      await startReading(sender.tab.id, state.voice, state.rate, { fromStart: true });
    }
  })();
  return;
    }

    case "GET_LATEST_FOR_ACTIVE_TAB": {
      (async () => {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        const chapter = tab ? await getChapterForTab(tab.id) : null;
        sendResponse(chapter);
      })();
      return true;
    }

    case "START_READING": {
      (async () => {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) {
          sendResponse({ ok: false, error: "找不到当前标签页" });
          return;
        }
        const result = await startReading(tab.id, message.voice, message.rate);
        sendResponse(result);
      })();
      return true;
    }

    case "STOP_READING": {
      stopReading().then(() => sendResponse({ ok: true }));
      return true;
    }

    case "PAUSE_READING": {
      pauseReading().then(() => sendResponse({ ok: true }));
      return true;
    }

    case "RESUME_READING": {
      resumeReading().then(() => sendResponse({ ok: true }));
      return true;
    }

    // content-koodo.js 快捷键 .（主键盘 / 小键盘）触发：播放中 → 暂停，已暂停 → 继续
    case "TOGGLE_PLAYBACK": {
      (async () => {
        const state = await getReadingState();
        if (!state.isReading) {
          logEvent("background", "[koodo] TOGGLE_PLAYBACK：当前未在朗读，忽略");
          return;
        }
        if (state.isPaused) {
          logEvent("background", "[koodo] TOGGLE_PLAYBACK：继续朗读");
          await resumeReading();
        } else {
          logEvent("background", "[koodo] TOGGLE_PLAYBACK：暂停朗读");
          await pauseReading();
        }
      })();
      return;
    }

    case "SET_RATE": {
      setRateLive(message.rate).then(() => sendResponse({ ok: true }));
      return true;
    }

    case "GET_READING_STATE": {
      (async () => {
        const state = await getReadingState();
        if (state.isReading) {
          // 跟 Play Books 一样的陈旧状态核实：offscreen document 早就没了，
          // 但存储里还留着"正在朗读"的旧状态，这里顺手纠正回来。
          const contexts = await chrome.runtime.getContexts({
            contextTypes: ["OFFSCREEN_DOCUMENT"],
          });
          if (contexts.length === 0) {
            const reset = await setReadingState({ ...DEFAULT_STATE });
            sendResponse(reset);
            return;
          }
        }
        sendResponse(state);
      })();
      return true;
    }

    case "SEEK": {
      sendToOffscreen({ type: "OFFSCREEN_SEEK", delta: message.delta }, 1).catch(() => {});
      return;
    }

    case "KOODO_EMPTY_PAGE": {
      // content 检测到当前页面无 p/h 文本（纯图片/空白页）时的周期上报。
      // 只在朗读中自动翻页跳过（用户手动浏览封面/插画不打扰）；
      // 用"翻页后页面内容有没有变化"判断是否到书尾：连续 MAX_EMPTY_STALL 次
      // 翻页后内容仍无变化（滚动到底、Koodo 不再懒加载新内容）→ 停止朗读。
      (async () => {
        const state = await getReadingState();
        if (!state.isReading || !state.tabId) return;
        // 只处理"正在朗读的那个 tab"发来的空页上报：其他 tab（比如还停在插画页的
        // 旧页面 / 另一本正在空页翻页的书）的残留空页上报如果也处理，会把正在朗读
        // 的页面翻走（"插画后遇文本" / 多 tab 跳页）。对齐 Play Books 的
        // PAGE_TEXT_UPDATED 空页分支（那里用 tabId = sender.tab.id 校验过了）。
        if (sender.tab && sender.tab.id !== state.tabId) return;
        const now = Date.now();
        // 节流：Koodo 翻页动画 / 章节懒加载需要时间，避免疯狂连翻
        if (now - (state.emptyLastTurnAt || 0) < EMPTY_TURN_INTERVAL_MS) return;
        const fp = message.fingerprint || null;
        const last = state.emptyLastFingerprint || null;
        const same =
          last &&
          fp &&
          fp.textLen === last.textLen &&
          fp.imgCount === last.imgCount &&
          JSON.stringify(fp.imgSrcs || []) === JSON.stringify(last.imgSrcs || []) &&
          Math.abs((fp.scrollY || 0) - (last.scrollY || 0)) < 2;
        const stall = same ? (state.emptyPageStallCount || 0) + 1 : 0;
        // 空页翻页与"高亮未命中翻页"互斥：空页本来就没有可高亮的内容，MISS 计数
        // 清零，避免两个翻页逻辑同时触发导致 double 翻页
        await setReadingState({
          emptyPageStallCount: stall,
          emptyLastFingerprint: fp,
          emptyLastTurnAt: now,
          highlightMissStreak: 0,
          pageTurnStreak: 0,
        });
        logEvent("background", `[koodo] 空页 ${same ? "无变化" : "有变化"}，停滞计数 ${stall}/${MAX_EMPTY_STALL}`, {
          fingerprint: fp,
        });
        if (stall >= MAX_EMPTY_STALL) {
          logEvent("background", "[koodo] 连续多次翻页内容无变化，判断已到书尾，停止朗读");
          await stopReading();
          return;
        }
        // 空页翻页就是"还在找下一章/下一页内容"：重置"没有等到下一章"的超时，
        // 否则空页翻页耗时超过该窗口会被误判成"没有下一章"而提前停止。
        // 书尾由上面的空页停滞判断兜底，这里只是把切章超时往后推。
        if (state.waitingForNextChapter) {
          logEvent("background", "[koodo] 空页翻页中，重置'等待下一章'超时");
          armNextChapterTimeout(state.tabId);
        }
        chrome.tabs.sendMessage(state.tabId, { type: "KOODO_TURN_PAGE" }).catch(() => {});
      })();
      return;
    }

    case "KOODO_HIGHLIGHT_HIT": {
      // content 高亮命中：未命中/翻页计数清零，恢复正常阅读
      (async () => {
        const state = await getReadingState();
        if (state.highlightMissStreak || state.pageTurnStreak) {
          await setReadingState({ highlightMissStreak: 0, pageTurnStreak: 0 });
          logEvent("background", "[koodo] 高亮命中，未命中/翻页计数清零");
        }
      })();
      return;
    }

    case "KOODO_HIGHLIGHT_MISS": {
      (async () => {
        const state = await getReadingState();
        if (!state.isReading || !state.tabId) return;
        const streak = (state.highlightMissStreak || 0) + 1;
        if (streak < MAX_MISS_BEFORE_TURN) {
          await setReadingState({ highlightMissStreak: streak });
          logEvent("background", `[koodo] 高亮未命中 ${streak}/${MAX_MISS_BEFORE_TURN}，继续观察`);
          return;
        }
        // 连续多个 chunk 未命中 → 翻页跳过（清零 streak，下一轮重新累计）
        await setReadingState({ highlightMissStreak: 0 });
        const turns = (state.pageTurnStreak || 0) + 1;
        if (turns > MAX_PAGE_TURNS) {
          logEvent("background", `[koodo] 已连续翻页 ${MAX_PAGE_TURNS} 次仍抓不到文本，停止朗读`);
          await stopReading();
          return;
        }
        await setReadingState({ pageTurnStreak: turns });
        logEvent("background", `[koodo] 连续未命中，第 ${turns} 次翻页跳过`);
        chrome.tabs.sendMessage(state.tabId, { type: "KOODO_TURN_PAGE" }).catch(() => {});
      })();
      return;
    }

    case "HIGHLIGHT_CHUNK": {
      // offscreen 播到某一块时通知 background，background 再转发给
      // content-koodo.js 去做跨 iframe 边界的高亮定位。
      (async () => {
        const state = await getReadingState();
        if (!state.tabId) return;
        chrome.tabs
          .sendMessage(state.tabId, {
            type: "HIGHLIGHT_CHUNK",
            text: message.text ?? null,
          })
          .catch(() => {});
      })();
      return;
    }

    case "PLAYBACK_PROGRESS": {
      (async () => {
        const state = await getReadingState();
        if (state.connectionStatus !== "ok") {
          await setReadingState({ connectionStatus: "ok" });
        }
      })();
      return;
    }

    case "AUDIO_STARTED": {
      return;
    }

    case "AUDIO_ENDED": {
  (async () => {
    const state = await getReadingState();
    if (!state.isReading || !state.tabId) {
      await stopReading();
      return;
    }
    logEvent("background", "[koodo] 本章读完，尝试切下一章");
    await setReadingState({ waitingForNextChapter: true });
    chrome.tabs.sendMessage(state.tabId, { type: "KOODO_NEXT_CHAPTER" }).catch(() => {});
    armNextChapterTimeout(state.tabId);
  })();
  return;
}

    case "AUDIO_ERROR": {
      logEvent("background", `[koodo] 播放出错: ${message.error}`);
      setReadingState({ connectionStatus: "error" });
      return;
    }

    default: {
      logEvent("background", `[koodo] 收到未处理的消息类型: ${message.type}`);
      return;
    }
  }
}
