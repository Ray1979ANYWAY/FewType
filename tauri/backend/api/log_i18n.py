# -*- coding: utf-8 -*-
"""后端日志消息国际化：日志文案跟随 ui_lang（zh-CN / zh-TW / en-US）。

用法：logger.info(L("key", arg=value, ...))
ui_lang 变化时调用 set_lang() 刷新（见 main.py POST /api/config）。
未收录的 key 原样返回，不会崩。
"""
from __future__ import annotations

from typing import Any

_LANG_INDEX = {"zh-CN": 0, "zh-TW": 1, "en-US": 2}

# key: (简体中文, 繁體中文, English)
_MSGS: dict[str, tuple[str, str, str]] = {
    # autostart_util
    "autostart_skip": (
        "自启跳过：dev 模式或找不到主壳 exe",
        "自啟跳過：dev 模式或找不到主殼 exe",
        "Autostart skipped: dev mode or shell exe not found",
    ),
    "autostart_set": (
        "已设置开机启动 -> {target}",
        "已設定開機啟動 -> {target}",
        "Autostart enabled -> {target}",
    ),
    "autostart_clear": (
        "已取消开机启动",
        "已取消開機啟動",
        "Autostart disabled",
    ),
    "autostart_fail": (
        "设置开机启动失败: {err}",
        "設定開機啟動失敗: {err}",
        "Failed to enable autostart: {err}",
    ),
    # dialog_util
    "dialog_tk_unavailable": (
        "tkinter 不可用: {err}",
        "tkinter 不可用: {err}",
        "tkinter unavailable: {err}",
    ),
    "dialog_folder_fail": (
        "文件夹对话框失败: {err}",
        "資料夾對話方塊失敗: {err}",
        "Folder dialog failed: {err}",
    ),
    "dialog_open_fail": (
        "打开目录失败: {path}: {err}",
        "開啟目錄失敗: {path}: {err}",
        "Failed to open directory {path}: {err}",
    ),
    # hotkey_service
    "paste_fail": (
        "模拟 Ctrl+V 上屏失败: {err}",
        "模擬 Ctrl+V 上屏失敗: {err}",
        "Simulated Ctrl+V paste failed: {err}",
    ),
    "hook_install_fail": (
        "热键钩子安装失败: {combo}",
        "熱鍵鉤子安裝失敗: {combo}",
        "Hotkey hook install failed: {combo}",
    ),
    "hook_init_fail": (
        "热键钩子初始化失败 {combo}: {err}",
        "熱鍵鉤子初始化失敗 {combo}: {err}",
        "Hotkey hook init failed {combo}: {err}",
    ),
    "hotkey_enabled": (
        "全局热键已启用: {combo} ({mode})",
        "全域熱鍵已啟用: {combo} ({mode})",
        "Global hotkey enabled: {combo} ({mode})",
    ),
    "hotkey_busy": (
        "热键触发但已有会话在录，忽略",
        "熱鍵觸發但已有會話在錄，忽略",
        "Hotkey triggered while a session is recording; ignored",
    ),
    "provider_key_missing": (
        "Provider API Key 未配置，热键触发改为弹出 Provider 设置",
        "Provider API Key 未配置，熱鍵觸發改為彈出 Provider 設定",
        "Provider API Key not configured; opening Provider settings instead",
    ),
    "hotkey_recording": (
        "热键开始录音 mode={mode} style={style}: {ok}",
        "熱鍵開始錄音 mode={mode} style={style}: {ok}",
        "Hotkey recording started mode={mode} style={style}: {ok}",
    ),
    "autocommit_off": (
        "自动上屏已关闭，仅写入剪贴板",
        "自動上屏已關閉，僅寫入剪貼簿",
        "Auto-commit off; wrote to clipboard only",
    ),
    "clipboard_fail": (
        "剪贴板写入失败: {err}",
        "剪貼簿寫入失敗: {err}",
        "Clipboard write failed: {err}",
    ),
    # main
    "voices_fail": (
        "获取音色清单失败: {type}: {err}",
        "取得音色清單失敗: {type}: {err}",
        "Failed to fetch voices: {type}: {err}",
    ),
    "tts_fail": (
        "TTS 合成失败: {type}: {err}",
        "TTS 合成失敗: {type}: {err}",
        "TTS synthesis failed: {type}: {err}",
    ),
    "speak_fail": (
        "合成失败: {type}: {err}",
        "合成失敗: {type}: {err}",
        "Synthesis failed: {type}: {err}",
    ),
    "translate_fail": (
        "翻译失败: {type}: {err}",
        "翻譯失敗: {type}: {err}",
        "Translation failed: {type}: {err}",
    ),
    "test_fail": (
        "连接测试失败: {type}: {err}",
        "連線測試失敗: {type}: {err}",
        "Connection test failed: {type}: {err}",
    ),
    "fetch_fail": (
        "模型抓取失败: {type}: {err}",
        "模型抓取失敗: {type}: {err}",
        "Model fetch failed: {type}: {err}",
    ),
    "config_updated": (
        "配置已更新: {keys}",
        "配置已更新: {keys}",
        "Config updated: {keys}",
    ),
    "speech_restarted": (
        "语音服务已由用户手动重启",
        "語音服務已由使用者手動重啟",
        "Speech service restarted by user",
    ),
    "speech_confirmed": (
        "已确认原文，开始翻译",
        "已確認原文，開始翻譯",
        "Text confirmed, translating",
    ),
    "workarea_fail": (
        "获取工作区失败: {err}",
        "取得工作區失敗: {err}",
        "Failed to get work area: {err}",
    ),
    "api_start": (
        "FewType API 启动: http://{host}:{port} (文档 /api/docs)",
        "FewType API 啟動: http://{host}:{port} (文件 /api/docs)",
        "FewType API started: http://{host}:{port} (docs /api/docs)",
    ),
    # speech_service 状态与日志
    "speech_aborted": (
        "已中止当前任务",
        "已中止目前任務",
        "Task aborted",
    ),
    "listening": (
        "聆听中…",
        "聆聽中…",
        "Listening…",
    ),
    "listening_start": (
        "开始聆听",
        "開始聆聽",
        "Listening started",
    ),
    "transcribing": (
        "转写中…",
        "轉寫中…",
        "Transcribing…",
    ),
    "polishing": (
        "润色中…",
        "潤飾中…",
        "Polishing…",
    ),
    "confirm_prompt": (
        "请确认原文",
        "請確認原文",
        "Please confirm the text",
    ),
    "confirm_cancel": (
        "已取消翻译上屏（未确认原文）",
        "已取消翻譯上屏（未確認原文）",
        "Translation cancelled (text not confirmed)",
    ),
    "lang_same_skip": (
        "语言一致，跳过翻译与确认，直接上屏",
        "語言一致，跳過翻譯與確認，直接上屏",
        "Same language detected; skipping translation & confirmation",
    ),
    "commit_len": (
        "上屏 {n} 字",
        "上屏 {n} 字",
        "Committed {n} chars",
    ),
    "committed": (
        "已上屏",
        "已上屏",
        "Committed",
    ),
    "idle": (
        "空闲",
        "閒置",
        "Idle",
    ),
    "fail_generic": (
        "失败: {err}",
        "失敗: {err}",
        "Failed: {err}",
    ),
    "asr_no_result": (
        "火山识别无返回结果（可能音频过短或静音）",
        "火山辨識無返回結果（可能音訊過短或靜音）",
        "No ASR result (audio too short or silent)",
    ),
    "no_speech": (
        "未检测到语音（录音过短或静音）",
        "未偵測到語音（錄音過短或靜音）",
        "No speech detected (recording too short or silent)",
    ),
    # main / ws
    "api_key_missing": (
        "未填写 API Key",
        "未填寫 API Key",
        "API Key is required",
    ),
    "ws_invalid_start": (
        "start 参数不合法",
        "start 參數不合法",
        "Invalid start parameters",
    ),
    "ws_already_recording": (
        "已在录制（另一个会话）",
        "已在錄製（另一個會話）",
        "Already recording (another session)",
    ),
    "ws_started": (
        "开始录音",
        "開始錄音",
        "Recording started",
    ),
    # tts_service
    "proxy_followed": (
        "已跟随系统代理: {proxy}",
        "已跟隨系統代理: {proxy}",
        "Following system proxy: {proxy}",
    ),
    "proxy_none": (
        "未检测到系统代理，外部网络保持直连",
        "未偵測到系統代理，外部網路保持直連",
        "No system proxy detected; external requests stay direct",
    ),
    # speech_service
    "stt_fail": (
        "STT 流程失败: {type}: {err}",
        "STT 流程失敗: {type}: {err}",
        "STT pipeline failed: {type}: {err}",
    ),
    # tts_service
    "voices_refresh": (
        "刷新音色清单: {n} voices / {locales} locales",
        "重新整理音色清單: {n} voices / {locales} locales",
        "Refreshed voices: {n} voices / {locales} locales",
    ),
    "synth_retry": (
        "第 {attempt}/{max} 次合成失败: {type}: {err}",
        "第 {attempt}/{max} 次合成失敗: {type}: {err}",
        "Synthesis attempt {attempt}/{max} failed: {type}: {err}",
    ),
    "seg_fail": (
        "第 {n} 段合成失败: {type}: {err}",
        "第 {n} 段合成失敗: {type}: {err}",
        "Segment {n} synthesis failed: {type}: {err}",
    ),
    "tts_file": (
        "TTS 文件生成: {fname} ({segments} 段, {bytes} bytes)",
        "TTS 檔案生成: {fname} ({segments} 段, {bytes} bytes)",
        "TTS file generated: {fname} ({segments} seg, {bytes} bytes)",
    ),
}

_CUR: str = "zh-CN"


def set_lang(lang: str) -> None:
    global _CUR
    if lang in _LANG_INDEX:
        _CUR = lang


def L(key: str, **kw: Any) -> str:
    tpls = _MSGS.get(key)
    if not tpls:
        return key
    tpl = tpls[_LANG_INDEX.get(_CUR, 0)]
    try:
        return tpl.format(**kw)
    except Exception:
        return tpl
