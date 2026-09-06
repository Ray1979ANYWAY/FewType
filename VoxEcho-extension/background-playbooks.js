// Play Books 平台专属朗读逻辑，被 background.js 引入，通过 handlePlaybooksMessage 对外暴露
import { logEvent, textPreview } from "./diagnostics.js";
import { isCJKChar, isLatinLetter, isAsciiDigit, chunkTextByWords, endsWithAnyPunctuation } from "./chunking.js";
import { ensureOffscreenDocument, sendToOffscreen } from "./offscreen-client.js";

// ---- 提取结果缓存：chrome.storage.session，扛得住 service worker 被回收重启 ----

function textKey(tabId) {
  return `tabText_${tabId}`;
}

async function saveLatestForTab(tabId, url, data) {
  await chrome.storage.session.set({
    [textKey(tabId)]: { url, data, updatedAt: Date.now() },
  });
}

async function getLatestForTab(tabId) {
  const result = await chrome.storage.session.get(textKey(tabId));
  return result[textKey(tabId)] || null;
}

// ---- 朗读状态 ----

const READING_STATE_KEY = "readingState";
const DEFAULT_STATE = {
  isReading: false,
  isPaused: false,
  tabId: null,
  voice: null,
  rate: 1,
  waitingForNextPage: false,
  errorRetryCount: 0,
  carryoverText: null, // 上一页末尾被硬切、还没有收尾标点的残句（X区），等下一页数据到了要接上去
  currentPageCarryoverPrefix: null, // 已经"落户"到当前这一页的续接前缀——跟上面 carryoverText 不是一回事：
  // carryoverText 是"还没派上用场、在等下一页"；这个是"已经确定要接在当前页第一段前面"，
  // 整页多次重算（比如同页追加消费）时要保持一致，不能每次都重新去问 carryoverText（那个用一次就清空了）。
  sentChunkCount: 0, // 当前页已经发给 offscreen 播放的块数——同页内容后续增长时，用这个数字确定新增的块从哪里切开始追加
  sentChunksSnapshot: [], // 已经真正发给 offscreen 的完整块内容（不只是数量）——重算整页后用来
  // 逐项比对前缀是否还一致，不一致就说明发生了错位（不只是末尾追加，可能中间/开头也变了），
  // 直接整页重发，不去猜"大概从哪里断开"，宁可重复也不能漏掉新冒出来的内容。见 syncAndSendDelta。
  emptyPageStreak: 0, // 连续遇到"这一页提取不到任何文字"的页数——用来给自动翻页设一个上限，避免图片书无限翻下去
  pageTurnRetryCount: 0, // 当前这次翻页等待超时后已经重试了几次——网络慢导致翻页耗时长，
  // 不代表真的翻到全书最后一页了，先重试几次再判定异常，见 triggerPageTurnAndWait
  // 空页自动翻页的"书尾判断"：翻页后页面内容有没有变化。连续插画页（内容不同但
  // 都无文本）不应误停，只有翻到书尾（翻页后内容不再变）才停止。
  emptyLastFingerprint: null, // 上次空页翻页时的页面指纹
  emptyPageStallCount: 0, // 连续"翻页后页面内容无变化"的次数
  emptyLastTurnAt: 0, // 上次空页翻页的时间戳（节流用）
  connectionStatus: "idle", // idle | ok | error —— 供 popup 信号灯显示
  // 用户点"开始朗读"时，按视口内第一个标点裁掉页首半句；只用一次，算完 chunks 后清掉
  startTrim: null, // { skipPrefix: string } | null
};

async function getReadingState() {
  const result = await chrome.storage.session.get(READING_STATE_KEY);
  return result[READING_STATE_KEY] || { ...DEFAULT_STATE };
}

async function setReadingState(patch) {
  const current = await getReadingState();
  const next = { ...current, ...patch };
  await chrome.storage.session.set({ [READING_STATE_KEY]: next });
  return next;
}

// 分块算法（isCJKChar/isLatinLetter/isAsciiDigit/chunkTextByWords/endsWithAnyPunctuation）
// 已抽到 chunking.js 共享模块，通过文件顶部的 import 引入。

// 把整页（已经排除掉被跨页续接扣走的部分）的段落，按"能不能跟前后合并、连续计数"分组：
// heading 永远单独一段，不参与连续计数（标题混进正文数字数听感很怪）；
// callout 自己连续合并成一条字流（长的话内部还是按 30 字规则切）；
// body 之间也互相连续合并——这是这次重写的核心，不再按原来的 <p> 各自独立切，
// 对话密集的书里一堆三五个字的短段落会被合并成大小均匀的块。
// 一旦类型切换（body<->callout）或者碰到 heading，就断开、重新开始计数。
function buildSegments(items) {
  const segments = [];
  let bufferType = null;
  let buffer = [];

  function flush() {
    if (buffer.length > 0) {
      segments.push({ type: bufferType, text: buffer.join(" ") });
      buffer = [];
      bufferType = null;
    }
  }

  items.forEach((item) => {
    if (item.type === "heading") {
      flush();
      segments.push({ type: "heading", text: item.text });
      return;
    }
    if (bufferType !== null && bufferType !== item.type) {
      flush();
    }
    bufferType = item.type;
    buffer.push(item.text);
  });
  flush();

  return segments;
}

// 页面切换时调用：把"上一页留下的、还没派上用场的续接文字"正式过户给"当前这一页"，
// 同时把发送进度归零——只在真正翻到新一页时调用一次，同一页内容后续增长时不调用，
// 不然 currentPageCarryoverPrefix 会被清空、下次重算时前缀就对不上了（见 computeFullChunksForTab 里的说明）。
async function beginNewPageProcessing() {
  const state = await getReadingState();
  await setReadingState({
    currentPageCarryoverPrefix: state.carryoverText || null,
    carryoverText: null,
    sentChunkCount: 0,
    sentChunksSnapshot: [], // 换新页了，不能拿旧页发过的内容去校验新页的对齐
  });
}

// 把当前页缓存里的原始段落，完整地重新算一遍变成播放块数组——每次同一页有新内容
// （追加消费）时都会整页重算一次，不是只算新增的部分。这样做是为了让"块的边界"
// 保持稳定：chunkTextByWords 是严格从左到右、不回看的算法，在末尾追加更多文字
// 绝不会改变前面已经切出来的块，所以"重算全部、只发没发过的部分"和"只算新增部分"
// 在结果上是等价的，但前者简单得多，不用在原始段落和已发送块之间做复杂的对齐。
//
// 返回完整的 chunks 数组（这一页目前已知、可以播放的全部内容）。
// 副作用：会读取/清空 currentPageCarryoverPrefix 对应的续接前缀（不清空这个字段本身，
// 它在整页处理期间保持不变），并把这一页目前算出来的"结尾还没写完的部分"写回 carryoverText
// （每次重算都会覆盖，以最新一次算出来的为准；如果这次结尾写完了，会写成 null，等于取消掉之前的续接）。
// 判断一段文字是否包含至少一个"可朗读"的字符（CJK / 拉丁字母 / 数字）。
// 像"———"这种纯符号分隔线，提取出来会被切成单独的一块（不含标点也不含文字），
// 发给 TTS 合成时可能因为"没有任何实际文字内容"而请求失败。这类块直接过滤掉，
// 不发送给 TTS——反正也没有可读的内容，跳过不影响听感，还能避免因为它报错、
// 影响到后面正常内容的合成和播放。
function hasReadableContent(text) {
  for (const ch of text) {
    if (isCJKChar(ch) || isLatinLetter(ch) || isAsciiDigit(ch)) return true;
  }
  return false;
}

async function computeFullChunksForTab(tabId) {
  const latest = await getLatestForTab(tabId);
  if (!latest || !latest.data || latest.data.length === 0) return null;

  const state = await getReadingState();

  // 开始朗读时的起点裁剪：与 Koodo 一样，从视口内第一个标点之后起读。
  // content 给出 skipPrefix（起点之前的正文），这里从缓存段落里按序消费掉这段前缀。
  let pageItems = latest.data;
  const skipPrefix = state.startTrim && state.startTrim.skipPrefix;
  if (skipPrefix && skipPrefix.length > 0) {
    let remaining = skipPrefix.replace(/\s+/g, " ").trim();
    const kept = [];
    for (const item of pageItems) {
      if (!remaining) {
        kept.push(item);
        continue;
      }
      const norm = item.text.replace(/\s+/g, " ").trim();
      if (!norm) continue;
      if (remaining.startsWith(norm)) {
        remaining = remaining.slice(norm.length).replace(/^\s+/, "");
        continue; // 整段都在前缀里，丢掉
      }
      if (norm.startsWith(remaining)) {
        const cut = item.text.replace(/\s+/g, " ").trim().slice(remaining.length).trimStart();
        remaining = "";
        if (cut) kept.push({ ...item, text: cut });
        continue;
      }
      // 对不齐：停止裁剪，保留本段及之后，避免误删
      remaining = "";
      kept.push(item);
    }
    pageItems = kept;
    logEvent("background", `应用 startTrim skipPrefix 长度=${skipPrefix.length}`, {
      preview: pageItems.slice(0, 2).map((d) => textPreview(d.text, 20)),
    });
  }

  const segments = buildSegments(pageItems);
  if (segments.length === 0) return null;

  const carryoverPrefix = state.currentPageCarryoverPrefix || "";

  const chunks = [];
  let newCarryoverText = null;

  for (let s = 0; s < segments.length; s++) {
    const seg = segments[s];
    const isLastSegment = s === segments.length - 1;

    if (seg.type === "heading") {
      const headingText = seg.text.trim();
      if (headingText) {
        chunks.push(s === 0 && carryoverPrefix ? carryoverPrefix + headingText : headingText);
      }
      continue;
    }

    const { chunks: segChunks, trailing } = chunkTextByWords(seg.text);
    let finalSegChunks = segChunks.slice();

    if (trailing) {
      if (!isLastSegment || endsWithAnyPunctuation(trailing)) {
        // 不是这一批最后一段，或者虽然是最后一段但本来就有标点收尾——直接当成一块正常输出
        finalSegChunks.push(trailing);
      } else {
        // 这一批的最后一段，而且是真的没有标点收尾——留成 X 区，这次不输出
        newCarryoverText = trailing;
      }
    }

    if (s === 0 && carryoverPrefix) {
      if (finalSegChunks.length > 0) {
        finalSegChunks[0] = carryoverPrefix + finalSegChunks[0];
      } else {
        // 这一段整个都被留成了 X 区（比如极短、还没到下一个标点），那就把续接前缀
        // 也一起接上去，作为这一页目前唯一能确定要播的内容
        finalSegChunks = [carryoverPrefix + seg.text.trim()];
      }
    }

    chunks.push(...finalSegChunks);
  }

  await setReadingState({ carryoverText: newCarryoverText });

  const readableChunks = chunks.filter((c) => hasReadableContent(c));
  if (readableChunks.length !== chunks.length) {
    logEvent("background", `过滤掉 ${chunks.length - readableChunks.length} 个纯符号块（无可朗读内容）`, {
      filtered: chunks.filter((c) => !hasReadableContent(c)),
    });
  }

  return readableChunks.length > 0 ? readableChunks : null;
}

// ---- 翻页后等待新内容的超时（注意：这个 timer 只在 service worker 这次存活期间有效，
// 如果 SW 恰好在等待期间被系统回收，这个"翻到头了"的检测会失效。已知限制，见 README。） ----

const PAGE_TURN_TIMEOUT_MS = 6000;
let pageTurnTimeoutHandle = null;

// 连续遇到"这一页提取不到任何文字"的页数上限——超过这个数就放弃自动翻页、停止朗读
// （大概率是纯图片/封面区段，或者书已经读完了）。只要有一页提取到真内容就清零重新计数。
const MAX_EMPTY_PAGE_STREAK = 5;
// 空页翻页的书尾判断（取代"连续 N 次空页就停"）：连续 N 次"翻页后页面内容无变化"
// 才认为到书尾。书中间可能连续很多页插画（内容不同但都无文本），不该误停；
// 只有滚动/翻到底、内容不再变化才是真正的终点。
const MAX_EMPTY_STALL = 4;
// 两次空页翻页的最小间隔：给 Play Books 翻页动画/渲染留时间，避免疯狂连翻
const EMPTY_TURN_INTERVAL_MS = 1200;

function clearPageTurnTimeout() {
  if (pageTurnTimeoutHandle) {
    clearTimeout(pageTurnTimeoutHandle);
    pageTurnTimeoutHandle = null;
  }
}

// ensureOffscreenDocument / sendToOffscreen 已抽到 offscreen-client.js 共享模块，
// 通过文件顶部的 import 引入。

// 发出播放请求之后，如果既没收到"已经开始播放"也没收到"出错了"，
// 大概率是 offscreen 播放器在这期间被 Chrome 强制关掉、整个请求悄无声息地消失了。
// 这个监控器负责兜底：等太久没回应，直接当成一次失败处理，触发跟真实报错一样的自动重试。
const PLAYBACK_WATCHDOG_MS = 30000;
let playbackWatchdogHandle = null;

function clearPlaybackWatchdog() {
  if (playbackWatchdogHandle) {
    clearTimeout(playbackWatchdogHandle);
    playbackWatchdogHandle = null;
  }
}

function armPlaybackWatchdog(tabId) {
  clearPlaybackWatchdog();
  playbackWatchdogHandle = setTimeout(async () => {
    const s = await getReadingState();
    if (s.isReading && !s.isPaused && s.tabId === tabId) {
      await handleAudioError();
    }
  }, PLAYBACK_WATCHDOG_MS);
}

// 统一的"算出这一页目前完整内容 + 只发还没发过的部分"入口。
// useFreshPlay=true 用 OFFSCREEN_PLAY（整个新会话，用于真正开始朗读，或者灾难性重连失败后的整页重发）；
// useFreshPlay=false 用 OFFSCREEN_APPEND（接到现有播放队列后面，不打断正在播的音频）。
// overrideAlready：翻页接续时由调用方直接传入 0，跳过从 storage 读 sentChunkCount——
// 避免"beginNewPageProcessing 还没写完 storage、新页内容就已经到了"这个竞争窗口
// 导致读到上一页的旧计数、把新页头几块 slice 掉丢句子的问题。
// syncAndSendDelta 有多个调用入口（翻页接续、同页追加、灾难性重连整页重发），
// content.js 的 MutationObserver 在 Play Books 分批渲染新页面时可能连续快速触发好几次
// PAGE_TEXT_UPDATED，如果两次处理在这里发生并发（上一次的 computeFullChunksForTab / setReadingState
// 还没跑完，下一次就已经开始读同一份旧状态），两次会算出各自的 delta 并各自写回 sentChunkCount，
// 后写的覆盖先写的，状态就乱了，播出去的内容会跟原文本错位。
// 用互斥锁保证任意时刻只有一次真正在跑；被挡住的调用只记录"最新一次的参数"，
// 等当前这次跑完后自动用最新参数补跑一次——不会丢请求，也不会让过时的旧参数重跑。
let syncInProgress = false;
let syncPendingArgs = null;

async function syncAndSendDeltaLocked(tabId, voice, useFreshPlay, rate, overrideAlready) {
  if (syncInProgress) {
    logEvent("background", "syncAndSendDelta 被锁挡住，排队等补跑", { useFreshPlay, overrideAlready });
    syncPendingArgs = { tabId, voice, useFreshPlay, rate, overrideAlready };
    return { ok: true }; // 静默排队，不代表失败——补跑会在当前这次结束后自动发生
  }
  syncInProgress = true;
  try {
    return await syncAndSendDelta(tabId, voice, useFreshPlay, rate, overrideAlready);
  } finally {
    syncInProgress = false;
    if (syncPendingArgs) {
      const args = syncPendingArgs;
      syncPendingArgs = null;
      logEvent("background", "锁释放，自动补跑排队的请求");
      syncAndSendDeltaLocked(args.tabId, args.voice, args.useFreshPlay, args.rate, args.overrideAlready);
    }
  }
}

async function syncAndSendDelta(tabId, voice, useFreshPlay, rate, overrideAlready) {
  const chunks = await computeFullChunksForTab(tabId);
  if (!chunks || chunks.length === 0) {
    logEvent("background", "computeFullChunksForTab 返回空，没有可朗读内容");
    return { ok: false, error: "没有可朗读的内容，先在书页里翻到有正文的一页" };
  }

  logEvent("background", `computeFullChunksForTab 算出 ${chunks.length} 块`, {
    useFreshPlay,
    overrideAlready,
    preview: chunks.map((c) => textPreview(c, 12)),
  });

  const state = await getReadingState();
  const usingOverride = overrideAlready !== undefined;
  let already = usingOverride ? overrideAlready : (state.sentChunkCount || 0);

  // 前置保护：不是翻页接续场景（overrideAlready 未指定），且这次算出的 chunks 数量
  // 比之前已经真正发过的还少——这打破了"整页数据只会增长、不会减少"这个核心假设。
  // 观察到的实际原因：Play Books 某些书的正文渲染不是稳定单调递增的，可能用了虚拟
  // 滚动/懒加载机制，DOM 里同时存在的段落集合会随内部滚动状态变化，不是只增不减。
  // 如果不做这层保护，会陷入"这次缩水 → 对齐校验失败 → 归零重发缩水后的不完整内容
  // → 之后 DOM 又恢复完整 → 又对不上 → 再归零重发" 的循环——表现为整页内容被
  // 反复重复朗读（观察到同一页内容连续播了三轮）。
  // 直接忽略这次更新，不做任何处理，等下一次真正更完整的内容出现再处理。
  if (!usingOverride && chunks.length < already) {
    logEvent(
      "background",
      `本次内容 (${chunks.length} 块) 比之前已发送的 (${already} 块) 还少，判定为渲染临时性缩水，忽略`
    );
    return { ok: true };
  }

  // 内容校验，不能只信任 sentChunkCount 这个数字：
  // computeFullChunksForTab 假设"整页数据只会增长、不会改变"，正常情况下成立；
  // 但如果书页 DOM 是分批异步渲染的（先上报一次中间态触发一次计算，后面 DOM 补上的
  // 内容不一定只是在末尾追加，也可能是开头/中间的段落变完整了、切分结果整个不一样），
  // 直接信任 already 这个数字去 slice 会切错位置。
  // 校验方式：把这次重算出的 chunks 的前 already 项，跟"上次真正发出去的完整内容"
  // 逐项比对，必须完全一致才能信任 already。只要有一项对不上，就说明前面也可能已经
  // 变了（不只是末尾），不去猜"大概从哪个位置断开"（那样可能漏掉中间新冒出来的内容），
  // 直接把 already 归零、整页重发。代价是这种情况下 offscreen 可能会收到一些
  // 已经播过的内容、被重复播放一次——但这是极少发生的异常兜底路径，重复一句
  // 好过丢一句，听感上也远没有"内容凭空消失"那么突兀。
  if (already > 0) {
    const snapshot = state.sentChunksSnapshot || [];
    const prefixMatches =
      snapshot.length === already && snapshot.every((text, i) => chunks[i] === text);
    if (!prefixMatches) {
      logEvent("background", `对齐校验失败！already=${already} 归零整页重发`, {
        snapshotLen: snapshot.length,
        snapshotPreview: snapshot.map((c) => textPreview(c, 12)),
      });
      already = 0;
    } else {
      logEvent("background", `对齐校验通过，already=${already}`);
    }
  }

  const delta = chunks.slice(already);
  logEvent("background", `delta 计算结果：already=${already}, delta.length=${delta.length}`, {
    deltaPreview: delta.map((c) => textPreview(c, 12)),
  });

  if (delta.length === 0) {
    logEvent("background", "delta 为空，本次无新内容可发");
    return { ok: true }; // 这次重算没有新增内容可发，正常情况（比如更新对这一页没有实质进展）
  }

  try {
    if (useFreshPlay) {
      logEvent("background", `发送 OFFSCREEN_PLAY，${delta.length} 块`);
      await sendToOffscreen({ type: "OFFSCREEN_PLAY", chunks: delta, voice, rate });
    } else {
      logEvent("background", `发送 OFFSCREEN_APPEND，${delta.length} 块`);
      await sendToOffscreen({ type: "OFFSCREEN_APPEND", chunks: delta, voice });
    }
  } catch (e) {
    logEvent("background", `发送到 offscreen 失败：${e.message}`);
    await setReadingState({ connectionStatus: "error" });
    return { ok: false, error: `无法连接朗读播放器：${e.message}` };
  }
  await setReadingState({
    sentChunkCount: chunks.length,
    sentChunksSnapshot: chunks,
  });
  armPlaybackWatchdog(tabId);
  return { ok: true };
}

// 手动开始朗读一个全新页面，或者灾难性重连失败后整页重发——两种场景都要用 OFFSCREEN_PLAY。
// 是否需要重置 currentPageCarryoverPrefix（真的翻到新一页 vs 还在同一页重发）由调用方决定。
async function playLatestForTab(tabId, voice, rate) {
  return syncAndSendDeltaLocked(tabId, voice, true, rate);
}

// 翻页后接上朗读下一页，或者同一页后来又渲染出新内容时追加播放——都用 OFFSCREEN_APPEND，
// 不会打断正在播的音频。是否需要重置 currentPageCarryoverPrefix 由调用方决定。
// fromZero=true：翻页接续时使用，强制从 0 开始（绕过 storage 竞争导致的旧计数残留）。
// fromZero=false（默认）：同页追加时使用，从 storage 读真实的 sentChunkCount。
async function appendLatestForTab(tabId, voice, fromZero = false) {
  return syncAndSendDeltaLocked(tabId, voice, false, null, fromZero ? 0 : undefined);
}

async function startReading(tabId, voice, rate) {
  // 向 content 查询视口内朗读起点（任意标点之后）；失败则从头
  let startTrim = null;
  try {
    const result = await chrome.tabs.sendMessage(tabId, { type: "PLAYBOOKS_GET_START_INDEX" });
    if (result && typeof result.skipPrefix === "string" && result.skipPrefix.length > 0) {
      startTrim = { skipPrefix: result.skipPrefix };
      logEvent("background", `起点查询成功 skipPrefix 长度=${startTrim.skipPrefix.length}`);
    } else {
      logEvent("background", "起点查询返回空前缀，从头开始");
    }
  } catch (e) {
    logEvent("background", `起点查询失败，从头开始: ${e.message}`);
  }

  await beginNewPageProcessing(); // 全新开始朗读，重置这一页的续接前缀和已发送计数
  await setReadingState({ startTrim });
  const result = await playLatestForTab(tabId, voice, rate);
  // 只用一次，避免同页追加/翻页接续时再次裁掉页首
  await setReadingState({ startTrim: null });
  if (!result.ok) return result;

  clearPageTurnTimeout();
  await setReadingState({
    isReading: true,
    isPaused: false,
    tabId,
    voice,
    rate,
    waitingForNextPage: false,
    pageTurnRetryCount: 0,
    startTrim: null,
  });
  // 通知 content 停掉旧的空页周期上报，新朗读从当前页重新检测空页状态
  chrome.tabs.sendMessage(tabId, { type: "PB_START_READING" }).catch(() => {});
  return { ok: true };
}

async function stopReading() {
  clearPageTurnTimeout();
  clearPlaybackWatchdog();
  const prev = await getReadingState();
  if (prev.tabId) {
    // 通知 content 停止空页周期上报：防止残留实例的旧页面空页上报在下次
    // 开始朗读时被误当成"当前页是空页"处理而误翻页（对齐 Koodo 的同类修复）。
    chrome.tabs.sendMessage(prev.tabId, { type: "PB_STOP_READING" }).catch(() => {});
  }
  await setReadingState({ ...DEFAULT_STATE });
  chrome.runtime.sendMessage({ type: "OFFSCREEN_STOP" }).catch(() => {
    // offscreen 本来就不存在（比如陈旧状态被纠正、或者从没成功创建过），没什么好停的，忽略
  });
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

// 触发翻页 + 挂上"等待新内容"的超时兜底。
// 读完触发（AUDIO_ENDED）、空页触发（这一页提取不到文字）共用这一个函数；
// 靠 waitingForNextPage 这个标记防止重复触发。
const MAX_PAGE_TURN_RETRIES = 3; // 翻页等待超时后最多重试几次，才真正判定为"翻到头了/异常"并停止

async function triggerPageTurnAndWait(tabId, isRetry = false) {
  logEvent("background", `触发 TURN_PAGE${isRetry ? "（重试）" : ""}`, { tabId });
  if (tabId) {
    chrome.tabs.sendMessage(tabId, { type: "TURN_PAGE" }).catch(() => {
      // 标签页可能已经关闭，忽略
    });
  }

  await setReadingState({ waitingForNextPage: true });

  clearPageTurnTimeout();
  pageTurnTimeoutHandle = setTimeout(async () => {
    const s = await getReadingState();
    if (!s.isReading || !s.waitingForNextPage) return;

    // 单纯超时不代表"没有下一页了"——网络慢、Play Books 加载新页面内容慢，
    // 都会导致翻页耗时超过这个等待窗口，但下一页其实是存在的、只是还没到。
    // 之前一超时就直接停止朗读，网络稍微卡一下整个朗读就断掉了，体验很差。
    // 现在改成：超时后先重试几次（重新模拟一次翻页动作，再等一轮），
    // 只有连续 MAX_PAGE_TURN_RETRIES 次都等不到新内容，才真正判定为
    // "翻到全书最后一页了，或者翻页动作确实失效了"，这时候才停止朗读。
    const retryCount = (s.pageTurnRetryCount || 0) + 1;
    if (retryCount <= MAX_PAGE_TURN_RETRIES) {
      logEvent("background", `翻页等待超时，第 ${retryCount} 次重试`);
      await setReadingState({ pageTurnRetryCount: retryCount });
      await triggerPageTurnAndWait(tabId, true);
    } else {
      // 等了这么久、重试了这么多次还没等到新内容：大概率翻到全书最后一页了
      logEvent("background", `翻页超时，重试 ${MAX_PAGE_TURN_RETRIES} 次后仍无内容，停止朗读`);
      await stopReading();
    }
  }, PAGE_TURN_TIMEOUT_MS);
}

// 当前页所有块读完，触发翻页。
async function handleAudioEnded() {
  logEvent("background", "收到 AUDIO_ENDED");
  const state = await getReadingState();
  if (!state.isReading || state.isPaused) return; // 用户已手动停止/暂停，不用自动翻页
  if (state.waitingForNextPage) {
    logEvent("background", "AUDIO_ENDED 但已经在等翻页了，忽略（防重复）");
    return;
  }

  await triggerPageTurnAndWait(state.tabId);
}

// ---- 消息路由 ----

// 对外暴露的消息处理入口——被 background.js（路由层）调用。
// 保持跟原来顶层 addListener 回调完全一样的同步返回值语义：
// 返回 true 表示会异步调用 sendResponse，需要 background.js 那边的监听器
// 把这个 true 原样透传出去，才能让消息通道保持开启、sendResponse 能生效。
export function handlePlaybooksMessage(message, sender, sendResponse) {
  switch (message.type) {
    case "PAGE_TEXT_UPDATED": {
      if (!sender.tab) return;
      const tabId = sender.tab.id;

      if (message.data === null) {
        logEvent("background", "PAGE_TEXT_UPDATED: data=null (空页上报)", { tabId });
        chrome.storage.session.remove(textKey(tabId));
        (async () => {
          const state = await getReadingState();
          if (!state.isReading || state.isPaused || state.tabId !== tabId) return;
          const fp = message.fingerprint || null;
          const last = state.emptyLastFingerprint || null;
          const sameContent =
            last &&
            fp &&
            fp.textLen === last.textLen &&
            fp.imgCount === last.imgCount &&
            JSON.stringify(fp.imgSrcs || []) === JSON.stringify(last.imgSrcs || []);

          // 已经在等一次翻页的结果了：若这次空页指纹与翻页前完全相同 → 动画中间态
          // （旧页还短暂留在 DOM / 翻页无效内容没变），忽略，交给 triggerPageTurnAndWait
          // 的超时兜底；若指纹变了 → 翻页其实成功了、只是新页也是空页（连续插图区段），
          // 清除 waiting 标记继续走空页流程，不能因为"还在等翻页"而错过继续翻页。
          if (state.waitingForNextPage) {
            if (sameContent) {
              logEvent("background", "空页上报被忽略：内容未变（动画中间态或翻页无效）");
              return;
            }
            logEvent("background", "翻页成功但新页也是空页（内容已变），清除 waiting 继续空页流程");
            await setReadingState({ waitingForNextPage: false, pageTurnRetryCount: 0 });
          }

          // 节流：给 Play Books 翻页动画/渲染留时间
          const now = Date.now();
          if (now - (state.emptyLastTurnAt || 0) < EMPTY_TURN_INTERVAL_MS) return;

          // 书尾判断：翻页后内容无变化 → 停滞计数 +1；内容变了（新插画页/新章节）→ 归零
          const stall = sameContent ? (state.emptyPageStallCount || 0) + 1 : 0;
          await setReadingState({
            emptyPageStallCount: stall,
            emptyLastFingerprint: fp,
            emptyLastTurnAt: now,
          });
          logEvent("background", `空页 ${sameContent ? "无变化" : "有变化"}，停滞计数 ${stall}/${MAX_EMPTY_STALL}`, {
            fingerprint: fp,
          });
          if (stall >= MAX_EMPTY_STALL) {
            logEvent("background", "连续多次翻页内容无变化，判断已到书尾，停止朗读");
            await stopReading();
            return;
          }
          clearPageTurnTimeout();
          await triggerPageTurnAndWait(tabId);
        })();
        return;
      }

      logEvent("background", `PAGE_TEXT_UPDATED: 收到 ${message.data.length} 个原始段落`, {
        tabId,
        preview: message.data.slice(0, 3).map((d) => textPreview(d.text, 15)),
      });

      saveLatestForTab(tabId, message.url, message.data).then(async () => {
        const state = await getReadingState();
        if (state.emptyPageStreak) {
          await setReadingState({ emptyPageStreak: 0 }); // 拿到真内容了，空页计数清零
        }

        // 正在等着翻页翻成功、且这条更新确实来自正在朗读的那个标签页 —— 自动接上朗读下一页
        // 用 append 而不是 play：如果是提前触发翻页的，这一页尾巴的音频可能还没放完，
        // append 只会把新内容接到队列后面，不会打断正在播放的音频。
        if (state.isReading && state.waitingForNextPage && state.tabId === tabId) {
          logEvent("background", "走翻页接续分支 (waitingForNextPage=true)");
          clearPageTurnTimeout();
          // 翻页接续：把上一页的 carryoverText 接到新页第一块开头。
          await setReadingState({
            waitingForNextPage: false,
            currentPageCarryoverPrefix: state.carryoverText || null,
            carryoverText: null,
            sentChunkCount: 0,
            sentChunksSnapshot: [],
            pageTurnRetryCount: 0,
          });
          const result = await appendLatestForTab(tabId, state.voice, true); // 从0开始
          if (!result.ok) {
            await stopReading();
          }
        } else if (
          state.isReading &&
          !state.isPaused &&
          !state.waitingForNextPage &&
          state.tabId === tabId
        ) {
          // 还在读这一页（没在等翻页），但这一页比上次拿去播放的时候又多渲染出了新内容——
          // 常见于 Play Books 渲染较慢，一开始只抓到半页就先播了，剩下的稍后才出现在 DOM 里。
          // 不重置 currentPageCarryoverPrefix / sentChunkCount，整页重算一遍，只把还没
          // 发送过的部分（sentChunkCount 之后）追加进正在播的队列——不影响已经在播的部分。
          logEvent("background", "走同页追加分支 (isReading, 未在等翻页)");
          await appendLatestForTab(tabId, state.voice);
        } else {
          logEvent("background", "PAGE_TEXT_UPDATED 未匹配任何分支，忽略", {
            isReading: state.isReading,
            isPaused: state.isPaused,
            waitingForNextPage: state.waitingForNextPage,
            stateTabId: state.tabId,
            msgTabId: tabId,
          });
        }
      });
      return;
    }

    case "GET_LATEST_FOR_ACTIVE_TAB": {
      chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
        const tab = tabs[0];
        const result = tab ? await getLatestForTab(tab.id) : null;
        sendResponse(result);
      });
      return true;
    }

    case "START_READING": {
      chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
        const tab = tabs[0];
        if (!tab) {
          sendResponse({ ok: false, error: "找不到当前标签页" });
          return;
        }
        const result = await startReading(tab.id, message.voice, message.rate);
        sendResponse(result);
      });
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

    // content-playbooks.js 快捷键 .（主键盘 / 小键盘）触发：播放中 → 暂停，已暂停 → 继续
    case "TOGGLE_PLAYBACK": {
      (async () => {
        const state = await getReadingState();
        if (!state.isReading) {
          logEvent("background", "TOGGLE_PLAYBACK：当前未在朗读，忽略");
          return;
        }
        if (state.isPaused) {
          logEvent("background", "TOGGLE_PLAYBACK：继续朗读");
          await resumeReading();
        } else {
          logEvent("background", "TOGGLE_PLAYBACK：暂停朗读");
          await pauseReading();
        }
      })();
      return;
    }

    // content 根据高亮位置判断需要提前翻页（接近 Koodo 的"高亮滚出视口就翻页"）
    case "REQUEST_PAGE_TURN": {
      if (!sender.tab) return;
      (async () => {
        const state = await getReadingState();
        if (!state.isReading || state.isPaused) return;
        if (state.tabId !== sender.tab.id) return;
        if (state.waitingForNextPage) {
          logEvent("background", "REQUEST_PAGE_TURN：已在等翻页，忽略");
          return;
        }
        logEvent("background", "REQUEST_PAGE_TURN：按高亮位置提前翻页");
        await triggerPageTurnAndWait(sender.tab.id);
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
          // state 存在 chrome.storage.session 里，只有整个浏览器关闭才会清空——
          // 如果上次朗读中途没点"停止"就关了浏览器/换了书，这里会留下一个"正在朗读"的
          // 陈旧状态，但播放器早就没了。打开 popup 时顺手核实一下，不一致就纠正回来。
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

    case "PLAYBACK_PROGRESS": {
      // 每一块真正开始播放都会发这条消息——收到就说明音频在正常走，不是卡死。
      // 之前只有 chunk[0] 的 AUDIO_STARTED 会清一次 watchdog，后续每一块开始播放都没清，
      // 导致武装后的 30 秒计时器在任何一页读到一半时都会必然触发（不管播放是否正常），
      // 进而误判成"卡死"走向 RETRY，把正在正常播放的句子截断——这是"每页必跳 1~3 句、
      // 间隔约 30~35 秒"的主因。
      // 这里不能只清表：清完不重新武装，watchdog 以后就永久失效了，真正卡死时也不会
      // 触发兜底重试。正确做法是清表后立即重新武装，形成持续心跳——只要块与块之间的
      // 间隔在 30 秒以内（正常朗读必然如此），就不会误触发；真的卡住超过 30 秒依然能兜住。
      (async () => {
        const state = await getReadingState();
        if (state.isReading && !state.isPaused) {
          armPlaybackWatchdog(state.tabId);
        } else {
          clearPlaybackWatchdog();
        }
        // 收到这条消息，说明音频确实在正常播放——不管之前有没有报过错，
        // 现在是好的，把信号灯重设回绿。之前的 bug：只有"这次朗读会话的第一句"
        // 播放成功时才会重设灯（AUDIO_STARTED 只在 currentIndex===0 时发一次），
        // 导致中途报过一次错之后，哪怕后面读得完全正常，灯也永远锁死在红色。
        if (state.connectionStatus !== "ok" || state.errorRetryCount !== 0) {
          await setReadingState({ connectionStatus: "ok", errorRetryCount: 0 });
        }
      })();
      return;
    }

    case "SEEK": {
      // popup 发来的 ±5 秒指令，转发给 offscreen
      sendToOffscreen({ type: "OFFSCREEN_SEEK", delta: message.delta }, 1).catch(() => {});
      return;
    }

    case "HIGHLIGHT_CHUNK": {
      // offscreen 播到某一块时通知 background，background 再转发给书页的 content.js
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

    case "AUDIO_STARTED": {
      clearPlaybackWatchdog(); // 真的收到回应了，不需要监控器再兜底
      setReadingState({ errorRetryCount: 0, connectionStatus: "ok" });
      return;
    }

    case "AUDIO_ENDED": {
      clearPlaybackWatchdog();
      handleAudioEnded();
      return;
    }

    case "AUDIO_ERROR": {
      clearPlaybackWatchdog();
      handleAudioError();
      return;
    }
  }
}

// 实测下来，网络从没真正断过，只是偶尔连接慢、要多试几次——不设总次数上限，
// 只要还在朗读状态就一直重试下去，只有用户手动点"停止"才会真正停下来。
const BACKGROUND_RETRY_DELAY_MS = 5000;

async function handleAudioError() {
  clearPlaybackWatchdog(); // 防止监控器和真实报错同时触发，重复走两次重试

  const state = await getReadingState();
  if (!state.isReading) return; // 已经手动停止了，不用管

  await setReadingState({
    errorRetryCount: (state.errorRetryCount || 0) + 1,
    connectionStatus: "error",
  });

  setTimeout(async () => {
    const s = await getReadingState();
    if (!s.isReading) return; // 等待期间用户手动停止了
    try {
      // 原地重试卡住的那一句，不动 chunks/currentIndex——不会丢掉已经播过的进度，
      // 也不会把还没消费的续接前缀又重新拿出来处理一遍（那样容易乱）。
      await sendToOffscreen({ type: "OFFSCREEN_RETRY" });
      armPlaybackWatchdog(s.tabId);
    } catch (e) {
      // 连 offscreen 消息都发不出去（比较罕见），只能退回到整页重新发送——
      // 同一页，不重置 currentPageCarryoverPrefix，只把已发送计数清零，全量重发一次。
      await setReadingState({ sentChunkCount: 0 });
      await playLatestForTab(s.tabId, s.voice, s.rate);
    }
    // 这次如果还失败，offscreen 会再发一次 AUDIO_ERROR，走回这里继续重试，没有次数上限
  }, BACKGROUND_RETRY_DELAY_MS);
}
