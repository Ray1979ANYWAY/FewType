// ---- offscreen document 管理（平台无关，Play Books 和 Koodo 共用）----

export async function ensureOffscreenDocument() {
  const existing = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
  });
  if (existing.length > 0) return;

  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["AUDIO_PLAYBACK"],
    justification: "持续朗读电子书正文，需要独立于 popup 播放音频",
  });
}

// Chrome 会把长时间没有真的在播放声音的 offscreen document 强制关掉（防止扩展假装播放骗后台常驻资源）。
// 这里每次发送前都重新确认存在，发送失败就重建后重试，而不是假设"之前建过了就一直在"。
export async function sendToOffscreen(message, retries = 3, delayMs = 250) {
  for (let attempt = 0; attempt < retries; attempt++) {
    await ensureOffscreenDocument();
    try {
      await chrome.runtime.sendMessage(message);
      return true;
    } catch (e) {
      if (attempt === retries - 1) throw e;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
}
