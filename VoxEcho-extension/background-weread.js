// ---- 微信读书 (weread.qq.com) 平台专属的朗读逻辑 ----
//
// 基于 background-koodo.js 改写：整章内容通过 content-weread.js 的 fillText hook
// 一次性采集完成（不是分批异步到达），所以复用 Koodo 的"整章缓存 + 一次性切块"
// 状态机。差异仅在消息名、storage key、空页指纹字段（weread 无 imgCount/imgSrcs）。
//
// 防跳页经验（从 Koodo 沉淀，全部保留）：
//   1. 空页上报校验来源 tab
//   2. 切章超时句柄化（armNextChapterTimeout / clearNextChapterTimeout）
//   3. 空页翻页重置切章超时
//   4. 提取到有文本章节时重置空页状态
//   5. content 空页上报加 __hasEverMatched 守卫（在 content 侧）
//   6. stop/start 朗读广播停空页定时器
//   7. 空页与高亮未命中翻页互斥
//   8. 空页书尾判断：MAX_EMPTY_STALL=4，指纹无变化→停滞+1

import { logEvent, textPreview } from "./diagnostics.js";
import { chunkTextByWords, endsWithAnyPunctuation } from "./chunking.js";
import { sendToOffscreen } from "./offscreen-client.js";

// ---- 章节内容缓存：每个 tabId 存一份最新采集到的整章数据 ----
function chapterKey(tabId) {
  return `wereadChapter:${tabId}`;
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

// ---- 朗读状态：独立存储 ----
const WEREAD_STATE_KEY = "wereadReadingState";

const DEFAULT_STATE = {
  isReading: false,
  isPaused: false,
  tabId: null,
  voice: null,
  rate: 1,
  connectionStatus: "idle",
  waitingForNextChapter: false,
  highlightMissStreak: 0,
  pageTurnStreak: 0,
  emptyPageStallCount: 0,
  emptyLastFingerprint: null,
  emptyLastTurnAt: 0,
  pendingTrailingText: null, // 页尾非终结标点的半句，翻页后与下一页开头合并
  lastCharOffset: 0, // 当前朗读起始位置（翻页前用于生成锚点）
  pageAnchorText: null, // 翻页前记录的锚点文本（当前朗读位置后面的100字）
};

const MAX_MISS_BEFORE_TURN = 3;
const MAX_PAGE_TURNS = 3;
const MAX_EMPTY_STALL = 4;
const EMPTY_TURN_INTERVAL_MS = 1200;

let nextChapterTimeoutHandle = null;
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
      logEvent("background", "[weread] 没有等到下一章（可能已是最后一章），停止朗读");
      await stopReading();
    }
  }, MAX_NEXT_CHAPTER_WAIT_MS);
}

async function getReadingState() {
  const result = await chrome.storage.session.get(WEREAD_STATE_KEY);
  return { ...DEFAULT_STATE, ...(result[WEREAD_STATE_KEY] || {}) };
}

async function setReadingState(patch) {
  const current = await getReadingState();
  const next = { ...current, ...patch };
  await chrome.storage.session.set({ [WEREAD_STATE_KEY]: next });
  return next;
}

// ---- 切块 ----
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

// ---- 开始朗读 ----
// prefixText: 上一页页尾缓存的半句（非终结标点结尾），与当前页文本拼接后再切块，
// 实现"页尾一小段放到下一个 chunk 一起读"，避免句子在翻页处被断开。
async function startReading(tabId, voice, rate, { fromStart = false, prefixText = "", fromOffset = null, fromViewportStart = false } = {}) {
  clearNextChapterTimeout();
  let charOffset = 0;

  if (fromOffset !== null && fromOffset >= 0) {
    // 直接从指定字符位置开始（滚动模式翻页后增量追加，从旧文本末尾继续）
    charOffset = fromOffset;
  } else if (!fromStart) {
    try {
      const result = await chrome.tabs.sendMessage(tabId, { type: "WEREAD_GET_START_INDEX", fromViewportStart });
      if (result) {
        if (typeof result.segmentIndex === "number") charOffset = result.segmentIndex;
        if (typeof result.charOffset === "number") charOffset = result.charOffset;
      }
    } catch (e) {
      logEvent("background", `[weread] 查询起始位置失败，从头开始朗读: ${e.message}`);
    }
  }

  const chapter = await getChapterForTab(tabId);
  if (!chapter || !chapter.data || chapter.data.length === 0) {
    return { ok: false, error: "还没有采集到章节内容，请翻一页触发重绘后再试" };
  }

  // 拼接全文：上一页尾部（prefixText）+ 当前页文本，然后从 charOffset 截取
  let fullText = (prefixText || "") + chapter.data.map((d) => d.text).join("");
  fullText = fullText.slice(charOffset);

  const { chunks, trailing } = chunkTextByWords(fullText);
  const allChunks = chunks.slice();
  if (trailing) allChunks.push(trailing);

  if (allChunks.length === 0) {
    return { ok: false, error: "没有可朗读的内容" };
  }

  // 页尾断句：如果最后一个 chunk 不以终结标点结尾，缓存到下一页合并，
  // 不加入当前播放队列（避免半句被单独读完后断开）。
  let pendingTrailing = null;
  if (allChunks.length > 1 && !endsWithAnyPunctuation(allChunks[allChunks.length - 1])) {
    pendingTrailing = allChunks.pop();
  }

  logEvent(
    "background",
    `[weread] 开始朗读，charOffset=${charOffset}, 共 ${allChunks.length} 块` +
      (pendingTrailing ? `，页尾待合并 ${pendingTrailing.length} 字` : ""),
    { preview: allChunks.slice(0, 3).map((c) => textPreview(c, 12)) }
  );

  try {
    // 静音占位：把 \uE000 标记转换成 pause 对象（offscreen 播放时等待对应时长，不调用 TTS）
    const PAUSE_MARKER = "\uE000";
    const PAUSE_MS = 450;
    const playChunks = [];
    for (const chunk of allChunks) {
      if (typeof chunk !== "string") { playChunks.push(chunk); continue; }
      if (chunk.startsWith(PAUSE_MARKER)) {
        const remaining = chunk.slice(PAUSE_MARKER.length);
        playChunks.push({ type: "pause", durationMs: PAUSE_MS });
        if (remaining) playChunks.push(remaining);
      } else if (chunk.endsWith(PAUSE_MARKER)) {
        const remaining = chunk.slice(0, -PAUSE_MARKER.length);
        if (remaining) playChunks.push(remaining);
        playChunks.push({ type: "pause", durationMs: PAUSE_MS });
      } else if (chunk === PAUSE_MARKER) {
        playChunks.push({ type: "pause", durationMs: PAUSE_MS });
      } else {
        playChunks.push(chunk);
      }
    }
    await sendToOffscreen({ type: "OFFSCREEN_PLAY", chunks: playChunks, voice, rate });
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
    emptyLastTurnAt: 0,
    pendingTrailingText: pendingTrailing,
    lastCharOffset: charOffset,
  });

  chrome.tabs.sendMessage(tabId, { type: "WEREAD_START_READING" }).catch(() => {});
  return { ok: true };
}

async function stopReading() {
  clearNextChapterTimeout();
  const prev = await getReadingState();
  if (prev.tabId) {
    chrome.tabs.sendMessage(prev.tabId, { type: "WEREAD_STOP_READING" }).catch(() => {});
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

// ---- 消息分发 ----
export function handleWereadMessage(message, sender, sendResponse) {
  switch (message.type) {
    case "WEREAD_CHAPTER_UPDATED": {
      if (!sender.tab) return;
      (async () => {
        // 保存新章节前先获取旧章节，用于找到接续位置
        const oldChapter = await getChapterForTab(sender.tab.id);
        const oldText = oldChapter && oldChapter.data ? oldChapter.data.map((d) => d.text).join("") : "";
        const newText = message.data ? message.data.map((d) => d.text).join("") : "";
        // 标题页/封面页判断：文本很短（<30字）且无标点，认为是标题页，不是真正的正文章节
        const isTitleOrCoverPage = newText.length > 0 && newText.length < 30 && !/[。！？，；：、,.!?;:]/.test(newText);
        const isAppend = oldText.length > 0 && newText.length > oldText.length && newText.startsWith(oldText);
        const state = await getReadingState();

        // 即使不是前缀追加，也尝试用旧文本片段在新文本中搜索接续位置
        // （滚动模式翻页后 canvas 重绘，文本从章节开头重新采集，旧文本是新文本的中间子串）
        let continueOffset = -1;

        // 优先用翻页前记录的锚点（当前朗读位置后面的100字）
        // 虚拟滚动模式下坐标定位不可靠，文本锚点是最准确的
        if (state.pageAnchorText && state.pageAnchorText.length >= 20 && newText.length > 0) {
          const anchor = state.pageAnchorText;
          // 尝试完整锚点，失败则尝试锚点的后半段（避免前半段在翻页时被截断）
          const searchCandidates = [
            anchor,
            anchor.slice(20),
            anchor.slice(40),
            anchor.slice(0, 60),
            anchor.slice(0, 40),
          ];
          for (let ai = 0; ai < searchCandidates.length; ai++) {
            const snippet = searchCandidates[ai];
            if (snippet.length < 15) continue;
            const foundPos = newText.indexOf(snippet);
            if (foundPos !== -1) {
              // 锚点在旧文本中的起始位置
              const anchorStartInOld = oldText.indexOf(anchor);
              if (anchorStartInOld !== -1) {
                const remainingAfterAnchor = oldText.length - (anchorStartInOld + anchor.length);
                continueOffset = foundPos + snippet.length + remainingAfterAnchor;
                if (continueOffset >= 0 && continueOffset < newText.length) {
                  logEvent("background", `[weread] 翻页锚点命中: 候选${ai}(${snippet.length}字), 匹配到${foundPos}, 接续位置${continueOffset}`);
                  break;
                }
              }
            }
          }
          if (continueOffset < 0) {
            logEvent("background", `[weread] 翻页锚点未命中，回退到多位置片段搜索`);
          }
        }

        if (continueOffset < 0 && !isAppend && oldText.length > 30 && newText.length > 0) {
          // 调试：输出新旧文本开头对比，排查为什么开头搜索失败
          logEvent("background", `[weread] 片段搜索调试: oldStart="${oldText.slice(0, 30)}" newStart="${newText.slice(0, 30)}" oldLen=${oldText.length} newLen=${newText.length}`);
          // 旧文本可能是 canvas+DOM 混合，翻页后新文本是纯 canvas。
          // 优先用旧文本开头搜索（最唯一），如果开头有差异则用靠前的 canvas 部分位置。
          const candidates = [
            { snippet: oldText.slice(0, 25), isStart: true },   // 开头25字（最优先）
            { snippet: oldText.slice(0, 15), isStart: true },   // 开头15字（兜底）
            // 微信读书 canvas 虚拟渲染：翻页前后开头可能不同（小节标题只在旧文本中），
            // 所以用正文部分的多个位置搜索（旧文本前几百字可能是小节标题，新文本中没有）
            { snippet: oldText.slice(300, 325), isStart: false }, // 第300字起（正文部分）
            { snippet: oldText.slice(400, 425), isStart: false }, // 第400字起
            { snippet: oldText.slice(500, 525), isStart: false }, // 第500字起
            { snippet: oldText.slice(600, 625), isStart: false }, // 第600字起
            { snippet: oldText.slice(800, 825), isStart: false }, // 第800字起
            { snippet: oldText.slice(1000, 1025), isStart: false }, // 第1000字起
            { snippet: oldText.slice(-25), isStart: false },    // 末尾25字
            { snippet: oldText.slice(-60, -35), isStart: false }, // 末尾前35~60字
          ];
          for (let ci = 0; ci < candidates.length; ci++) {
            const snippet = candidates[ci].snippet;
            const isStart = candidates[ci].isStart;
            if (snippet.length < 8) continue;
            const foundPos = newText.indexOf(snippet);
            if (foundPos !== -1) {
              if (isStart) {
                // 开头片段：接续位置 = 旧文本开头在新文本中的位置 + 旧文本长度
                continueOffset = foundPos + oldText.length;
              } else {
                // 中间/末尾片段：计算片段在旧文本中的位置，推算接续位置
                const snippetStartInOld = oldText.indexOf(snippet);
                if (snippetStartInOld !== -1) {
                  const remainingAfterSnippet = oldText.length - (snippetStartInOld + snippet.length);
                  continueOffset = foundPos + snippet.length + remainingAfterSnippet;
                }
              }
              if (continueOffset >= 0 && continueOffset < newText.length) {
                logEvent("background", `[weread] 片段搜索命中: 候选位置${ci}(${isStart ? "开头" : "中间"}), 匹配到${foundPos}, 接续位置${continueOffset}`);
                break;
              }
            }
          }
        }

        await saveChapterForTab(sender.tab.id, message.url, message.data);
        if (state.tabId === sender.tab.id && message.data && message.data.length > 0 && !isTitleOrCoverPage) {
          if (state.emptyLastFingerprint || state.emptyPageStallCount) {
            logEvent("background", "[weread] 采集到有文本章节，重置空页翻页状态");
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
          if (isAppend) {
            // 增量追加：从旧文本末尾继续
            logEvent("background", `[weread] 增量追加 ${oldText.length}→${newText.length}，从 ${oldText.length} 继续`);
            await startReading(sender.tab.id, state.voice, state.rate, {
              fromOffset: oldText.length,
              prefixText: state.pendingTrailingText || "",
            });
          } else if (continueOffset >= 0 && continueOffset < newText.length) {
            // 翻页后查询视口起始位置（跳过导航条/章节标题，从正文第一句开始）
            let viewportOffset = -1;
            try {
              const vpResult = await chrome.tabs.sendMessage(sender.tab.id, { type: "WEREAD_GET_START_INDEX", fromViewportStart: true });
              if (vpResult && vpResult.charOffset !== undefined && vpResult.charOffset > 0) {
                viewportOffset = vpResult.charOffset;
              }
            } catch (e) {
              logEvent("background", `[weread] 翻页后视口定位查询失败: ${e.message}`);
            }

            if (viewportOffset > 0) {
              logEvent("background", `[weread] 翻页后视口定位成功，从正文第一句 ${viewportOffset} 开始朗读`);
              await startReading(sender.tab.id, state.voice, state.rate, {
                fromOffset: viewportOffset,
                prefixText: state.pendingTrailingText || "",
              });
            } else {
              logEvent("background", `[weread] 翻页后视口定位失败，从新文本开头开始朗读`);
              await startReading(sender.tab.id, state.voice, state.rate, {
                fromOffset: 0,
                prefixText: state.pendingTrailingText || "",
              });
            }
          } else if (state.pendingTrailingText) {
            logEvent("background", `[weread] 下一章已采集，合并页尾 ${state.pendingTrailingText.length} 字后继续朗读`);
            await startReading(sender.tab.id, state.voice, state.rate, {
              fromStart: true,
              prefixText: state.pendingTrailingText,
            });
          } else if (isTitleOrCoverPage) {
            // 标题页/封面页：不朗读，继续翻页找正文
            logEvent("background", `[weread] 下一章是标题页/封面页(${newText.length}字，无标点)，继续翻页`);
            chrome.tabs.sendMessage(sender.tab.id, { type: "WEREAD_TURN_PAGE" }).catch(() => {});
          } else {
            logEvent("background", `[weread] 下一章已采集 ${message.data.length} 段，从章首继续朗读`);
            await startReading(sender.tab.id, state.voice, state.rate, { fromStart: true });
          }
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

    case "TOGGLE_PLAYBACK": {
      (async () => {
        const state = await getReadingState();
        if (!state.isReading) {
          logEvent("background", "[weread] TOGGLE_PLAYBACK：当前未在朗读，忽略");
          return;
        }
        if (state.isPaused) {
          logEvent("background", "[weread] TOGGLE_PLAYBACK：继续朗读");
          await resumeReading();
        } else {
          logEvent("background", "[weread] TOGGLE_PLAYBACK：暂停朗读");
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

    case "WEREAD_EMPTY_PAGE": {
      (async () => {
        const state = await getReadingState();
        if (!state.isReading || !state.tabId) return;
        // 校验来源 tab
        if (sender.tab && sender.tab.id !== state.tabId) return;
        const now = Date.now();
        if (now - (state.emptyLastTurnAt || 0) < EMPTY_TURN_INTERVAL_MS) return;
        const fp = message.fingerprint || null;
        const last = state.emptyLastFingerprint || null;
        // weread 指纹：textLen / canvasCount / scrollY / url（无 imgCount/imgSrcs）
        const same =
          last &&
          fp &&
          fp.textLen === last.textLen &&
          fp.canvasCount === last.canvasCount &&
          Math.abs((fp.scrollY || 0) - (last.scrollY || 0)) < 2 &&
          fp.url === last.url;
        const stall = same ? (state.emptyPageStallCount || 0) + 1 : 0;
        await setReadingState({
          emptyPageStallCount: stall,
          emptyLastFingerprint: fp,
          emptyLastTurnAt: now,
          highlightMissStreak: 0,
          pageTurnStreak: 0,
        });
        logEvent("background", `[weread] 空页 ${same ? "无变化" : "有变化"}，停滞计数 ${stall}/${MAX_EMPTY_STALL}`, {
          fingerprint: fp,
        });
        if (stall >= MAX_EMPTY_STALL) {
          logEvent("background", "[weread] 连续多次翻页内容无变化，判断已到书尾，停止朗读");
          await stopReading();
          return;
        }
        if (state.waitingForNextChapter) {
          logEvent("background", "[weread] 空页翻页中，重置'等待下一章'超时");
          armNextChapterTimeout(state.tabId);
        }
        chrome.tabs.sendMessage(state.tabId, { type: "WEREAD_TURN_PAGE" }).catch(() => {});
      })();
      return;
    }

    case "WEREAD_HIGHLIGHT_HIT": {
      (async () => {
        const state = await getReadingState();
        if (state.highlightMissStreak || state.pageTurnStreak) {
          await setReadingState({ highlightMissStreak: 0, pageTurnStreak: 0 });
          logEvent("background", "[weread] 高亮命中，未命中/翻页计数清零");
        }
      })();
      return;
    }

    case "WEREAD_HIGHLIGHT_MISS": {
      (async () => {
        const state = await getReadingState();
        if (!state.isReading || !state.tabId) return;
        const streak = (state.highlightMissStreak || 0) + 1;
        if (streak < MAX_MISS_BEFORE_TURN) {
          await setReadingState({ highlightMissStreak: streak });
          logEvent("background", `[weread] 高亮未命中 ${streak}/${MAX_MISS_BEFORE_TURN}，继续观察`);
          return;
        }
        const turns = (state.pageTurnStreak || 0) + 1;
        if (turns > MAX_PAGE_TURNS) {
          logEvent("background", "[weread] 连续翻页仍高亮未命中，停止朗读");
          await stopReading();
          return;
        }
        await setReadingState({ highlightMissStreak: 0, pageTurnStreak: turns });
        logEvent("background", `[weread] 高亮未命中达阈值，翻页 ${turns}/${MAX_PAGE_TURNS}`);
        chrome.tabs.sendMessage(state.tabId, { type: "WEREAD_TURN_PAGE" }).catch(() => {});
      })();
      return;
    }

    case "HIGHLIGHT_CHUNK": {
      (async () => {
        const state = await getReadingState();
        if (!state.isReading || !state.tabId) return;
        chrome.tabs.sendMessage(state.tabId, { type: "HIGHLIGHT_CHUNK", text: message.text }).catch(() => {});
      })();
      return;
    }

    case "PLAYBACK_PROGRESS": {
      // 进度上报：weread 暂不做进度条，透传忽略
      return;
    }

    case "AUDIO_STARTED": {
      (async () => {
        await setReadingState({ connectionStatus: "ok" });
      })();
      return;
    }

    case "AUDIO_ENDED": {
      // 一章读完 → 切下一章（如果有页尾半句缓存，翻页后会自动合并）
      (async () => {
        const state = await getReadingState();
        if (!state.isReading || !state.tabId) return;

        // 翻页前记录锚点：当前朗读位置后面的 100 个字
        // （虚拟滚动模式下坐标定位不可靠，用文本锚点搜索更准确）
        let anchorText = null;
        try {
          const chapter = await getChapterForTab(state.tabId);
          if (chapter && chapter.data) {
            const fullText = chapter.data.map((d) => d.text).join("");
            const anchorStart = state.lastCharOffset || 0;
            anchorText = fullText.slice(anchorStart, anchorStart + 100);
            if (anchorText.length < 20) anchorText = null;
          }
        } catch (e) {}

        if (state.pendingTrailingText) {
          logEvent("background", `[weread] 队列读完，页尾有 ${state.pendingTrailingText.length} 字待合并，翻页后与下一页拼接`);
        } else {
          logEvent("background", "[weread] 本章朗读结束，请求切下一章");
        }
        await setReadingState({ waitingForNextChapter: true, pageAnchorText: anchorText });
        if (anchorText) {
          logEvent("background", `[weread] 翻页锚点已记录: ${textPreview(anchorText, 30)}`);
        }
        chrome.tabs.sendMessage(state.tabId, { type: "WEREAD_NEXT_CHAPTER" }).catch(() => {});
        armNextChapterTimeout(state.tabId);
      })();
      return;
    }

    case "AUDIO_ERROR": {
      (async () => {
        logEvent("background", "[weread] 音频播放错误", message.error || message);
        await setReadingState({ connectionStatus: "error" });
      })();
      return;
    }

    default: {
      logEvent("background", `[weread] 收到未处理的消息类型: ${message.type}`);
      return;
    }
  }
}
