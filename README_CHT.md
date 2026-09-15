Languages: [English](README.md) | [简体中文](README_ZH.md) | [繁體中文](README_CHT.md)

---

# 🎙️ FewType — 語音打字、長文轉語音與翻譯桌面助手

一個免費的 Windows 桌面助手，集語音打字（STT）、長文本轉語音（TTS）、翻譯與電子書朗讀於一體——基於 Tauri 重構。配套瀏覽器擴充功能為網頁電子書閱讀器（Google Play Books 與 Koodo Reader）提供高品質 TTS 朗讀。

### ✨ 核心功能

- **語音打字 / 語音聽寫（STT）** — 按快捷鍵說話，文字直接落在游標所在處——任意視窗通用。逐字模式忠實記錄你所說的內容，不經 LLM 改寫。
- **翻譯並上屏** — 確認原文、翻譯、直接貼上到任意視窗。支援可選翻譯風格（如「王家衛」風格），翻譯也可以有味道。
- **長文本轉語音（TTS）** — 輸入或貼上長文本，逐行試聽，生成自然神經語音，可匯出音訊檔。
- **電子書朗讀** — 配套擴充功能為網頁電子書閱讀器提供自然 TTS 朗讀：Google Play Books 與 Koodo Reader，支援自動翻頁與文字高亮同步。
- **多服務商** — 火山引擎、Groq 等，STT / LLM / TTS 可選；網路好時用快的，不好時用穩的。
- **注重隱私** — 日誌只記錄錯誤，轉寫原文一律不落地。

### 🖥️ 介面截圖

![FewType 主介面](docs/screenshots/main_en.png)

### 🚀 快速開始

1. 從 [Releases](https://github.com/Ray1979ANYWAY/VoxEcho/releases) 下載最新安裝版或便攜版。
2. 執行 FewType，開啟**設定**，填入服務商的 API Key。
3. 按下快捷鍵（可在設定中自訂——Ctrl+Win 或雙擊 Ctrl）說話，文字將輸入到游標所在處。
4. 需要電子書朗讀時，將 `FewType-extension` 資料夾作為「已解壓的擴充功能」載入到 Chrome/Edge——不讀電子書可略過此步。

### 📦 技術棧

- **前端** — Tauri 2 + React + TypeScript + Tailwind
- **後端** — Python（PyInstaller 單檔），本機 HTTP 服務 `127.0.0.1:5010`
- **擴充功能** — Chrome Manifest V3，自動適配 bridge 埠 5005/5010

### 🌍 介面語言

English · 简体中文 · 繁體中文——首次啟動跟隨系統語言，其他語言一律英文兜底。

### ⚠️ 說明與能力邊界

- 程式**未簽名**——首次安裝時 Windows SmartScreen 可能彈出警告，選擇「更多資訊 → 仍要執行」即可。
- 需要 **WebView2 執行階段**（大多數 Windows 10/11 系統已預裝）。
- 需要自備 **API Key**（STT / LLM 服務商）。
- 這是一個活躍維護的個人專案——能力可能隨版本變化。Bug 與建議歡迎提交到 [Issues](https://github.com/Ray1979ANYWAY/VoxEcho/issues)。

### 🔒 安全與隱私

- **你的 API Key 與風格 Prompt 永不存放在程式目錄**——統一保存在 `%APPDATA%\com.rayanyway.fewtype\bridge_config.json`。複製、壓縮、移動程式資料夾（或本倉庫）都不會帶走任何密鑰。
- 發佈包**不含任何設定檔**；內建安全預設值，首次啟動自動生成。
- 打包腳本若偵測到設定檔會以系統語言彈出警告並中止，密鑰不可能混入發佈包。
- **卸載時自動刪除應用資料（含 API Key）**。

### ☕ 支持專案

如果 FewType 對你有用，請考慮請我喝杯咖啡！

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/rayhu)
