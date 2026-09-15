Languages: [English](README.md) | [简体中文](README_ZH.md) | [繁體中文](README_CHT.md)

---

# 📖 FewType

One program, free of charge, for voice typing, e-book read-aloud, and long-form text-to-speech.

### 💡 Why FewType

This project grew out of my own reading habit. I found that taking in text and voice at the same time kept me far more focused and let me read for longer — and small interruptions, like getting up for a glass of water, no longer broke my concentration.

The problem was that e-book platforms' built-in read-aloud support left a lot to be desired, while Microsoft's public TTS API sounded surprisingly good. So the first version was a Chrome extension that reads web e-books aloud (which, of course, means reading on your computer in a browser). Since I wanted everything to go through a local relay, I also wrote a small backend that runs on your machine.

Later, my conversations with AI and my writing both called for heavy voice-to-text input, so I merged the two into a single program. It eventually grew into a voice platform with three parts:

- **E-book read-aloud**: a Chrome extension plus a local relay, using Microsoft's free neural voices
- **STT voice typing**: type with your voice instead of the keyboard (Groq API by default)
- **TTS text-to-speech**: turn long-form text into speech

E-book reading and TTS use Microsoft's free voices; STT uses the Groq API by default. You can also bring your own API key, URL, and model for any of them. At personal-usage levels, these services are effectively free.

There are two versions of FewType:
- **2.0**: built with tkinter — light and minimal
- **3.0**: built with Tauri — a more modern experience

### 🌟 Key Features

- **STT voice typing**: speak instead of typing
- **E-book read-aloud**: web e-books read aloud with Microsoft voices
- **TTS text-to-speech**: long-form text to speech

### ⚠️ Limitations & Dependencies

FewType is a local relay — it doesn't depend on any cloud server of ours. However, some features rely on external services and may be affected by their availability and policies:

- **E-book read-aloud / TTS**: uses Microsoft's free voice service. Microsoft may change its API, rate limits, or policies, which could affect these features
- **STT voice typing**: uses the Groq API by default, which requires your own API key. Pricing, rate limits, and model changes from the provider can affect usage
- **Translation / polishing / styling**: uses the LLM you configure in Settings (e.g. Groq, Volcengine), also subject to provider API changes

This is a personal project maintained in my spare time — I'll do my best, but support is best-effort. If you run into issues or have suggestions, please open an [issue](https://github.com/Ray1979ANYWAY/FewType/issues).

### 🛡️ Unsigned Build Notice

FewType is not code-signed yet, so you may run into the following when installing or running it:

- **Windows SmartScreen**: on first launch you may see "Windows protected your PC" (unknown publisher). Click "More info" → "Run anyway"
- **Antivirus false positives**: unsigned executables can be flagged by Windows Defender or other AV software. Add the program to your whitelist if this happens
- **Browser extension**: the extension is not on the Chrome Web Store, so you load it manually (developer mode); the browser may warn about risk — this is expected

The source code is open (MIT licensed), so you're welcome to review it before running.

### 🔒 Security & Privacy

- **Your API key and style prompts never live in the program folder** — they are stored in `%APPDATA%\FewType\bridge_config.json`. Copying, zipping or moving the program folder (or this repository) carries no secrets.
- The release package ships **without any config file**; safe defaults are built in and generated on first launch.
- Packaging scripts abort with a system-language warning if a config file is detected, so secrets can never sneak into a release build.
- **Uninstall**: run `uninstall.bat` in the program folder (removes local config, API key included), or manually delete the program folder and `%APPDATA%\FewType`.

### ☕ Support the Project

If you find FewType helpful, consider buying me a coffee!

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/rayhu)
