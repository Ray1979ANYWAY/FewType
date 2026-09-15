# -*- coding: utf-8 -*-
import json

body = """# VoxEcho 2.0.1 (tkinter edition)

![Main UI](https://raw.githubusercontent.com/Ray1979ANYWAY/VoxEcho/v2.0.1/docs/screenshots/main_1.png)
![TTS Panel](https://raw.githubusercontent.com/Ray1979ANYWAY/VoxEcho/v2.0.1/docs/screenshots/main_tts.png)

Lightweight tkinter edition — one free program for voice typing (STT), e-book read-aloud, and long-form text-to-speech (TTS). Maintained alongside the Tauri edition (3.1.6).

## ✨ What's New
- NEW: STT "confirm original then translate" window — review/edit the transcript, press Enter to translate & auto-commit, ESC to cancel; height adapts to text, no taskbar entry
- NEW: Focus-restore hardening — the translation is pasted back to the cursor position where you were recording
- CHANGE: Preset style renamed to "Karwai Wong" (old configs migrate automatically)
- CHANGE: Browser extension auto-detects ports 5005/5010
- FIX: onedir build bundles audio DLLs and version info

## 📦 Assets
- `VoxEcho-2.0.1-tk-win64.zip` — portable, includes the browser extension and README

## ⚠️ Notes
- Unsigned build: SmartScreen / antivirus may warn — add to whitelist to run
- Do NOT run together with the Tauri edition (3.1.6)
- Depends on external services (Microsoft TTS / Groq / LLM) — subject to provider policies

---

# VoxEcho 2.0.1（tkinter 版）

轻量精简版：一个程序免费解决语音打字（STT）、电子书朗读、长文本转语音（TTS）。与 Tauri 版（3.1.6）并行维护。

## ✨ 本次更新
- 新增：STT「确认原文再翻译」窗口——回车上屏 / ESC 取消，高度随文本自适应，不占任务栏
- 新增：上屏焦点恢复强化——翻译结果自动粘贴回录音前的光标位置
- 调整：预制风格更名 Karwai Wong（旧配置自动迁移）
- 调整：浏览器扩展自动适配端口 5005/5010
- 修复：onedir 打包补全音频 DLL 与版本信息

## 📦 附件
- `VoxEcho-2.0.1-tk-win64.zip` — 免安装版，含浏览器扩展与使用说明

## ⚠️ 注意
- 未签名：SmartScreen / 杀软可能提示，加入白名单即可
- 与 Tauri 版（3.1.6）二选一，勿同时运行
- 依赖外部服务（微软 TTS / Groq / LLM），受供应方政策影响"""

payload = {
    "tag_name": "v2.0.1",
    "target_commitish": "tkinter-legacy",
    "name": "VoxEcho 2.0.1 (tkinter edition)",
    "body": body,
    "draft": False,
    "prerelease": False,
}

with open(r"D:\Documents\VoxEcho-tk\release_payload.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
print("payload written, body chars:", len(body))
