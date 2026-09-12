const player = document.getElementById("player");

function diagLog(message, data) {
  try {
    chrome.runtime.sendMessage({ type: "DIAG_LOG", source: "offscreen", message, data });
  } catch (e) {
    // ignore
  }
}

function textPreview(text, len = 12) {
  if (typeof text !== "string") return text;
  return text.length > len ? text.slice(0, len) + "…" : text;
}

let currentRate = 1;
let currentVoice = null;
let chunks = [];
let currentIndex = -1;
let sessionId = 0;
let activeAbortControllers = [];
let pending = new Map();
let playStartedAt = 0;

const PREFETCH_WINDOW = 5;
/** 单块合成失败后，在停播前再试几次（桥接本身也有重试，这里防短暂断线/超时） */
const CHUNK_FETCH_RETRIES = 4;
const CHUNK_RETRY_DELAY_MS = 1500;
/** 连续失败超过这个数才真正停；中间成功会清零 */
const MAX_CONSECUTIVE_FAILURES = 5;
/** 单次合成请求的超时：本地桥 /speak 会等远程 TTS 返回，网络慢时可能长时间无响应。
 *  不加超时的话 fetch 会永久挂起（既不成功也不失败），朗读卡在等待；加了超时后
 *  超时按失败处理、进入重试，网络恢复后能接上，而不是一直干等。 */
const FETCH_TIMEOUT_MS = 30000;
let consecutiveFailures = 0;

function abortAllPending() {
  activeAbortControllers.forEach((c) => c.abort());
  activeAbortControllers = [];
}

function fetchChunk(text, voice) {
  const controller = new AbortController();
  activeAbortControllers.push(controller);
  // 超时用 abort(reason) 传一个普通 Error：这样 fetch reject 的是这个 Error 而不是
  // AbortError，fetchChunkWithRetry 会把它当成普通失败走重试；如果直接 abort() 无参
  // 数，fetch reject 成 AbortError，会被当作"主动取消"直接放弃，不重试。
  const timeout = setTimeout(() => controller.abort(new Error("synthesize timeout")), FETCH_TIMEOUT_MS);
  return fetch("http://127.0.0.1:5005/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice }),
    signal: controller.signal,
  })
    .then(async (resp) => {
      clearTimeout(timeout);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      return resp.blob();
    })
    .catch((e) => {
      clearTimeout(timeout);
      throw e;
    });
}

/** 带重试的合成；全部失败才返回 { __error } */
async function fetchChunkWithRetry(text, voice, label) {
  let lastErr = null;
  for (let attempt = 1; attempt <= CHUNK_FETCH_RETRIES; attempt++) {
    try {
      const blob = await fetchChunk(text, voice);
      if (attempt > 1) {
        diagLog(`${label} 第 ${attempt} 次重试成功`);
      }
      return blob;
    } catch (e) {
      if (e && e.name === "AbortError") throw e;
      lastErr = e;
      diagLog(`${label} 合成失败 ${attempt}/${CHUNK_FETCH_RETRIES}: ${e.message || e}`);
      if (attempt < CHUNK_FETCH_RETRIES) {
        await new Promise((r) => setTimeout(r, CHUNK_RETRY_DELAY_MS * attempt));
      }
    }
  }
  return { __error: lastErr || new Error("synthesize failed") };
}

function ensurePrefetched() {
  const start = currentIndex + 1;
  const end = Math.min(currentIndex + PREFETCH_WINDOW, chunks.length - 1);
  for (let i = start; i <= end; i++) {
    // pause 对象不需要预取（不调用 TTS 合成）
    if (typeof chunks[i] === "object" && chunks[i] && chunks[i].type === "pause") continue;
    if (!pending.has(i)) {
      pending.set(
        i,
        fetchChunkWithRetry(chunks[i], currentVoice, `chunk[${i}]`).catch((e) => {
          if (e && e.name === "AbortError") return { __error: e };
          return { __error: e };
        })
      );
    }
  }
}

async function playChunkList(newChunks, voice, rate) {
  diagLog(`OFFSCREEN_PLAY 收到 ${newChunks.length} 块，开新 session`, {
    preview: newChunks.map((c) => textPreview(c)),
  });
  abortAllPending();
  const mySession = ++sessionId;
  chunks = newChunks;
  currentIndex = -1;
  pending = new Map();
  currentVoice = voice;
  ranDry = false;
  consecutiveFailures = 0;
  if (rate) currentRate = rate;

  if (chunks.length === 0) {
    chrome.runtime.sendMessage({ type: "AUDIO_ERROR", error: "没有可朗读的内容" });
    return;
  }

  ensurePrefetched();
  await advanceAndPlay(mySession);
}

let advancing = false;
let advancePending = false;

async function advanceAndPlay(mySession) {
  if (advancing) {
    advancePending = true;
    return;
  }
  advancing = true;
  try {
    await doAdvanceAndPlay(mySession);
  } finally {
    advancing = false;
    if (advancePending) {
      advancePending = false;
      advanceAndPlay(sessionId);
    }
  }
}

let ranDry = false;
let srcEpoch = 0;

async function doAdvanceAndPlay(mySession) {
  const nextIndex = currentIndex + 1;
  if (nextIndex >= chunks.length) {
    if (mySession === sessionId) {
      ranDry = true;
      diagLog(
        `队列耗尽 (currentIndex=${currentIndex}, chunks.length=${chunks.length})，发 AUDIO_ENDED`
      );
      chrome.runtime.sendMessage({ type: "AUDIO_ENDED" });
    }
    return;
  }
  ranDry = false;

  // pause 对象：等待对应时长，不调用 TTS 合成
  const nextChunk = chunks[nextIndex];
  if (typeof nextChunk === "object" && nextChunk && nextChunk.type === "pause") {
    const pauseMs = nextChunk.durationMs || 450;
    diagLog("chunk[" + nextIndex + "] pause " + pauseMs + "ms（标题后静音占位）");
    await new Promise(function (r) { setTimeout(r, pauseMs); });
    if (mySession !== sessionId) return;
    currentIndex = nextIndex;
    consecutiveFailures = 0;
    chrome.runtime.sendMessage({ type: "HIGHLIGHT_CHUNK", text: "" });
    if (currentIndex === 0) {
      chrome.runtime.sendMessage({ type: "AUDIO_STARTED" });
    }
    ensurePrefetched();
    advanceAndPlay(sessionId);
    return;
  }

  if (!pending.has(nextIndex)) {
    diagLog(`nextIndex=${nextIndex} 没有 pending 记录，补一次 fetch`);
    pending.set(
      nextIndex,
      fetchChunkWithRetry(chunks[nextIndex], currentVoice, `chunk[${nextIndex}]`).catch(
        (e) => ({ __error: e })
      )
    );
  }

  try {
    const result = await pending.get(nextIndex);
    pending.delete(nextIndex);
    if (mySession !== sessionId) {
      diagLog(
        `session 已过期 (mySession=${mySession}, sessionId=${sessionId})，放弃 nextIndex=${nextIndex}`
      );
      return;
    }

    if (result && result.__error) throw result.__error;

    const url = URL.createObjectURL(result);

    const epoch = ++srcEpoch;
    player.src = url;
    player.playbackRate = currentRate;
    await player.play();

    if (mySession !== sessionId || epoch !== srcEpoch) {
      diagLog(
        `播放确认前发现过期 (mySession match=${mySession === sessionId}, epoch match=${epoch === srcEpoch})，放弃 nextIndex=${nextIndex}`
      );
      return;
    }

    currentIndex = nextIndex;
    consecutiveFailures = 0;
    const chunkText = chunks[currentIndex];
    const expectedSeconds = (chunkText.replace(/\s/g, "").length / 5).toFixed(1);
    diagLog(
      `开始播放 chunk[${currentIndex}]，字数=${chunkText.length}，预估${expectedSeconds}s，` +
        `blob=${result.size}B，audio.duration=${Number.isFinite(player.duration) ? player.duration.toFixed(2) : "?"}s`,
      { text: textPreview(chunkText, 20) }
    );
    playStartedAt = Date.now();
    ensurePrefetched();

    player.addEventListener(
      "ended",
      () => {
        if (epoch !== srcEpoch) {
          diagLog(`迟到的 ended 事件被挡下 (epoch=${epoch}, srcEpoch=${srcEpoch})`);
          return;
        }
        const elapsed = ((Date.now() - playStartedAt) / 1000).toFixed(2);
        const dur = Number.isFinite(player.duration) ? player.duration.toFixed(2) : "?";
        diagLog(
          `chunk[${currentIndex}] ended 触发，实际播放耗时 ${elapsed}s（audio.duration=${dur}s）`
        );
        advanceAndPlay(sessionId);
      },
      { once: true }
    );

    chrome.runtime.sendMessage({ type: "PLAYBACK_PROGRESS" });
    chrome.runtime.sendMessage({ type: "HIGHLIGHT_CHUNK", text: chunks[currentIndex] });
    if (currentIndex === 0) {
      chrome.runtime.sendMessage({ type: "AUDIO_STARTED" });
    }
  } catch (e) {
    if (e.name === "AbortError" || mySession !== sessionId) return;

    consecutiveFailures += 1;
    diagLog(`播放出错：${e.message}`, {
      nextIndex,
      consecutiveFailures,
      max: MAX_CONSECUTIVE_FAILURES,
    });

    // 本块再单独打一轮（pending 已删），避免一次抖动就整段停死
    if (consecutiveFailures < MAX_CONSECUTIVE_FAILURES && mySession === sessionId) {
      diagLog(
        `块 ${nextIndex} 失败，${CHUNK_RETRY_DELAY_MS}ms 后自动重试 (连续失败 ${consecutiveFailures}/${MAX_CONSECUTIVE_FAILURES})`
      );
      await new Promise((r) => setTimeout(r, CHUNK_RETRY_DELAY_MS));
      if (mySession !== sessionId) return;
      pending.set(
        nextIndex,
        fetchChunkWithRetry(
          chunks[nextIndex],
          currentVoice,
          `chunk[${nextIndex}]-auto`
        ).catch((err) => ({ __error: err }))
      );
      advanceAndPlay(mySession);
      return;
    }

    // 连续失败过多：通知 background，但可选跳过本块继续（减少“必须重按开始”）
    chrome.runtime.sendMessage({ type: "AUDIO_ERROR", error: e.message });

    // 跳过坏块，尝试后续内容（若你希望失败就彻底停，删掉下面这段即可）
    if (mySession === sessionId && nextIndex + 1 < chunks.length) {
      diagLog(`连续失败达上限，跳过 chunk[${nextIndex}]，尝试下一块`);
      currentIndex = nextIndex; // 视为已处理，前进
      consecutiveFailures = 0;
      ensurePrefetched();
      advanceAndPlay(mySession);
    }
  }
}

function appendChunks(newChunks, voice) {
  if (!newChunks || newChunks.length === 0) return;
  if (voice) currentVoice = voice;
  const shouldResume = ranDry;
  diagLog(
    `OFFSCREEN_APPEND 收到 ${newChunks.length} 块，ranDry=${ranDry} → shouldResume=${shouldResume}`,
    {
      currentIndex,
      chunksLenBefore: chunks.length,
      preview: newChunks.map((c) => textPreview(c)),
    }
  );
  ranDry = false;
  chunks = chunks.concat(newChunks);
  ensurePrefetched();
  if (shouldResume) {
    advanceAndPlay(sessionId);
  }
}

function retryCurrentChunk() {
  if (!player.paused && !player.ended && currentIndex >= 0 && player.src) {
    diagLog("retryCurrentChunk 被调用，但当前正在正常播放，判定为误触发，忽略");
    return;
  }
  const idx = currentIndex + 1;
  if (idx >= chunks.length) return;
  diagLog(`retryCurrentChunk 真正执行，重试 idx=${idx}`);
  consecutiveFailures = 0;
  pending.delete(idx);
  pending.set(
    idx,
    fetchChunkWithRetry(chunks[idx], currentVoice, `chunk[${idx}]-manual`).catch((e) => ({
      __error: e,
    }))
  );
  advanceAndPlay(sessionId);
}

function stopPlayback() {
  abortAllPending();
  sessionId++;
  srcEpoch++;
  ranDry = false;
  consecutiveFailures = 0;
  chunks = [];
  currentIndex = -1;
  pending = new Map();
  player.pause();
  player.removeAttribute("src");
  player.load();
  chrome.runtime.sendMessage({ type: "HIGHLIGHT_CHUNK", text: null });
}

function pausePlayback() {
  player.pause();
}

function resumePlayback() {
  player.play();
}

function setRate(rate) {
  currentRate = rate;
  player.playbackRate = rate;
}

function seekBy(delta) {
  if (isNaN(player.duration) || player.duration === 0) return;
  player.currentTime = Math.max(0, Math.min(player.duration, player.currentTime + delta));
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "OFFSCREEN_PLAY") {
    // 重开朗读前先走一遍 stopPlayback：它除了清空内部状态，还会发一条
    // HIGHLIGHT_CHUNK(text:null) 通知 content 侧重置高亮锚点（lastEndChar）。
    // 否则重开时 content 侧的锚点还停留在上一次朗读的结束位置，导致新 session
    // 的 chunk[0]（位于全文靠前处）永远匹配不到 → 高亮未命中。
    stopPlayback();
    playChunkList(message.chunks, message.voice, message.rate);
  } else if (message.type === "OFFSCREEN_RETRY") {
    retryCurrentChunk();
  } else if (message.type === "OFFSCREEN_APPEND") {
    appendChunks(message.chunks, message.voice);
  } else if (message.type === "OFFSCREEN_STOP") {
    stopPlayback();
  } else if (message.type === "OFFSCREEN_PAUSE") {
    pausePlayback();
  } else if (message.type === "OFFSCREEN_RESUME") {
    resumePlayback();
  } else if (message.type === "OFFSCREEN_SET_RATE") {
    setRate(message.rate);
  } else if (message.type === "OFFSCREEN_SEEK") {
    seekBy(message.delta);
  }
});
