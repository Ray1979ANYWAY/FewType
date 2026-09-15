/**
 * FewType 三语界面（简体中文 / 繁體中文 / English）
 * - I18nProvider 启动时读后端 ui_lang（localStorage 缓存），setLang 即时切换并写回后端
 * - useI18n() 返回 t(key, vars?)：{n} 占位替换
 * - 专有名词（平台名/热键名/语言选项）不翻译，语言下拉选项恒显示原语言
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { getConfig, updateConfig } from "./api";

export type Lang = "zh-CN" | "zh-TW" | "en-US";

const UI_LANG_KEY = "fewtype.ui_lang";
// 改名 VoxEcho→FewType 前的旧 key：升级后首次启动自动继承，避免用户语言设置丢失
const UI_LANG_KEY_LEGACY = "voxecho.ui_lang";

const dicts: Record<Lang, Record<string, string>> = {
  /* ================================================================ 简体中文 */
  "zh-CN": {
    // App / TopBar
    "app.title": "FewType 语音处理平台",
    "app.title_suffix": "语音处理平台",
    "app.view_voice": "语音输入",
    "app.view_ebook": "电子书朗读",
    "app.view_tts": "长文本转语音",
    "app.view_settings": "设置",
    "app.local_service": "本地服务",
    "app.log_open": "查看运行日志",
    "app.log_close": "收起运行日志",
    "app.log_title": "运行日志 (System Logs)",
    "app.log_empty": "暂无日志…",
    "app.window_minimize": "最小化",
    "app.window_close": "关闭",

    // 语音输入
    "voice.hint": "在任意文本框激活快捷键，即刻语音转文字",
    "voice.no_mic": "需要插麦克风",
    "voice.no_mic_title": "未检测到麦克风，请插入后重试",
    "voice.hold_hint": "按住说话，松开自动上屏",
    "voice.online": "服务在线",
    "voice.offline": "服务离线",
    "voice.restart": "重启服务",
    "voice.restart_title": "如果悬浮状态条（HUD）出现假死，可点击重启语音服务",
    "voice.mode_verbatim": "忠实记录",
    "voice.mode_fluent": "智能润色",
    "voice.mode_formal": "严肃文档",
    "voice.mode_custom": "自定义风格",
    "voice.desc_verbatim": "只加标点，保留所有口头废话",
    "voice.desc_fluent": "去除口头废话，句子更通顺",
    "voice.desc_formal": "改写为正式、规范的文档",
    "voice.desc_custom": "使用你自己的 Prompt 作为风格积木",
    "voice.style_placeholder": "选择自定义风格…",
    "voice.manage_styles": "配置风格…",
    "voice.auto_commit": "自动上屏到光标所在文本框（Ctrl+V）",
    "voice.auto_translate": "自动翻译",
    "voice.mode_note": "「忠实记录」无额外提示词干预，转换效率最高；其余模式均含风格化重写。",
    "voice.double_ctrl": "双击 Ctrl",

    // 电子书朗读
    "ebook.checking": "检测扩展状态…",
    "ebook.ready": "Chrome 扩展服务已就绪",
    "ebook.load": "请加载 Chrome 扩展",
    "ebook.btn": "Chrome 扩展",
    "ebook.support": "支持",
    "ebook.platform_google": "Google Play 图书",
    "ebook.platform_koodo": "Koodo Reader",
    "ebook.missing":
      "未检测到扩展心跳。请打开 chrome://extensions → 开启「开发者模式」→「加载已解压的扩展程序」→ 选择 FewType-extension 文件夹",
    "ebook.guide_title": "快速使用指南",
    "ebook.guide_1": "本程序保持运行（托盘后台驻留）",
    "ebook.guide_2": "在网页端阅读器中 鼠标划选文本，即可自动朗读",
    "ebook.guide_3": "暂停 / 恢复快捷键：按 .（句号键，主键盘与小键盘均可）",
    "ebook.footer": "划选朗读由 Chrome 扩展完成，本程序仅需保持后台运行",

    // 长文本转语音
    "tts.input_title": "输入文本",
    "tts.input_placeholder": "粘贴文本，输入与输出语言不一致时自动翻译，用任意音色生成音频",
    "tts.output_lang": "输出语言",
    "tts.rate": "语速",
    "tts.voice": "音色",
    "tts.volume": "音量",
    "tts.rate_title": "可下拉选择或手动输入数字，如 15 / -20 / 30%",
    "tts.volume_title": "音量提升（edge-tts prosody）",
    "tts.note": "输入与输出语言一致时自动跳过翻译 · 音色为实时抓取，可能需要等待",
    "tts.expand_edit": "放大编辑",
    "tts.collapse": "完成并收起",
    "tts.done": "完成",
    "tts.char_count": "字符",
    "tts.output_title": "输出预览",
    "tts.output_placeholder": "点击「预览文本」生成翻译结果，可手动编辑…",
    "tts.preview": "预览文本",
    "tts.listen": "试听 3 秒",
    "tts.generate": "生成音频",
    "tts.pick": "指定",
    "tts.pick_title": "指定输出文件夹",
    "tts.open": "打开",
    "tts.open_title": "打开输出文件夹",
    "tts.ready": "就绪",
    "tts.voices_loaded": "已加载 {n} 个音色",
    "tts.status_input_first": "请先输入或粘贴文本",
    "tts.status_voice_first": "请先选择音色",
    "tts.status_translate_first": "输入与输出语言不一致，请先点「预览文本」完成翻译，确认后再生成",
    "tts.status_provider_first": "需要 LLM 翻译，请先在弹出的窗口中配置服务密钥 (API Key)",
    "tts.status_listen_translate_first": "输入与输出语言不一致，请先点「预览文本」完成翻译，再试听",
    "tts.status_translating": "翻译中…",
    "tts.status_translated": "翻译完成，可编辑后生成音频",
    "tts.status_translate_failed": "翻译失败: {msg}",
    "tts.status_no_translate": "输入与输出语言一致，无需翻译，可直接生成",
    "tts.status_speaking": "试听合成中…: {text}",
    "tts.status_listening": "试听中（3 秒）: {text}",
    "tts.status_speak_failed": "试听失败: {msg}",
    "tts.status_generating": "正在合成（长文本自动分段拼接）…",
    "tts.status_generated": "已生成 {seg} 段 / {kb} KB",
    "tts.status_generate_failed": "生成失败: {msg}",
    "tts.status_dir_missing": "目录不存在: {dir}",
    "tts.status_dir_picked": "输出文件夹已指定: {dir}",
    "tts.status_voices_failed": "音色加载失败（后端未启动？）",
    "tts.busy": "处理中…",

    // 设置
    "settings.general": "通用偏好",
    "settings.ui_lang": "界面语言",
    "settings.autostart": "开机时自动启动本程序（常驻）",
    "settings.service": "语音服务",
    "settings.provider": "服务提供商",
    "settings.advanced": "高级配置",
    "settings.hotkey": "语音输入快捷键",
    "settings.customize": "自定义",
    "settings.about": "关于",
    "settings.not_configured": "未配置",
    "settings.loading": "加载中…",
    "settings.load_failed": "配置加载失败: {msg}",
    "settings.saved": "已保存",

    // Provider 配置
    "provider.title": "语音服务配置",
    "provider.notice_missing_key": "当前未绑定 服务密钥 (API Key)，请在此填入以激活转写服务。",
    "provider.platform": "平台",
    "provider.get_key": "获取 API Key",
    "provider.hint_groq": "国内需代理 · 海外直连",
    "provider.hint_siliconflow": "国内直连",
    "provider.hint_volcengine": "国内直连（ASR + ARK 两个 Key）",
    "provider.hint_custom": "自定义托管平台",
    "provider.asr_url_builtin": "ASR 地址（已内置）",
    "provider.asr_url_custom": "ASR 地址 (base_url)",
    "provider.asr_url": "平台地址（已内置）",
    "provider.asr_key": "ASR API Key",
    "provider.asr_model": "ASR 模型",
    "provider.asr_model_placeholder": "选择或粘贴 ASR 模型…",
    "provider.llm_key_volc": "方舟 LLM Key",
    "provider.llm_key": "LLM API Key",
    "provider.llm_url": "LLM 地址 (llm_base_url)（留空与 ASR 共用）",
    "provider.llm_model": "LLM 模型",
    "provider.llm_model_placeholder": "选择或粘贴 LLM 模型…",
    "provider.paste": "粘贴",
    "provider.fetch": "抓取模型",
    "provider.test": "测试连接",
    "provider.save": "保存",
    "provider.close": "关闭",
    "provider.volc_url_note": "（WebSocket 流式，无需配置）",
    "provider.msg_key_first": "请先申请并粘贴 API Key（点击上方注册链接）",
    "provider.msg_saved": "语音服务配置已保存",
    "provider.msg_save_failed": "保存失败: {msg}",
    "provider.msg_paste_empty": "剪贴板中没有可用的 Key（已自动清洗）",
    "provider.msg_pasted": "已粘贴并清洗 API Key",
    "provider.msg_paste_err": "无法读取剪贴板，请手动粘贴",
    "provider.msg_fetching": "抓取模型列表中…",
    "provider.msg_fetched": "模型列表已刷新",
    "provider.msg_fetch_failed": "刷新失败: {msg}",
    "provider.msg_testing": "测试中…",
    "provider.msg_load_failed": "配置加载失败: {msg}",
    "provider.plat_groq": "Groq（推荐！）",
    "provider.plat_volcengine": "火山引擎 Volcengine",
    "provider.plat_siliconflow": "硅基流动 SiliconFlow",
    "provider.plat_custom": "自定义托管平台",

    // 快捷键弹窗
    "hotkey.title": "自定义快捷键",
    "hotkey.hint": "选择触发方式，保存后立即生效。",
    "hotkey.mode_combo": "自定义组合键",
    "hotkey.mode_combo_placeholder": "在此按下组合键…",
    "hotkey.mode_combo_note": "点击框内，按下组合键即预览",
    "hotkey.mode_double": "双击长按 Control",
    "hotkey.mode_double_note": "第二下按住讲话，松开自动发送。",
    "hotkey.need_modifier": "请至少包含一个修饰键（Ctrl / Alt / Win / Shift）",
    "hotkey.saved": "已保存，立即生效",
    "hotkey.save_failed": "保存失败: {msg}",
    "hotkey.load_failed": "加载失败: {msg}",
    "hotkey.save": "保存",

    // 关于
    "about.title": "关于 FewType",
    "about.subtitle": "语音输入 · 电子书朗读 · 长文本转语音",
    "about.made_by": "Made by Ray",
    "about.github": "GitHub 仓库",
    "about.coffee": "赞助一杯咖啡 ☕",
    "about.footer": "引擎：Python FastAPI · 界面：React + Tailwind（Tauri 壳开发中）",

    // 风格管理
    "style.title": "自定义风格配置",
    "style.list": "风格列表（最多 {n} 个）",
    "style.max": "最多 {n} 个自定义风格",
    "style.add": "新增",
    "style.rename": "重命名",
    "style.delete": "删除",
    "style.confirm_delete": "删除该风格？",
    "style.unnamed": "(未命名)",
    "style.name_placeholder": "输入风格名…",
    "style.prompt_label": "Prompt（你的风格积木）",
    "style.prompt_placeholder_sel": "输入该风格的 Prompt…",
    "style.prompt_placeholder_none": "先在左侧选择一个风格，再编辑其 Prompt…",
    "style.prompt_note": "系统会自动拼接「保留原语言 / 翻译」指令，无需在此重复。",
    "style.saved": "自定义风格已保存",
    "style.save_failed": "保存失败: {msg}",
    "style.load_failed": "加载失败: {msg}",
    "style.save": "保存",

    // HUD
    "hud.recording": "录音中",
    "hud.transcribing": "转写中",
    "hud.polishing": "润色中",
    "hud.confirming": "请确认原文",
    "hud.finalizing": "正在收尾",
    "hud.error": "出错",
    "hud.idle": "空闲",
    "hud.committed": "已上屏",
    "hud.confirm_title": "请确认原文（可修改）",
    "hud.confirm_submit": "翻译并上屏",
    "hud.confirm_cancel": "取消",
  },

  /* ================================================================ 繁體中文 */
  "zh-TW": {
    "app.title": "FewType 語音處理平台",
    "app.title_suffix": "語音處理平台",
    "app.view_voice": "語音輸入",
    "app.view_ebook": "電子書朗讀",
    "app.view_tts": "長文本轉語音",
    "app.view_settings": "設定",
    "app.local_service": "本地服務",
    "app.log_open": "查看運行日誌",
    "app.log_close": "收起運行日誌",
    "app.log_title": "運行日誌 (System Logs)",
    "app.log_empty": "暫無日誌…",
    "app.window_minimize": "最小化",
    "app.window_close": "關閉",

    "voice.hint": "在任意文字框啟動快捷鍵，即刻語音轉文字",
    "voice.no_mic": "需要插麥克風",
    "voice.no_mic_title": "未偵測到麥克風，請插入後重試",
    "voice.hold_hint": "按住說話，鬆開自動上屏",
    "voice.online": "服務在線",
    "voice.offline": "服務離線",
    "voice.restart": "重啟服務",
    "voice.restart_title": "如果懸浮狀態條（HUD）出現假死，可點擊重啟語音服務",
    "voice.mode_verbatim": "忠實記錄",
    "voice.mode_fluent": "智能潤色",
    "voice.mode_formal": "嚴肅文檔",
    "voice.mode_custom": "自定義風格",
    "voice.desc_verbatim": "只加標點，保留所有口頭廢話",
    "voice.desc_fluent": "去除口頭廢話，句子更通順",
    "voice.desc_formal": "改寫為正式、規範的文檔",
    "voice.desc_custom": "使用你自己的 Prompt 作為風格積木",
    "voice.style_placeholder": "選擇自定義風格…",
    "voice.manage_styles": "配置風格…",
    "voice.auto_commit": "自動上屏到光標所在文字框（Ctrl+V）",
    "voice.auto_translate": "自動翻譯",
    "voice.mode_note": "「忠實記錄」無額外提示詞干預，轉換效率最高；其餘模式均含風格化重寫。",
    "voice.double_ctrl": "雙擊 Ctrl",

    "ebook.checking": "偵測擴充功能狀態…",
    "ebook.ready": "Chrome 擴充功能服務已就緒",
    "ebook.load": "請載入 Chrome 擴充功能",
    "ebook.btn": "Chrome 擴充功能",
    "ebook.support": "支援",
    "ebook.platform_google": "Google Play 圖書",
    "ebook.platform_koodo": "Koodo Reader",
    "ebook.missing":
      "未偵測到擴充功能心跳。請開啟 chrome://extensions → 開啟「開發者模式」→「載入已解壓縮的擴充功能」→ 選擇 FewType-extension 資料夾",
    "ebook.guide_title": "快速使用指南",
    "ebook.guide_1": "本程式保持運行（托盤後台駐留）",
    "ebook.guide_2": "在網頁端閱讀器中 滑鼠劃選文字，即可自動朗讀",
    "ebook.guide_3": "暫停 / 恢復快捷鍵：按 .（句號鍵，主鍵盤與小鍵盤均可）",
    "ebook.footer": "劃選朗讀由 Chrome 擴充功能完成，本程式僅需保持後台運行",

    "tts.input_title": "輸入文字",
    "tts.input_placeholder": "貼上文字，輸入與輸出語言不一致時自動翻譯，用任意音色生成音頻",
    "tts.output_lang": "輸出語言",
    "tts.rate": "語速",
    "tts.voice": "音色",
    "tts.volume": "音量",
    "tts.rate_title": "可下拉選擇或手動輸入數字，如 15 / -20 / 30%",
    "tts.volume_title": "音量提升（edge-tts prosody）",
    "tts.note": "輸入與輸出語言一致時自動跳過翻譯 · 音色為即時抓取，可能需要等待",
    "tts.expand_edit": "放大編輯",
    "tts.collapse": "完成並收起",
    "tts.done": "完成",
    "tts.char_count": "字元",
    "tts.output_title": "輸出預覽",
    "tts.output_placeholder": "點擊「預覽文本」生成翻譯結果，可手動編輯…",
    "tts.preview": "預覽文本",
    "tts.listen": "試聽 3 秒",
    "tts.generate": "生成音頻",
    "tts.pick": "指定",
    "tts.pick_title": "指定輸出資料夾",
    "tts.open": "開啟",
    "tts.open_title": "開啟輸出資料夾",
    "tts.ready": "就緒",
    "tts.voices_loaded": "已載入 {n} 個音色",
    "tts.status_input_first": "請先輸入或貼上文字",
    "tts.status_voice_first": "請先選擇音色",
    "tts.status_translate_first": "輸入與輸出語言不一致，請先點「預覽文本」完成翻譯，確認後再生成",
    "tts.status_provider_first": "需要 LLM 翻譯，請先在彈出的視窗中設定服務金鑰 (API Key)",
    "tts.status_listen_translate_first": "輸入與輸出語言不一致，請先點「預覽文本」完成翻譯，再試聽",
    "tts.status_translating": "翻譯中…",
    "tts.status_translated": "翻譯完成，可編輯後生成音頻",
    "tts.status_translate_failed": "翻譯失敗: {msg}",
    "tts.status_no_translate": "輸入與輸出語言一致，無需翻譯，可直接生成",
    "tts.status_speaking": "試聽合成中…: {text}",
    "tts.status_listening": "試聽中（3 秒）: {text}",
    "tts.status_speak_failed": "試聽失敗: {msg}",
    "tts.status_generating": "正在合成（長文本自動分段拼接）…",
    "tts.status_generated": "已生成 {seg} 段 / {kb} KB",
    "tts.status_generate_failed": "生成失敗: {msg}",
    "tts.status_dir_missing": "目錄不存在: {dir}",
    "tts.status_dir_picked": "輸出資料夾已指定: {dir}",
    "tts.status_voices_failed": "音色載入失敗（後端未啟動？）",
    "tts.busy": "處理中…",

    "settings.general": "通用偏好",
    "settings.ui_lang": "介面語言",
    "settings.autostart": "開機時自動啟動本程式（常駐）",
    "settings.service": "語音服務",
    "settings.provider": "服務提供商",
    "settings.advanced": "高級配置",
    "settings.hotkey": "語音輸入快捷鍵",
    "settings.customize": "自定義",
    "settings.about": "關於",
    "settings.not_configured": "未配置",
    "settings.loading": "載入中…",
    "settings.load_failed": "配置載入失敗: {msg}",
    "settings.saved": "已儲存",

    "provider.title": "語音服務配置",
    "provider.notice_missing_key": "目前未綁定 服務金鑰 (API Key)，請在此填入以啟用轉寫服務。",
    "provider.platform": "平台",
    "provider.get_key": "獲取 API Key",
    "provider.hint_groq": "國內需代理 · 海外直連",
    "provider.hint_siliconflow": "國內直連",
    "provider.hint_volcengine": "國內直連（ASR + ARK 兩個 Key）",
    "provider.hint_custom": "自定義託管平台",
    "provider.asr_url_builtin": "ASR 位址（已內建）",
    "provider.asr_url_custom": "ASR 位址 (base_url)",
    "provider.asr_url": "平台位址（已內建）",
    "provider.asr_key": "ASR API Key",
    "provider.asr_model": "ASR 模型",
    "provider.asr_model_placeholder": "選擇或貼上 ASR 模型…",
    "provider.llm_key_volc": "方舟 LLM Key",
    "provider.llm_key": "LLM API Key",
    "provider.llm_url": "LLM 位址 (llm_base_url)（留空與 ASR 共用）",
    "provider.llm_model": "LLM 模型",
    "provider.llm_model_placeholder": "選擇或貼上 LLM 模型…",
    "provider.paste": "貼上",
    "provider.fetch": "抓取模型",
    "provider.test": "測試連線",
    "provider.save": "儲存",
    "provider.close": "關閉",
    "provider.volc_url_note": "（WebSocket 串流，無需配置）",
    "provider.msg_key_first": "請先申請並貼上 API Key（點擊上方註冊連結）",
    "provider.msg_saved": "語音服務配置已儲存",
    "provider.msg_save_failed": "儲存失敗: {msg}",
    "provider.msg_paste_empty": "剪貼簿中沒有可用的 Key（已自動清洗）",
    "provider.msg_pasted": "已貼上並清洗 API Key",
    "provider.msg_paste_err": "無法讀取剪貼簿，請手動貼上",
    "provider.msg_fetching": "抓取模型清單中…",
    "provider.msg_fetched": "模型清單已刷新",
    "provider.msg_fetch_failed": "刷新失敗: {msg}",
    "provider.msg_testing": "測試中…",
    "provider.msg_load_failed": "配置載入失敗: {msg}",
    "provider.plat_groq": "Groq（推薦！）",
    "provider.plat_volcengine": "火山引擎 Volcengine",
    "provider.plat_siliconflow": "矽基流動 SiliconFlow",
    "provider.plat_custom": "自定義託管平台",

    "hotkey.title": "自定義快捷鍵",
    "hotkey.hint": "選擇觸發方式，儲存後立即生效。",
    "hotkey.mode_combo": "自定義組合鍵",
    "hotkey.mode_combo_placeholder": "在此按下組合鍵…",
    "hotkey.mode_combo_note": "點擊框內，按下組合鍵即預覽",
    "hotkey.mode_double": "雙擊長按 Control",
    "hotkey.mode_double_note": "第二下按住講話，鬆開自動發送。",
    "hotkey.need_modifier": "請至少包含一個修飾鍵（Ctrl / Alt / Win / Shift）",
    "hotkey.saved": "已儲存，立即生效",
    "hotkey.save_failed": "儲存失敗: {msg}",
    "hotkey.load_failed": "載入失敗: {msg}",
    "hotkey.save": "儲存",

    "about.title": "關於 FewType",
    "about.subtitle": "語音輸入 · 電子書朗讀 · 長文本轉語音",
    "about.made_by": "Made by Ray",
    "about.github": "GitHub 倉庫",
    "about.coffee": "贊助一杯咖啡 ☕",
    "about.footer": "引擎：Python FastAPI · 介面：React + Tailwind（Tauri 殼開發中）",

    "style.title": "自定義風格配置",
    "style.list": "風格清單（最多 {n} 個）",
    "style.max": "最多 {n} 個自定義風格",
    "style.add": "新增",
    "style.rename": "重新命名",
    "style.delete": "刪除",
    "style.confirm_delete": "刪除此風格？",
    "style.unnamed": "(未命名)",
    "style.name_placeholder": "輸入風格名…",
    "style.prompt_label": "Prompt（你的風格積木）",
    "style.prompt_placeholder_sel": "輸入此風格的 Prompt…",
    "style.prompt_placeholder_none": "先在左側選擇一個風格，再編輯其 Prompt…",
    "style.prompt_note": "系統會自動拼接「保留原語言 / 翻譯」指令，無需在此重複。",
    "style.saved": "自定義風格已儲存",
    "style.save_failed": "儲存失敗: {msg}",
    "style.load_failed": "載入失敗: {msg}",
    "style.save": "儲存",

    "hud.recording": "錄音中",
    "hud.transcribing": "轉寫中",
    "hud.polishing": "潤色中",
    "hud.confirming": "請確認原文",
    "hud.finalizing": "正在收尾",
    "hud.error": "出錯",
    "hud.idle": "空閒",
    "hud.committed": "已上屏",
    "hud.confirm_title": "請確認原文（可修改）",
    "hud.confirm_submit": "翻譯並上屏",
    "hud.confirm_cancel": "取消",
  },

  /* ================================================================ English */
  "en-US": {
    "app.title": "FewType Voice Platform",
    "app.title_suffix": "Voice Platform",
    "app.view_voice": "Voice Input",
    "app.view_ebook": "E-book Reader",
    "app.view_tts": "Long-text TTS",
    "app.view_settings": "Settings",
    "app.local_service": "Local service",
    "app.log_open": "View run logs",
    "app.log_close": "Hide run logs",
    "app.log_title": "Run Logs",
    "app.log_empty": "No logs yet…",
    "app.window_minimize": "Minimize",
    "app.window_close": "Close",

    "voice.hint": "Press the hotkey in any text box and speak — it turns into text",
    "voice.no_mic": "Plug in a mic",
    "voice.no_mic_title": "No microphone detected. Plug one in and try again",
    "voice.hold_hint": "Hold to talk, release to commit",
    "voice.online": "Service online",
    "voice.offline": "Service offline",
    "voice.restart": "Restart service",
    "voice.restart_title": "If the floating status bar (HUD) freezes, click to restart the voice service",
    "voice.mode_verbatim": "Verbatim",
    "voice.mode_fluent": "Smart Polish",
    "voice.mode_formal": "Formal",
    "voice.mode_custom": "Custom Style",
    "voice.desc_verbatim": "Punctuation only, keep all filler words",
    "voice.desc_fluent": "Remove filler words, smoother sentences",
    "voice.desc_formal": "Rewrite into formal, well-structured text",
    "voice.desc_custom": "Use your own prompt as the style building block",
    "voice.style_placeholder": "Select style…",
    "voice.manage_styles": "Manage styles…",
    "voice.auto_commit": "Auto-commit to focused text box (Ctrl+V)",
    "voice.auto_translate": "Auto-translate",
    "voice.mode_note": "“Verbatim” runs with no extra prompt intervention for maximum speed; all other modes apply stylized rewriting.",
    "voice.double_ctrl": "Double-press Ctrl",

    "ebook.checking": "Checking extension…",
    "ebook.ready": "Chrome extension service ready",
    "ebook.load": "Load Chrome extension",
    "ebook.btn": "Chrome Extension",
    "ebook.support": "Supports",
    "ebook.platform_google": "Google Play Books",
    "ebook.platform_koodo": "Koodo Reader",
    "ebook.missing":
      "No extension heartbeat detected. Open chrome://extensions → enable \"Developer mode\" → \"Load unpacked\" → select the FewType-extension folder",
    "ebook.guide_title": "Quick Start Guide",
    "ebook.guide_1": "Keep this program running (tray resident)",
    "ebook.guide_2": "In the web reader, select text with the mouse to read it aloud",
    "ebook.guide_3": "Pause / resume hotkey: . (period — main & numpad)",
    "ebook.footer": "Selection reading is handled by the Chrome extension; this app just stays in the background",

    "tts.input_title": "Input Text",
    "tts.input_placeholder": "Paste text — auto-translated when input and output languages differ; generate audio with any voice",
    "tts.output_lang": "Language",
    "tts.rate": "Speed",
    "tts.voice": "Voice",
    "tts.volume": "Volume",
    "tts.rate_title": "Pick from list or type a number, e.g. 15 / -20 / 30%",
    "tts.volume_title": "Volume boost (edge-tts prosody)",
    "tts.note": "Skips translation when input and output languages match · voices are fetched live, may take a moment",
    "tts.expand_edit": "Expand editor",
    "tts.collapse": "Done & collapse",
    "tts.done": "Done",
    "tts.char_count": "chars",
    "tts.output_title": "Output Preview",
    "tts.output_placeholder": "Click “Preview” to translate, then edit…",
    "tts.preview": "Preview",
    "tts.listen": "Preview 3s",
    "tts.generate": "Generate Audio",
    "tts.pick": "Choose",
    "tts.pick_title": "Choose output folder",
    "tts.open": "Open",
    "tts.open_title": "Open output folder",
    "tts.ready": "Ready",
    "tts.voices_loaded": "{n} voices loaded",
    "tts.status_input_first": "Please enter or paste text first",
    "tts.status_voice_first": "Please choose a voice first",
    "tts.status_translate_first": "Input and output languages differ — click “Preview” to translate, then generate",
    "tts.status_provider_first": "LLM translation needed — configure the service API Key in the dialog",
    "tts.status_listen_translate_first": "Input and output languages differ — click “Preview” to translate first",
    "tts.status_translating": "Translating…",
    "tts.status_translated": "Translation done — edit if needed, then generate",
    "tts.status_translate_failed": "Translation failed: {msg}",
    "tts.status_no_translate": "Input and output languages match — no translation needed, generate directly",
    "tts.status_speaking": "Synthesizing preview…: {text}",
    "tts.status_listening": "Previewing (3s): {text}",
    "tts.status_speak_failed": "Preview failed: {msg}",
    "tts.status_generating": "Generating (long text auto-chunked)…",
    "tts.status_generated": "Done: {seg} segment(s) / {kb} KB",
    "tts.status_generate_failed": "Generation failed: {msg}",
    "tts.status_dir_missing": "Directory not found: {dir}",
    "tts.status_dir_picked": "Output folder set: {dir}",
    "tts.status_voices_failed": "Failed to load voices (backend down?)",
    "tts.busy": "Working…",

    "settings.general": "General",
    "settings.ui_lang": "UI Language",
    "settings.autostart": "Launch at startup (resident)",
    "settings.service": "Voice Service",
    "settings.provider": "Provider",
    "settings.advanced": "Advanced",
    "settings.hotkey": "Speech Hotkey",
    "settings.customize": "Customize",
    "settings.about": "About",
    "settings.not_configured": "Not configured",
    "settings.loading": "Loading…",
    "settings.load_failed": "Failed to load config: {msg}",
    "settings.saved": "Saved",

    "provider.title": "Voice Service Configuration",
    "provider.notice_missing_key": "No service API Key is bound yet — enter one here to activate transcription.",
    "provider.platform": "Platform",
    "provider.get_key": "Get API Key",
    "provider.hint_groq": "",
    "provider.hint_siliconflow": "Direct in China",
    "provider.hint_volcengine": "Direct in China (ASR + ARK two keys)",
    "provider.hint_custom": "Self-hosted platform",
    "provider.asr_url_builtin": "ASR URL (built-in)",
    "provider.asr_url_custom": "ASR URL (base_url)",
    "provider.asr_url": "Platform URL (built-in)",
    "provider.asr_key": "ASR API Key",
    "provider.asr_model": "ASR Model",
    "provider.asr_model_placeholder": "Select or paste ASR model…",
    "provider.llm_key_volc": "Ark LLM Key",
    "provider.llm_key": "LLM API Key",
    "provider.llm_url": "LLM URL (llm_base_url) (blank shares ASR URL)",
    "provider.llm_model": "LLM Model",
    "provider.llm_model_placeholder": "Select or paste LLM model…",
    "provider.paste": "Paste",
    "provider.fetch": "Fetch Models",
    "provider.test": "Test Connection",
    "provider.save": "Save",
    "provider.close": "Close",
    "provider.volc_url_note": "(WebSocket streaming, no config needed)",
    "provider.msg_key_first": "Please get and paste an API Key first (sign-up link above)",
    "provider.msg_saved": "Voice service configuration saved",
    "provider.msg_save_failed": "Save failed: {msg}",
    "provider.msg_paste_empty": "No usable key in clipboard (auto-sanitized)",
    "provider.msg_pasted": "Key pasted and sanitized",
    "provider.msg_paste_err": "Cannot read clipboard, paste manually",
    "provider.msg_fetching": "Fetching model list…",
    "provider.msg_fetched": "Model list refreshed",
    "provider.msg_fetch_failed": "Refresh failed: {msg}",
    "provider.msg_testing": "Testing…",
    "provider.msg_load_failed": "Failed to load config: {msg}",
    "provider.plat_groq": "Groq (Recommended!)",
    "provider.plat_volcengine": "Volcengine",
    "provider.plat_siliconflow": "SiliconFlow",
    "provider.plat_custom": "Custom Platform",

    "hotkey.title": "Custom Hotkey",
    "hotkey.hint": "Choose a trigger mode; takes effect immediately after saving.",
    "hotkey.mode_combo": "Custom Combination",
    "hotkey.mode_combo_placeholder": "Press combination here…",
    "hotkey.mode_combo_note": "Click the box and press the combination to preview",
    "hotkey.mode_double": "Double-press & Hold Control",
    "hotkey.mode_double_note": "Hold on the second press to talk; release to send.",
    "hotkey.need_modifier": "At least one modifier key required (Ctrl / Alt / Win / Shift)",
    "hotkey.saved": "Saved — takes effect immediately",
    "hotkey.save_failed": "Save failed: {msg}",
    "hotkey.load_failed": "Load failed: {msg}",
    "hotkey.save": "Save",

    "about.title": "About FewType",
    "about.subtitle": "Voice Input · E-book Reading · Long-text TTS",
    "about.made_by": "Made by Ray",
    "about.github": "GitHub Repository",
    "about.coffee": "Buy Me a Coffee ☕",
    "about.footer": "Engine: Python FastAPI · UI: React + Tailwind (Tauri shell in development)",

    "style.title": "Custom Style Configuration",
    "style.list": "Style list (max {n})",
    "style.max": "At most {n} custom styles",
    "style.add": "Add",
    "style.rename": "Rename",
    "style.delete": "Delete",
    "style.confirm_delete": "Delete this style?",
    "style.unnamed": "(unnamed)",
    "style.name_placeholder": "Enter style name…",
    "style.prompt_label": "Prompt (your style building block)",
    "style.prompt_placeholder_sel": "Enter the prompt for this style…",
    "style.prompt_placeholder_none": "Select a style on the left first, then edit its prompt…",
    "style.prompt_note": "The system appends “keep original language / translate” instructions automatically.",
    "style.saved": "Custom styles saved",
    "style.save_failed": "Save failed: {msg}",
    "style.load_failed": "Load failed: {msg}",
    "style.save": "Save",

    "hud.recording": "Recording",
    "hud.transcribing": "Transcribing",
    "hud.polishing": "Polishing",
    "hud.confirming": "Confirm text",
    "hud.finalizing": "Finalizing",
    "hud.error": "Error",
    "hud.idle": "Idle",
    "hud.committed": "Committed",
    "hud.confirm_title": "Confirm the original text (editable)",
    "hud.confirm_submit": "Translate & Commit",
    "hud.confirm_cancel": "Cancel",
  },
};

interface I18nCtxValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const I18nCtx = createContext<I18nCtxValue>({
  lang: "zh-CN",
  setLang: () => {},
  t: (k) => k,
});

/** 检测系统语言：zh 系按繁简分流，其他语言一律英文兜底 */
function detectSystemLang(): Lang {
  try {
    const raw = (navigator.language || (navigator.languages && navigator.languages[0]) || "en-US").toLowerCase();
    if (raw.startsWith("zh")) {
      return /(tw|hk|mo|hant)/.test(raw) ? "zh-TW" : "zh-CN";
    }
    if (raw.startsWith("en")) {
      return "en-US";
    }
  } catch {
    /* 忽略 */
  }
  return "en-US";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    try {
      const saved = localStorage.getItem(UI_LANG_KEY) ?? localStorage.getItem(UI_LANG_KEY_LEGACY);
      if (saved === "zh-CN" || saved === "zh-TW" || saved === "en-US") {
        return saved;
      }
      return detectSystemLang();
    } catch {
      return detectSystemLang();
    }
  });

  // 启动时与后端 ui_lang 对齐（后端未设置时保持系统检测结果，不持久化）
  useEffect(() => {
    let disposed = false;
    getConfig()
      .then((cfg) => {
        if (disposed) return;
        const ul = cfg.ui_lang;
        if (ul === "zh-CN" || ul === "zh-TW" || ul === "en-US") {
          // 后端有明确语言（用户手动设置过）→ 以后端为准
          setLangState(ul);
          try {
            localStorage.setItem(UI_LANG_KEY, ul);
          } catch {
            /* 忽略 */
          }
        }
      })
      .catch(() => {
        /* 后端不可用时沿用本地语言 */
      });
    return () => {
      disposed = true;
    };
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(UI_LANG_KEY, l);
    } catch {
      /* 忽略 */
    }
    updateConfig({ ui_lang: l }).catch(() => {
      /* 后端写失败不阻塞本地切换 */
    });
  }, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => {
      let s = dicts[lang][key] ?? dicts["zh-CN"][key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          s = s.replaceAll(`{${k}}`, String(v));
        }
      }
      return s;
    },
    [lang]
  );

  return (
    <I18nCtx.Provider value={{ lang, setLang, t }}>{children}</I18nCtx.Provider>
  );
}

export function useI18n(): I18nCtxValue {
  return useContext(I18nCtx);
}
