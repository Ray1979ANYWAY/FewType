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
        "试听合成失败: {type}: {err}",
        "試聽合成失敗: {type}: {err}",
        "Preview synthesis failed: {type}: {err}",
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
        "VoxEcho API 启动: http://{host}:{port} (文档 /api/docs)",
        "VoxEcho API 啟動: http://{host}:{port} (文件 /api/docs)",
        "VoxEcho API started: http://{host}:{port} (docs /api/docs)",
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
