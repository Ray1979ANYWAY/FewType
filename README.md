Languages: [English](README.md) | [简体中文](README_ZH.md) | [繁體中文](README_CHT.md)

---

# 🎙️ VoxEcho — Voice Typing, Long-text TTS & Translation Desktop Assistant

Voice typing (STT), ebook reading, long-text TTS and translation in one free Windows desktop assistant — rebuilt with Tauri. A companion browser extension adds high-quality TTS playback to web e-book readers (Google Play Books & Koodo Reader).

### ✨ Key Features

- **Voice Typing (STT)** — Press the hotkey, speak, and the text lands right where your cursor is — in any window. Verbatim mode faithfully records exactly what you said, no LLM rewriting.
- **Translate & Paste** — Confirm the original text, translate it, and paste it straight into any window. Optional translation styles (e.g. the "Karwai Wong" flavor) for a bit of character.
- **Long-text TTS** — Type or paste long text and generate natural neural speech with per-line preview, then export audio files.
- **E-book Reading** — The companion extension adds natural TTS playback to web e-book readers: Google Play Books & Koodo Reader, with auto-paging and visual text sync.
- **Multiple Providers** — Volcengine, Groq and more for STT / LLM / TTS; a fast provider when the network is good, a reliable one when it isn't.
- **Privacy-minded** — Logs keep errors only; voice transcripts are never recorded.

### 🖥️ Screenshot

![VoxEcho main UI](docs/screenshots/main_en.png)

### 🚀 Quick Start

1. Download the latest installer or portable zip from [Releases](https://github.com/Ray1979ANYWAY/VoxEcho/releases).
2. Run VoxEcho, open **Settings**, and fill in your provider API key.
3. Press your hotkey (set in Settings — Ctrl+Win or double-tap Ctrl) and speak; the text is typed where your cursor is.
4. For e-book reading, load the `VoxEcho-extension` folder as an unpacked extension in Chrome/Edge — skip this if you don't read e-books.

### 📦 Tech Stack

- **Frontend** — Tauri 2 + React + TypeScript + Tailwind
- **Backend** — Python (PyInstaller one-file), local HTTP API on `127.0.0.1:5010`
- **Extension** — Chrome Manifest V3, auto-adapts to bridge ports 5005/5010

### 🌍 Languages

English · 简体中文 · 繁體中文 — follows the system language on first launch, English fallback.

### ⚠️ Notes & Limitations

- The program is **unsigned** — Windows SmartScreen may warn on first install; choose "More info → Run anyway".
- Requires **WebView2 Runtime** (preinstalled on most Windows 10/11 systems).
- You bring your own **API key** for the STT / LLM providers.
- This is an actively maintained hobby project — capabilities may change. Bugs and suggestions are welcome in the [Issues](https://github.com/Ray1979ANYWAY/VoxEcho/issues) tab.

### ☕ Support the Project

If you find VoxEcho useful, consider buying me a coffee!

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/rayhu)
