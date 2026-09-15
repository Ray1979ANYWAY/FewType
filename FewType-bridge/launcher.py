# -*- coding: utf-8 -*-
"""
ebooks-tts bridge launcher
Left controls + right terminal log + tray
UI language: zh-CN / zh-TW / en / es / ja / ko (auto from system)
"""
from __future__ import annotations

import base64
import json
import locale
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests

# TTS 连接复用：用 requests.Session 保持长连接，减少每次合成的连接建立开销
# 为什么用 Session 而不是每次 urllib.request：本地连接虽然快，但 TCP 握手+HTTP 头
# 每次都要重新发，Session 复用连接后第一次之后的请求能省几毫秒到几十毫秒。
_tts_session = requests.Session()
_tts_session.headers.update({"Content-Type": "application/json"})


def _warmup_tts_connection():
    """启动预热：发一个轻量 GET 请求到本地服务，让 TCP 连接提前建立好。
    这样第一次点"生成音频"时不用再等 TCP 握手，能省几十毫秒。
    失败也无所谓（服务还没启动时会静默失败）。
    """
    try:
        _tts_session.get("http://%s:%d/health" % (HOST, PORT), timeout=3)
    except Exception:
        pass  # 服务还没启动，忽略

import tkinter as tk

from provider import PROVIDERS, PlatformDef, Provider, ProviderError, sanitize_api_key
import stt_engine


def _show_fewtype_dialog(parent, title, message):
    """FewType 统一风格的信息弹窗（暗色主题 + 翡翠绿强调色）。
    替代系统默认的 messagebox.showinfo，保持整体 UI 审美一致。
    """
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg="#090D0A")
    win.resizable(False, False)
    win.overrideredirect(False)
    # 计算居中位置
    win.update_idletasks()
    w, h = 380, 170
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    win.geometry(f"{w}x{h}+{x}+{y}")

    # 卡片容器（模拟圆角卡片效果）
    card = tk.Frame(win, bg="#121A15", bd=0)
    card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    # 标题（翡翠绿，加粗）
    tk.Label(card, text=title, bg="#121A15", fg="#10B981",
             font=("Microsoft YaHei UI", 11, "bold")).pack(pady=(18, 10))

    # 内容（浅灰，自动换行，居中）
    tk.Label(card, text=message, bg="#121A15", fg="#D1D5DB",
             font=("Microsoft YaHei UI", 9), wraplength=320,
             justify="center").pack(pady=(0, 18), padx=24)

    # 确定按钮（翡翠绿背景，深色文字）
    btn = tk.Button(card, text="确定", bg="#10B981", fg="#090D0A",
                    font=("Microsoft YaHei UI", 9, "bold"), relief="flat", bd=0,
                    cursor="hand2", padx=28, pady=6, activebackground="#34D399",
                    activeforeground="#090D0A", command=win.destroy)
    btn.pack(pady=(0, 18))

    # 绑定回车和 Esc
    win.bind("<Return>", lambda e: win.destroy())
    win.bind("<Escape>", lambda e: win.destroy())
    win.focus_set()
    win.grab_set()
    win.wait_window()


HOST, PORT = "127.0.0.1", 5005
REQUIRED_PKGS = ["edge-tts", "flask", "flask-cors", "lameenc"]
MIN_PY = (3, 10)

KO_FI_URL = "https://ko-fi.com/rayhu"
APP_VERSION = "2.0.5"
APP_NAME = "FewType"
GITHUB_URL = "https://github.com/Ray1979ANYWAY/VoxEcho"
# Ko-fi 咖啡杯图标（浅蓝圆角底 + 白杯 + 橙心），20x20 PNG base64，
# 供 tk.PhotoImage(data=...) 内嵌显示，无需额外资源文件。
KO_FI_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAD1ElEQVR4nJWUX2gcRRzHP/Nn9/bucneStA1tqq3RmFBCHwotFrFFVIJ5qLVQFCwoKFTsg6IUH0sf9FFQUbAKIlKQ9sEHY+tTiyL+qWhQLFaM2pimtsnlrpfby93e7szIXkwaJRZdmIednfnu5zu/7/yEc04IIRzA4ffDUdCjzpqcMUZwo0eCUtoIqc7WquUPjh3sWzhyxMl0k3jmnWrJC/zjSmdGhZAgbqy1/LgOB9a0v49a7Udeeaz4ozhy1un5S/Njhe7iSH2uZsR/FVvWdGSLJdVqhJPCsEOH0/P3+0HXSL0ynwgp9f9SS+0JQatei4Ou0qZWIzykHfJBIaUTcB3NORw2Xb4aU2e+czRLM0KouN12ztk9GlywcqdzFu35SJ3t2FmdCkx7AWNMh3AFbCa1aFeKeX6Wyuxl6pe+Qet/noDAOYNTObo3byeXzZIk8QocYbVzi68pjfZ9KrOX6K2d5tmHt5PNFzo0SxTpmnSUy2XePXWSxq37yGZ87F+KDidWIDiEDqhPfs7RQ/exYdPgvxaifwCazQWOfTHBmm07qc9dW/6mU8fCLQ5nDPnAxw/yWGsXc6bUsiO3VBJjuGlNHzM/f4RpXKZveOS6oJEBkZa0vALa0zTxsSZBpgZSsfIUjL0OPX2I6hXsthHk8C6isEr54jgiDhHSo3/HHuJWA702/JVSZHHhDDbbQ649hc7vAqmgGcJL++HCV5DNQ9KGU2/Ca19jlKR7wxY2bNlNvTy5fLm00Rny8RS3XTnNwNyXrK//hHjxBDz0PGL4bggrUOxetB/kYW4Grl1F+AEL1WnmZyaQKnPdciVYT62rn3Ol3eSFIfjuLV4Y+oPimfdw336MuGMHnP8MNg7C3DTc+zgM3ElmfBxjE8LyJIP3HCSOkk7YtbJtgjiDjKqIbDe/qV6aex+lWCjA1Ytw5RfYuQ/6t3bOET/bIUniFoO7n+CW4buIwrBTUKRIqyxxQjorFApL4CK8hSomCHC9mxG9mxe9NOZBZ7DtNk5pPM8jiSokbYM1MVJ7nWslpXBn0iSkkZVS0nI5ZmZnUJ6X/g21NPJFlJR4vo9Skt+nptG5NQhsmmCrvUzaVT8Vh9+eLSQZ/1y2UByKGvW4GUXKTpzkge3r6CqUOhf3bzm0lnKlxth4zNqtewVJZLwgr20cNy1mW2ftc8dbQ8rjQ60zt6cbmq2IyvSFVbuCswYvKLJ24wCYCKkzOBvX4qh54OUDpTGRtu2jR4V96o2JdYWem592Jh5FiJzn51KiVbuts0kaYie0Z5TWn7RqlWOvPrn+h/0nnPoTApGxu3uuaQMAAAAASUVORK5CYII="

# ---------------------------------------------------------------------------
# 配置目录（安全关键）：bridge_config.json 含 API Key 与风格 Prompt，
# 绝不能放在程序目录——否则打包/压缩/复制/转移程序文件夹会带走密钥。
# ---------------------------------------------------------------------------

def config_dir() -> Path:
    """配置文件目录：%APPDATA%\\FewType（源码 / frozen 统一）。"""
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "FewType"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _migrate_legacy_config() -> None:
    """旧版把 bridge_config.json 存在程序目录或旧 %APPDATA%\\VoxEcho——启动时迁移到
    %APPDATA%\\FewType 并删除源文件。改名 FewType 前的目录（com.rayanyway 无，TK 为 VoxEcho）
    也一并迁移，确保老用户升级不丢 API Key 与风格 Prompt。
    迁移后打包、压缩、复制、移动整个程序文件夹都不会带走含 API Key 的配置。"""
    try:
        _base = os.environ.get("APPDATA") or str(Path.home())
        _candidates = [
            Path(_base) / "VoxEcho" / "bridge_config.json",          # 改名 FewType 前的 APPDATA 目录
            Path(__file__).resolve().parent / "bridge_config.json",   # 程序目录（源码 / frozen 统一）
        ]
        _dst = config_dir() / "bridge_config.json"
        for _src in _candidates:
            if not _src.exists():
                continue
            if not _dst.exists():
                import shutil
                shutil.copy2(_src, _dst)
            try:
                _src.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _lock_config_file() -> None:
    """给配置文件加“拒绝删除/移动”ACL（Everyone 拒绝 DELETE）：
    资源管理器里拖动/删除会提示“拒绝访问”（提示语言跟随系统）。
    复制仍允许——Windows 没有“复制时弹警告”的机制；真正防泄露靠 config 不在程序目录。"""
    try:
        p = config_dir() / "bridge_config.json"
        if not p.exists():
            return
        import subprocess
        subprocess.run(["icacls", str(p), "/deny", "*S-1-1-0:(DE)"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

def detect_ui_lang() -> str:
    """Return 'zh-CN' | 'zh-TW' | 'en'.
    优先读取配置文件中的 ui_lang（用户手动选择），否则从系统语言检测。"""
    # 优先读取用户在 UI 中选择的语言（保存于 bridge_config.json 的 ui_lang 字段）
    try:
        _cfg_path = config_dir() / "bridge_config.json"
        if _cfg_path.exists():
            _data = json.loads(_cfg_path.read_text(encoding="utf-8"))
            if _data.get("ui_lang") in ("zh-CN", "zh-TW", "en"):
                return _data["ui_lang"]
    except Exception:
        pass
    candidates = []
    try:
        loc = locale.getdefaultlocale()
        if loc and loc[0]:
            candidates.append(loc[0])
    except Exception:
        pass
    for key in ("LANG", "LC_ALL", "LC_MESSAGES"):
        v = os.environ.get(key)
        if v:
            candidates.append(v)
    for c in candidates:
        cl = c.lower().replace("_", "-")
        if cl.startswith("zh-tw") or cl.startswith("zh-hk") or cl.startswith("zh-hant"):
            return "zh-TW"
        if cl.startswith("zh"):
            return "zh-CN"
    return "en"


# 迁移旧版程序目录里的 config（须在 _LANG 之前完成，才能读到 ui_lang）
_migrate_legacy_config()
_lock_config_file()

_LANG = detect_ui_lang()

_TEXTS = {
    "en": {
        "title": "FewType Voice Processing Platform",
        "heading": "FewType voice processing platform",
        "status_stopped": "Status: stopped",
        "status_running": "Status: running  http://{host}:{port}",
        "status_env_bad": "Status: environment not ready",
        "hint": (
            "Keep this program running so the Chrome extension can read aloud.\n"
            "Closing the window minimizes to the tray (does not exit).\n"
            "To quit fully: tray icon → Exit."
        ),
        "autostart": "Start with Windows (keep running)",
        "btn_start": "Start service",
        "btn_stop": "Stop service",
        "btn_health": "Health check",
        "btn_open_health": "Open health page",
        "howto": (
            "How to use:\n"
            "1. Keep this app running (window or tray)\n"
            "2. Load the extension in Chrome\n"
            "3. Open a book in the web Google Play Books / Koodo, "
            "select the text with the mouse to start reading from there, "
            "or start from the page top by default\n"
            "4. Pause/resume hotkey: ."
        ),
        "sponsor": "Support FewType",
        "log_title": "Log (terminal)",
        "env_error_title": "Environment error",
        "env_error_body": "Dependencies are not ready. See the log panel.",
        "start_warn_title": "Start",
        "start_warn_body": "Service may not be ready. Check the log panel.",
        "health_ok_title": "Health check",
        "health_ok_body": "Service is healthy\nhttp://{host}:{port}/health",
        "health_fail_title": "Health check",
        "health_fail_body": "No response. Please start the service first.",
        "exit_confirm_title": "Exit",
        "exit_confirm_body": "The extension cannot read aloud after exit. Quit anyway?",
        "welcome_title": "Welcome to FewType bridge",
        "welcome_body": (
            "Keep this program running so the Chrome extension can read aloud.\n\n"
            "You can enable Start with Windows.\n"
            "Closing the window goes to the tray (if available)."
        ),
        "already_running_title": "FewType is already running",
        "already_running_body": "FewType bridge is already running. Please do not start it again.\nCheck the FewType icon in the system tray.",
        "tray_show": "Show window",
        "tray_start": "Start service",
        "tray_stop": "Stop service",
        "tray_exit": "Exit",
        "tray_tooltip": "FewType bridge",
        "log_packed": "Running as packaged app; skip Python/pip checks.",
        "log_python": "Python: {v}",
        "log_need_py": "Error: need Python >= {a}.{b}",
        "log_deps_ok": "Dependencies OK.",
        "log_deps_missing": "Missing: {pkgs}. Installing with pip…",
        "log_pip_fail": "pip failed: {err}",
        "log_pip_ok": "Dependencies installed.",
        "log_pip_exc": "pip error: {e}",
        "log_already": "Service already running.",
        "log_reuse": "Healthy service already on port; reusing it.",
        "log_port_busy": "Port {port} is in use by another program.",
        "log_start_cmd": "Starting: {cmd}",
        "log_start_fail": "Start failed: {e}",
        "log_ready": "Bridge ready -> http://{host}:{port}",
        "log_proc_exit": "Service process exited.",
        "log_timeout": "Timed out waiting for ready. See log.",
        "log_not_running": "Service is not running.",
        "log_stopping": "Stopping service…",
        "log_stopped": "Service stopped.",
        "log_shortcut_ok": "Desktop shortcut created: {name}",
        "log_shortcut_fail": "Desktop shortcut failed (ignored): {e}",
        "log_autostart_on": "Start with Windows: enabled.",
        "log_autostart_off": "Start with Windows: disabled.",
        "log_autostart_fail": "Autostart setting failed: {e}",
        "log_autostart_os": "Autostart is only supported on Windows.",
        "log_tray_ok": "Tray icon ready.",
        "log_tray_missing": "pystray/Pillow not installed; closing window will ask to quit.",
        "log_min_tray": "Minimized to tray.",
        "log_boot": "FewType voice bridge launcher",
        "log_workdir": "Working directory: {d}",
        "log_first": "First-run setup done.",
        "log_user_start": "—— User clicked Start ——",
        "log_health_ok": "Health check: OK",
        "log_health_fail": "Health check: failed",
        "shortcut_name": "FewType.lnk",
    },
    "zh-CN": {
        "title": "FewType 语音处理平台",
        "heading": "FewType 语音处理平台",
        "status_stopped": "状态：未启动",
        "status_running": "状态：运行中  http://{host}:{port}",
        "status_env_bad": "状态：环境未就绪",
        "hint": (
            "请保持本程序运行，Chrome 扩展才能朗读。\n"
            "关闭窗口会最小化到托盘（不会退出）。\n"
            "完全退出：托盘图标 → 退出。"
        ),
        "autostart": "开机时自动启动本程序（常驻）",
        "btn_start": "启动服务",
        "btn_stop": "停止服务",
        "btn_health": "健康检查",
        "btn_open_health": "打开健康页",
        "howto": (
            "使用顺序：\n"
            "1. 本程序保持运行（窗口或托盘）\n"
            "2. Chrome 加载扩展\n"
            "3. 打开网页版微信读书 / Google Play 图书 / Koodo 里面的图书，"
            "鼠标选定文本开始朗读，或者默认从页首开始朗读\n"
            "4. 暂停/恢复的快捷键：."
        ),
        "sponsor": "支持 FewType",
        "log_title": "运行日志（终端）",
        "env_error_title": "环境错误",
        "env_error_body": "依赖未就绪，请查看右侧日志。",
        "start_warn_title": "启动",
        "start_warn_body": "服务可能未就绪，请看右侧日志。",
        "health_ok_title": "健康检查",
        "health_ok_body": "服务正常\nhttp://{host}:{port}/health",
        "health_fail_title": "健康检查",
        "health_fail_body": "服务未响应，请先启动。",
        "exit_confirm_title": "退出",
        "exit_confirm_body": "关闭后扩展将无法朗读。确定退出？",
        "welcome_title": "欢迎使用 FewType 桥接",
        "welcome_body": (
            "请保持本程序运行，Chrome 扩展才能朗读。\n\n"
            "可勾选「开机启动」。\n"
            "关闭窗口会进入托盘（若可用）。"
        ),
        "already_running_title": "FewType 已在运行",
        "already_running_body": "检测到 FewType 桥接已经在运行，请勿重复启动。\n请查看系统托盘中的 FewType 图标。",
        "tray_show": "显示主窗口",
        "tray_start": "启动服务",
        "tray_stop": "停止服务",
        "tray_exit": "退出",
        "tray_tooltip": "FewType 桥接",
        "log_packed": "已打包运行，跳过 Python / pip 检测。",
        "log_python": "当前 Python: {v}",
        "log_need_py": "错误：需要 Python >= {a}.{b}",
        "log_deps_ok": "依赖已齐全。",
        "log_deps_missing": "缺少依赖: {pkgs}，开始 pip 安装…",
        "log_pip_fail": "pip 失败: {err}",
        "log_pip_ok": "依赖安装完成。",
        "log_pip_exc": "pip 异常: {e}",
        "log_already": "服务已在运行。",
        "log_reuse": "检测到端口上已有健康服务，直接使用。",
        "log_port_busy": "端口 {port} 被占用且不是本桥接。",
        "log_start_cmd": "启动: {cmd}",
        "log_start_fail": "启动失败: {e}",
        "log_ready": "桥接已就绪 -> http://{host}:{port}",
        "log_proc_exit": "服务进程已退出。",
        "log_timeout": "等待就绪超时，请看右侧日志。",
        "log_not_running": "服务未在运行。",
        "log_stopping": "正在停止服务…",
        "log_stopped": "服务已停止。",
        "log_shortcut_ok": "已创建桌面快捷方式: {name}",
        "log_shortcut_fail": "桌面快捷方式失败(可忽略): {e}",
        "log_autostart_on": "已设置开机启动。",
        "log_autostart_off": "已取消开机启动。",
        "log_autostart_fail": "设置开机启动失败: {e}",
        "log_autostart_os": "开机启动仅支持 Windows。",
        "log_tray_ok": "托盘图标已就绪。",
        "log_tray_missing": "未安装 pystray/Pillow，关闭窗口将询问是否退出。",
        "log_min_tray": "已最小化到托盘。",
        "log_boot": "FewType 语音桥接启动器",
        "log_workdir": "工作目录: {d}",
        "log_first": "首次运行初始化完成。",
        "log_user_start": "—— 用户点击启动 ——",
        "log_health_ok": "健康检查：OK",
        "log_health_fail": "健康检查：失败",
        "shortcut_name": "FewType.lnk",
    },
    "zh-TW": {
        "title": "FewType 語音處理平台",
        "heading": "FewType 語音處理平台",
        "status_stopped": "狀態：未啟動",
        "status_running": "狀態：執行中  http://{host}:{port}",
        "status_env_bad": "狀態：環境未就緒",
        "hint": (
            "請保持本程式執行，Chrome 擴充功能才能朗讀。\n"
            "關閉視窗會最小化到系統匣（不會結束）。\n"
            "完全結束：系統匣圖示 → 結束。"
        ),
        "autostart": "開機時自動啟動本程式（常駐）",
        "btn_start": "啟動服務",
        "btn_stop": "停止服務",
        "btn_health": "健康檢查",
        "btn_open_health": "開啟健康頁",
        "howto": (
            "使用順序：\n"
            "1. 本程式保持執行（視窗或系統匣）\n"
            "2. 在 Chrome 載入擴充功能\n"
            "3. 打開網頁版微信讀書 / Google Play 圖書 / Koodo 裡面的書籍，"
            "用滑鼠選定文字開始朗讀，或預設從頁首開始朗讀\n"
            "4. 暫停/繼續的快捷鍵：."
        ),
        "sponsor": "支持 FewType ☕",
        "log_title": "執行記錄（終端）",
        "env_error_title": "環境錯誤",
        "env_error_body": "相依套件未就緒，請查看右側記錄。",
        "start_warn_title": "啟動",
        "start_warn_body": "服務可能尚未就緒，請查看右側記錄。",
        "health_ok_title": "健康檢查",
        "health_ok_body": "服務正常\nhttp://{host}:{port}/health",
        "health_fail_title": "健康檢查",
        "health_fail_body": "服務無回應，請先啟動。",
        "exit_confirm_title": "結束",
        "exit_confirm_body": "結束後擴充功能將無法朗讀。確定結束？",
        "welcome_title": "歡迎使用 FewType 橋接",
        "welcome_body": (
            "請保持本程式執行，Chrome 擴充功能才能朗讀。\n\n"
            "可勾選「開機啟動」。\n"
            "關閉視窗會進入系統匣（若可用）。"
        ),
        "already_running_title": "FewType 已在執行",
        "already_running_body": "偵測到 FewType 橋接已在執行，請勿重複啟動。\n請查看系統匣中的 FewType 圖示。",
        "tray_show": "顯示主視窗",
        "tray_start": "啟動服務",
        "tray_stop": "停止服務",
        "tray_exit": "結束",
        "tray_tooltip": "FewType 橋接",
        "log_packed": "已打包執行，略過 Python / pip 偵測。",
        "log_python": "目前 Python: {v}",
        "log_need_py": "錯誤：需要 Python >= {a}.{b}",
        "log_deps_ok": "相依套件已齊全。",
        "log_deps_missing": "缺少套件: {pkgs}，開始以 pip 安裝…",
        "log_pip_fail": "pip 失敗: {err}",
        "log_pip_ok": "相依套件安裝完成。",
        "log_pip_exc": "pip 例外: {e}",
        "log_already": "服務已在執行。",
        "log_reuse": "偵測到連接埠上已有健康服務，直接使用。",
        "log_port_busy": "連接埠 {port} 被占用且不是本橋接。",
        "log_start_cmd": "啟動: {cmd}",
        "log_start_fail": "啟動失敗: {e}",
        "log_ready": "橋接已就緒 -> http://{host}:{port}",
        "log_proc_exit": "服務行程已結束。",
        "log_timeout": "等待就緒逾時，請查看右側記錄。",
        "log_not_running": "服務未在執行。",
        "log_stopping": "正在停止服務…",
        "log_stopped": "服務已停止。",
        "log_shortcut_ok": "已建立桌面捷徑: {name}",
        "log_shortcut_fail": "桌面捷徑失敗(可忽略): {e}",
        "log_autostart_on": "已設定開機啟動。",
        "log_autostart_off": "已取消開機啟動。",
        "log_autostart_fail": "設定開機啟動失敗: {e}",
        "log_autostart_os": "開機啟動僅支援 Windows。",
        "log_tray_ok": "系統匣圖示已就緒。",
        "log_tray_missing": "未安裝 pystray/Pillow，關閉視窗將詢問是否結束。",
        "log_min_tray": "已最小化到系統匣。",
        "log_boot": "FewType 語音橋接啟動器",
        "log_workdir": "工作目錄: {d}",
        "log_first": "首次執行初始化完成。",
        "log_user_start": "—— 使用者點選啟動 ——",
        "log_health_ok": "健康檢查：OK",
        "log_health_fail": "健康檢查：失敗",
        "shortcut_name": "FewType.lnk",
    },
}


def t(key: str, **kwargs) -> str:
    table = _TEXTS.get(_LANG) or _TEXTS["en"]
    s = table.get(key) or _TEXTS["en"].get(key) or key
    # zh-TW 没有独立翻译表时，用 zh-CN 的文本运行时转繁体
    if _LANG == "zh-TW" and key not in _TEXTS.get("zh-TW", {}):
        s = _s2t(s)
    if kwargs:
        try:
            return s.format(**kwargs)
        except Exception:
            return s
    return s


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).joinpath(*parts)
    return app_dir().joinpath(*parts)


APP_DIR = app_dir()
# 配置放 %APPDATA%\FewType（含 API Key，绝不能随程序目录被拖走）
CONFIG_PATH = config_dir() / "bridge_config.json"
log_queue: "queue.Queue[str]" = queue.Queue()


def _install_crash_hook() -> None:
    """全局兜底：未捕获异常（主线程 + 工作线程）写入 crash.log，崩溃后可定位。"""
    _crash_path = Path(__file__).resolve().parent / "crash.log"

    def _dump(text: str) -> None:
        try:
            with _crash_path.open("a", encoding="utf-8") as f:
                f.write("\n=== %s ===\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
        except Exception:
            pass

    def _main_hook(tp, val, tb):
        import traceback
        _dump("".join(traceback.format_exception(tp, val, tb)))

    def _thread_hook(args):
        import traceback
        _dump("".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook


def ui_log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    log_queue.put("[%s] %s" % (ts, msg))


def _focus_dbg(msg: str) -> None:
    """焦点调试专用日志（不含原文，落盘排查上屏失败）"""
    try:
        with open(Path(__file__).resolve().parent / "stt_focus_debug.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def load_config() -> dict:
    defaults = {
        "autostart": False,
        "first_run_done": False,
        "provider": {
            "platform": "groq",
            "api_key": "",
            "asr_model": "",
            "llm_model": "",
            "base_url": "",
        },
        "tts_output_dir": "",
        # 默认风格内置（发布包不带 bridge_config.json，首次运行即有）
        "stt_styles": [
            {
                "name": "Karwai Wong style",
                "prompt": (
                    "Role: You are a scriptwriter specializing in Wong Kar-wai's signature cinematic "
                    "monologue style.\n\nTask: Rewrite the user's input into a reflective, poetic, and "
                    "atmospheric monologue reminiscent of classic Hong Kong cinema (e.g., Chungking Express, "
                    "In the Mood for Love).\n\nStyle Guidelines:\n1. Temporal Anchors: Frequently frame "
                    "thoughts around ultra-specific timestamps, precise distances, or shelf-life expiration "
                    "dates (e.g., \"At 0.01mm apart,\" \"57 minutes past midnight,\" \"Canned pineapples "
                    "expiring on May 1st\").\n2. Sensory & Visual Imagery: Evoke neon lights, rain-slicked "
                    "streets, lingering smoke, retro songs, and quiet solitary moments.\n3. Tone: Melancholic, "
                    "nostalgic, detached yet emotionally deeply yearning. Use short, rhythmic sentences with "
                    "reflective pauses.\n4. Core Retention: Keep the essential meaning or main event from the "
                    "original speech, but reframe it as a memory or interior monologue.\n\nOutput Constraint:\n"
                    "Output ONLY the final polished text in the requested target language. Do NOT add meta "
                    "commentary, markdown formatting, or cinematic scene directions (like [Camera cuts])."
                ),
            }
        ],
    }
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception:
            pass
    return defaults


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def ensure_environment(log=ui_log) -> bool:
    if getattr(sys, "frozen", False):
        log(t("log_packed"))
        return True
    ver = sys.version_info
    log(t("log_python", v="%d.%d.%d" % (ver.major, ver.minor, ver.micro)))
    if (ver.major, ver.minor) < MIN_PY:
        log(t("log_need_py", a=MIN_PY[0], b=MIN_PY[1]))
        log("https://www.python.org/downloads/")
        return False
    import importlib.util

    missing = []
    mapping = {
        "edge-tts": "edge_tts",
        "flask": "flask",
        "flask-cors": "flask_cors",
    }
    for name in REQUIRED_PKGS:
        mod = mapping.get(name, name.replace("-", "_"))
        if importlib.util.find_spec(mod) is None:
            missing.append(name)
    if not missing:
        log(t("log_deps_ok"))
        return True
    log(t("log_deps_missing", pkgs=", ".join(missing)))
    cmd = [sys.executable, "-m", "pip", "install", "-U"] + missing
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if p.stdout:
            for line in p.stdout.splitlines()[-20:]:
                log(line)
        if p.returncode != 0:
            log(t("log_pip_fail", err=(p.stderr or "")[-400:]))
            return False
        log(t("log_pip_ok"))
        return True
    except Exception as e:
        log(t("log_pip_exc", e=e))
        return False


def port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def health_ok() -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(
            "http://%s:%d/health" % (HOST, PORT), timeout=2
        ) as r:
            return r.status == 200
    except Exception:
        return False


def desktop_path() -> Path:
    return Path(os.path.expanduser("~")) / "Desktop"


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def exe_or_script() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def create_shortcut(link_path: Path, target: Path) -> None:
    if sys.platform != "win32":
        return
    target_s = str(target).replace("'", "''")
    link_s = str(link_path).replace("'", "''")
    work_s = str(target.parent).replace("'", "''")
    # IconLocation 显式指定 exe 内嵌图标（索引 0 = 第一个图标组，含 16~256 多尺寸）。
    # 若不设置，Windows 会在创建快捷方式那一刻用"当前缓存的 exe 默认图标"渲染，
    # 首次运行/exe 刚替换时可能没读到内嵌的清晰图标，导致桌面快捷方式图标发糊或显示旧图标。
    icon_s = str(target).replace("'", "''")
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "$s.IconLocation = '%s,0'; "
        "$s.Save()"
    ) % (link_s, target_s, work_s, icon_s)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
    )


def ensure_desktop_shortcut(log=ui_log) -> None:
    if sys.platform != "win32":
        return
    link = desktop_path() / t("shortcut_name")
    if link.exists():
        return
    try:
        create_shortcut(link, exe_or_script())
        log(t("log_shortcut_ok", name=link.name))
    except Exception as e:
        log(t("log_shortcut_fail", e=e))


def set_autostart(enabled: bool, log=ui_log) -> None:
    if sys.platform != "win32":
        log(t("log_autostart_os"))
        return
    link = startup_folder() / "FewType-bridge.lnk"
    try:
        if enabled:
            startup_folder().mkdir(parents=True, exist_ok=True)
            create_shortcut(link, exe_or_script())
            log(t("log_autostart_on"))
        else:
            if link.exists():
                link.unlink()
            log(t("log_autostart_off"))
    except Exception as e:
        log(t("log_autostart_fail", e=e))


class BridgeService:
    def __init__(self):
        self.proc = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, log=ui_log) -> bool:
        if self.running():
            log(t("log_already"))
            return True
        if port_open() and health_ok():
            log(t("log_reuse"))
            return True
        if port_open() and not health_ok():
            log(t("log_port_busy", port=PORT))
            return False

        if getattr(sys, "frozen", False):
            cmd = [str(sys.executable), "--run-server"]
        else:
            server_py = app_dir() / "server.py"
            cmd = [sys.executable, str(server_py)]

        log(t("log_start_cmd", cmd=" ".join(cmd)))
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(APP_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as e:
            log(t("log_start_fail", e=e))
            return False

        def pump():
            assert self.proc and self.proc.stdout
            for line in self.proc.stdout:
                line = line.rstrip("\n\r")
                if line:
                    log_queue.put(line)

        threading.Thread(target=pump, daemon=True).start()

        for _ in range(50):
            time.sleep(0.2)
            if health_ok():
                log(t("log_ready", host=HOST, port=PORT))
                return True
            if self.proc.poll() is not None:
                log(t("log_proc_exit"))
                return False
        log(t("log_timeout"))
        return False

    def stop(self, log=ui_log) -> None:
        if not self.running():
            log(t("log_not_running"))
            return
        assert self.proc
        log(t("log_stopping"))
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None
        log(t("log_stopped"))


service = BridgeService()




def resolve_app_icon_ico() -> Path | None:
    """Path to .ico for window title bar + taskbar."""
    candidates = [
        APP_DIR / "FewType.ico",
        APP_DIR / "icon" / "FewType.ico",
        resource_path("FewType.ico"),
        resource_path("icon", "FewType.ico"),
    ]
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


def set_windows_app_id(app_id: str = "FewType.Bridge.1") -> None:
    """让任务栏按「正式应用」处理，优先用 exe 内嵌多尺寸图标，减少发糊。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def apply_window_icons(root) -> None:
    """
    设置窗口/任务栏图标。

    【背景 / 为什么这样写】
    Tk 的 iconbitmap 在高 DPI 下常只用到 ico 里较小的一帧导致发糊；
    这里用 PIL 取出 ico 中最大一帧，再 iconphoto 提交高清图。

    【历史修复记录（重要，勿回退）】
    早期版本在 iconphoto(256px 高清) 之后无条件调用 iconbitmap(ico)，
    后者会用 ico 里的 16x16 小帧覆盖掉刚提交的高清图，导致任务栏发糊。
    已修复：iconbitmap 仅作 iconphoto 失败时的兜底，不再覆盖高清图。

    【完整图标链路（配合 build.bat）】
    - build.bat 的 --icon FewType.ico
      → 把 7 尺寸(16~256)图标内嵌进 exe 的 PE 资源
      → 资源管理器里看 exe 文件 = 清晰（与运行时无关，一直正常）
    - build.bat 的 --add-data FewType.ico;.
      → 把 ICO 打包进 PKG 归档
      → 运行时 resource_path("FewType.ico") 从解压目录取到 ICO
      → 本函数取最大帧(256px)经 iconphoto 提交 = 任务栏清晰
    - 发布只需单 exe：两条链路都在 exe 内部，无需 exe 旁再放 ico。

    【警告】
    对 onefile exe 运行 rcedit / Resource Hacker / UpdateResource
    改图标会重写整个 exe 并丢弃末尾 PKG 归档（报 "embedded PKG
    archive" 错误）。onefile 换图标只能用 tools/fix_exe_icon_safe.py
    （注入后原样拼回 PKG），日常构建不需要。
    """
    ico = resolve_app_icon_ico()
    if ico is None:
        return

    # 1) 尽量提供高分辨率 PhotoImage（任务栏/标题栏更清晰）
    photo_ok = False
    try:
        from PIL import Image, ImageTk

        im = Image.open(ico)
        # ICO 可能含多帧：选像素最多的一帧
        best = im
        try:
            n = getattr(im, "n_frames", 1) or 1
            pixels = 0
            for i in range(n):
                im.seek(i)
                frame = im.copy()
                px = frame.size[0] * frame.size[1]
                if px >= pixels:
                    pixels = px
                    best = frame
        except Exception:
            best = im.convert("RGBA")
        best = best.convert("RGBA")
        # 不要强行缩到 16：交给系统缩放，保留 256 更清晰
        if best.size[0] < 64:
            best = best.resize((256, 256), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(best)
        root.iconphoto(True, photo)
        # 防止被 GC 收掉
        root._fewtype_icon_photo = photo  # type: ignore[attr-defined]
        photo_ok = True
    except Exception as e:
        ui_log("iconphoto failed: %s" % e)

    # 2) 仅当 iconphoto 失败时，才用 iconbitmap 兜底；
    #    若 iconphoto 已成功，不要再调用 iconbitmap——
    #    它会用 ico 里的 16x16 小帧覆盖掉刚提交的高清图，导致任务栏发糊。
    if not photo_ok:
        try:
            root.iconbitmap(str(ico))
        except Exception:
            try:
                root.iconbitmap(default=str(ico))
            except Exception as e:
                ui_log("iconbitmap failed: %s" % e)


def load_tray_image():
    """Load tray icon from FewType.ico / png near exe or in bundle."""
    try:
        from PIL import Image
    except ImportError:
        return None
    candidates = [
        APP_DIR / "FewType.ico",
        APP_DIR / "icon" / "FewType.ico",
        APP_DIR / "icon" / "FewType-32.png",
        APP_DIR / "icon" / "FewType-48.png",
        APP_DIR / "icon" / "FewType-16.png",
        resource_path("FewType.ico"),
        resource_path("icon", "FewType.ico"),
        resource_path("icon", "FewType-32.png"),
        resource_path("icon", "FewType-48.png"),
    ]
    for p in candidates:
        try:
            if p.exists():
                im = Image.open(p)
                # tray prefers small RGBA
                im = im.convert("RGBA")
                im.thumbnail((64, 64))
                return im
        except Exception:
            continue
    return None



# ---------------------------------------------------------------------------
# 语音服务配置对话框（LLM/ASR 平台：Groq 推荐 / 硅基流动 / 自定义）
# P0：中英双语文案（_LANG 为中文用中文，否则英文）；完整 6 语言在 UI 打磨阶段补全
# ---------------------------------------------------------------------------

# ---- 运行时简繁转换（内置常用字映射表，覆盖界面高频字）----
# 为什么用运行时转换而非手动翻译：244 处 _pd() 调用只有中英文版本，
# 手动翻译繁体工作量大且易遗漏；运行时转换零维护成本，新增文本自动支持繁体。
# 映射表覆盖界面高频出现的简繁不同字，生僻字保留简体（不影响理解）。
_S2T_MAP = {}
for _s, _t in [
    # 高频功能字
    ("这", "這"), ("那", "那"), ("什么", "什麼"), ("怎么", "怎麼"),
    ("样", "樣"), ("因为", "因為"), ("所以", "所以"), ("但是", "但是"),
    ("然后", "然後"), ("时候", "時候"), ("问题", "問題"), ("个", "個"),
    ("们", "們"), ("说", "說"), ("会", "會"), ("觉", "覺"), ("得", "得"),
    ("现", "現"), ("在", "在"), ("已", "已"), ("经", "經"), ("应", "應"),
    ("该", "該"), ("认", "認"), ("为", "為"), ("发", "發"), ("现", "現"),
    ("开", "開"), ("始", "始"), ("结", "結"), ("束", "束"), ("将", "將"),
    ("来", "來"), ("过", "過"), ("还", "還"), ("要", "要"), ("能", "能"),
    ("够", "夠"), ("可", "可"), ("以", "以"), ("请", "請"), ("问", "問"),
    ("答", "答"), ("对", "對"), ("错", "錯"), ("是", "是"), ("否", "否"),
    ("和", "和"), ("或", "或"), ("与", "與"), ("及", "及"), ("等", "等"),
    ("中", "中"), ("英", "英"), ("文", "文"), ("语", "語"), ("言", "言"),
    ("音", "音"), ("频", "頻"), ("视", "視"), ("识", "識"), ("别", "別"),
    ("转", "轉"), ("写", "寫"), ("译", "譯"), ("润", "潤"), ("色", "色"),
    ("模", "模"), ("型", "型"), ("平", "平"), ("台", "臺"), ("服", "服"),
    ("务", "務"), ("器", "器"), ("网", "網"), ("络", "絡"), ("连", "連"),
    ("接", "接"), ("链", "鏈"), ("下", "下"), ("载", "載"), ("上", "上"),
    ("传", "傳"), ("安", "安"), ("装", "裝"), ("卸", "卸"), ("运", "運"),
    ("行", "行"), ("启", "啟"), ("动", "動"), ("停", "停"), ("止", "止"),
    ("关", "關"), ("闭", "閉"), ("打", "打"), ("开", "開"), ("保", "保"),
    ("存", "存"), ("删", "刪"), ("除", "除"), ("编", "編"), ("辑", "輯"),
    ("复", "復"), ("制", "製"), ("粘", "粘"), ("贴", "貼"), ("剪", "剪"),
    ("切", "切"), ("文", "文"), ("件", "件"), ("夹", "夾"), ("目", "目"),
    ("录", "錄"), ("径", "徑"), ("名", "名"), ("称", "稱"), ("内", "內"),
    ("容", "容"), ("数", "數"), ("据", "據"), ("信", "信"), ("息", "息"),
    ("状", "狀"), ("态", "態"), ("错", "錯"), ("误", "誤"), ("警", "警"),
    ("告", "告"), ("提", "提"), ("示", "示"), ("成", "成"), ("功", "功"),
    ("败", "敗"), ("失", "失"), ("超", "超"), ("时", "時"), ("重", "重"),
    ("试", "試"), ("确", "確"), ("认", "認"), ("取", "取"), ("消", "消"),
    ("应", "應"), ("用", "用"), ("定", "定"), ("关", "關"), ("于", "於"),
    ("帮", "幫"), ("助", "助"), ("支", "支"), ("持", "持"), ("赞", "贊"),
    ("助", "助"), ("捐", "捐"), ("赠", "贈"), ("免", "免"), ("费", "費"),
    ("付", "付"), ("额", "額"), ("度", "度"), ("限", "限"), ("制", "制"),
    ("权", "權"), ("验", "驗"), ("证", "證"), ("密", "密"), ("钥", "鑰"),
    ("码", "碼"), ("账", "賬"), ("号", "號"), ("登", "登"), ("录", "錄"),
    ("注", "註"), ("册", "冊"), ("用", "用"), ("户", "戶"), ("客", "客"),
    ("发", "發"), ("者", "者"), ("版", "版"), ("本", "本"), ("更", "更"),
    ("新", "新"), ("布", "佈"), ("测", "測"), ("调", "調"), ("试", "試"),
    ("日", "日"), ("志", "誌"), ("记", "記"), ("录", "錄"), ("监", "監"),
    ("控", "控"), ("统", "統"), ("计", "計"), ("分", "分"), ("析", "析"),
    ("报", "報"), ("告", "告"), ("总", "總"), ("结", "結"), ("计", "計"),
    ("划", "劃"), ("任", "任"), ("务", "務"), ("项", "項"), ("目", "目"),
    ("产", "產"), ("品", "品"), ("牌", "牌"), ("标", "標"), ("商", "商"),
    ("专", "專"), ("利", "利"), ("版", "版"), ("权", "權"), ("创", "創"),
    ("意", "意"), ("新", "新"), ("设", "設"), ("计", "計"), ("发", "發"),
    ("营", "營"), ("销", "銷"), ("售", "售"), ("订", "訂"), ("单", "單"),
    ("购", "購"), ("买", "買"), ("卖", "賣"), ("结", "結"), ("算", "算"),
    ("支", "支"), ("付", "付"), ("退", "退"), ("款", "款"), ("货", "貨"),
    ("换", "換"), ("发", "發"), ("票", "票"), ("收", "收"), ("据", "據"),
    ("凭", "憑"), ("证", "證"), ("合", "合"), ("同", "同"), ("协", "協"),
    ("议", "議"), ("条", "條"), ("款", "款"), ("规", "規"), ("则", "則"),
    ("政", "政"), ("策", "策"), ("法", "法"), ("律", "律"), ("规", "規"),
    ("范", "範"), ("认", "認"), ("证", "證"), ("检", "檢"), ("测", "測"),
    ("验", "驗"), ("鉴", "鑒"), ("定", "定"), ("评", "評"), ("估", "估"),
    ("价", "價"), ("值", "值"), ("审", "審"), ("核", "核"), ("批", "批"),
    ("准", "准"), ("许", "許"), ("可", "可"), ("授", "授"), ("权", "權"),
    ("委", "委"), ("托", "托"), ("代", "代"), ("理", "理"), ("表", "表"),
    ("机", "機"), ("构", "構"), ("组", "組"), ("织", "織"), ("团", "團"),
    ("体", "體"), ("协", "協"), ("会", "會"), ("联", "聯"), ("盟", "盟"),
    ("委", "委"), ("员", "員"), ("会", "會"), ("董", "董"), ("事", "事"),
    ("监", "監"), ("事", "事"), ("股", "股"), ("东", "東"), ("经", "經"),
    ("理", "理"), ("总", "總"), ("监", "監"), ("总", "總"), ("裁", "裁"),
    ("员", "員"), ("工", "工"), ("职", "職"), ("员", "員"), ("同", "同"),
    ("事", "事"), ("团", "團"), ("队", "隊"), ("部", "部"), ("门", "門"),
    ("小", "小"), ("组", "組"), ("项", "項"), ("目", "目"), ("产", "產"),
    ("品", "品"), ("服", "服"), ("务", "務"), ("品", "品"), ("牌", "牌"),
    ("子", "子"), ("菜", "菜"), ("单", "單"), ("窗", "窗"), ("口", "口"),
    ("界", "界"), ("面", "面"), ("按", "按"), ("钮", "鈕"), ("选", "選"),
    ("择", "擇"), ("项", "項"), ("配", "配"), ("置", "置"), ("设", "設"),
    ("定", "定"), ("语", "語"), ("言", "言"), ("语", "語"), ("音", "音"),
    ("文", "文"), ("字", "字"), ("音", "音"), ("频", "頻"), ("视", "視"),
    ("频", "頻"), ("识", "識"), ("别", "別"), ("转", "轉"), ("写", "寫"),
    ("翻", "翻"), ("译", "譯"), ("润", "潤"), ("色", "色"), ("模", "模"),
    ("型", "型"), ("平", "平"), ("台", "臺"), ("服", "服"), ("务", "務"),
    ("服", "服"), ("务", "務"), ("器", "器"), ("网", "網"), ("络", "絡"),
    ("连", "連"), ("接", "接"), ("链", "鏈"), ("接", "接"), ("下", "下"),
    ("载", "載"), ("上", "上"), ("传", "傳"), ("安", "安"), ("装", "裝"),
    ("卸", "卸"), ("载", "載"), ("运", "運"), ("行", "行"), ("启", "啟"),
    ("动", "動"), ("停", "停"), ("止", "止"), ("关", "關"), ("闭", "閉"),
    ("打", "打"), ("开", "開"), ("保", "保"), ("存", "存"), ("删", "刪"),
    ("除", "除"), ("编", "編"), ("辑", "輯"), ("复", "復"), ("制", "製"),
    ("粘", "粘"), ("贴", "貼"), ("剪", "剪"), ("切", "切"), ("文", "文"),
    ("件", "件"), ("文", "文"), ("件", "件"), ("夹", "夾"), ("目", "目"),
    ("录", "錄"), ("路", "路"), ("径", "徑"), ("名", "名"), ("称", "稱"),
    ("内", "內"), ("容", "容"), ("数", "數"), ("据", "據"), ("信", "信"),
    ("息", "息"), ("状", "狀"), ("态", "態"), ("错", "錯"), ("误", "誤"),
    ("警", "警"), ("告", "告"), ("提", "提"), ("示", "示"), ("成", "成"),
    ("功", "功"), ("败", "敗"), ("失", "失"), ("败", "敗"), ("超", "超"),
    ("时", "時"), ("重", "重"), ("试", "試"), ("确", "確"), ("认", "認"),
    ("取", "取"), ("消", "消"), ("应", "應"), ("用", "用"), ("确", "確"),
    ("定", "定"), ("关", "關"), ("于", "於"), ("帮", "幫"), ("助", "助"),
    ("支", "支"), ("持", "持"), ("赞", "贊"), ("助", "助"), ("捐", "捐"),
    ("赠", "贈"), ("免", "免"), ("费", "費"), ("付", "付"), ("费", "費"),
    ("额", "額"), ("度", "度"), ("限", "限"), ("制", "制"), ("权", "權"),
    ("限", "限"), ("验", "驗"), ("证", "證"), ("认", "認"), ("证", "證"),
    ("密", "密"), ("钥", "鑰"), ("码", "碼"), ("密", "密"), ("码", "碼"),
    ("账", "賬"), ("号", "號"), ("登", "登"), ("录", "錄"), ("注", "註"),
    ("册", "冊"), ("用", "用"), ("户", "戶"), ("客", "客"), ("户", "戶"),
    ("开", "開"), ("发", "發"), ("者", "者"), ("版", "版"), ("本", "本"),
    ("更", "更"), ("新", "新"), ("发", "發"), ("布", "佈"), ("测", "測"),
    ("试", "試"), ("调", "調"), ("试", "試"), ("日", "日"), ("志", "誌"),
    ("记", "記"), ("录", "錄"), ("监", "監"), ("控", "控"), ("统", "統"),
    ("计", "計"), ("分", "分"), ("析", "析"), ("报", "報"), ("告", "告"),
    ("总", "總"), ("结", "結"), ("计", "計"), ("划", "劃"), ("任", "任"),
    ("务", "務"), ("项", "項"), ("目", "目"),
]:
    _S2T_MAP[_s] = _t


def _s2t(text: str) -> str:
    """简体中文 → 繁体中文（运行时转换，基于内置常用字映射表）。
    生僻字保留简体，不影响理解。转换开销可忽略（<1ms/次）。"""
    if not text:
        return text
    # 优先替换多字词（什么/怎麼/因为/因為 等），再替换单字
    result = text
    for _k in sorted(_S2T_MAP.keys(), key=len, reverse=True):
        if _k in result:
            result = result.replace(_k, _S2T_MAP[_k])
    return result


def _pd(text_zh: str, text_en: str) -> str:
    """界面文本：中文界面返回简体（zh-CN）或运行时转繁体（zh-TW），英文界面返回英文。"""
    if _LANG == "zh-TW":
        return _s2t(text_zh)
    return text_zh if _LANG == "zh-CN" else text_en


def _bind_text_menu(widget):
    """给文本框（tk.Text / ttk.Entry / tk.Entry）绑定右键上下文菜单。
    支持：剪切、复制、粘贴、全选。
    """
    menu = tk.Menu(widget, tearoff=0, bg="#121A15", fg="#ECFDF5",
                   activebackground="#10B981", activeforeground="#090D0A",
                   bd=0, font=("Segoe UI", 9))
    menu.add_command(label=_pd("剪切", "Cut"), command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label=_pd("复制", "Copy"), command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label=_pd("粘贴", "Paste"), command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label=_pd("全选", "Select All"), command=lambda: widget.event_generate("<<SelectAll>>"))

    def show_menu(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    widget.bind("<Button-3>", show_menu)
    # macOS 右键是 Button-2
    widget.bind("<Button-2>", show_menu)
    return widget


def _open_fullscreen_editor(widget, title):
    """全屏放大编辑：把文本框内容放进独立的 Toplevel 窗口，用大字号滚动编辑。

    对应 Tauri 版 TTS 面板右下角的 Maximize2「全屏编辑」按钮。
    - 「✅ 完成」：把编辑结果保存回原文本框并关闭
    - Esc / 窗口 X：关闭且不保存（避免误触覆盖原文，与原框内容隔离）
    - 大字号（14px）+ 支持 Ctrl+Z 撤销 + 右键菜单
    """
    root_win = widget.winfo_toplevel()
    top = tk.Toplevel(root_win)
    top.title(title)
    top.configure(bg=BG)
    top.transient(root_win)  # 跟随主窗口
    # 尺寸 = 主面板当前大小，位置 = 主面板左上角（正好盖住主面板）。
    # 用 withdraw→geometry→deiconify 三段式：先隐藏窗口再设 geometry，
    # 最后显示——直接设 geometry 时部分系统 WM 会把窗口重新放回屏幕左上角（实测坑）。
    top.withdraw()
    try:
        root_win.update_idletasks()
        _rw, _rh = root_win.winfo_width(), root_win.winfo_height()
        if _rw < 400:  # 主窗口尚未完成渲染时回退到固定尺寸
            _rw, _rh = 820, 620
        _rx, _ry = root_win.winfo_rootx(), root_win.winfo_rooty()
        top.geometry("%dx%d+%d+%d" % (_rw, _rh, _rx, _ry))
    except Exception:
        pass
    top.deiconify()
    try:
        top.attributes("-topmost", True)  # 置顶，避免被主窗口盖住
    except Exception:
        pass

    # 编辑区：大字号 Text + 滚动条（深色主题与主界面一致）
    edit_frame = tk.Frame(top, bg=BG)
    edit_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 6))
    editor = tk.Text(
        edit_frame, wrap=tk.WORD, bg=INPUT, fg=TEXT, insertbackground=TEXT,
        font=("Segoe UI", 14), relief="flat", bd=0, highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=ACCENT, padx=12, pady=12,
        undo=True,  # 支持 Ctrl+Z
    )
    _bind_text_menu(editor)
    sb = tk.Scrollbar(edit_frame, width=8, bg=INPUT, troughcolor=BG,
                      activebackground=ACCENT, relief="flat", bd=0, highlightthickness=0)
    editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    sb.config(command=editor.yview)
    editor.config(yscrollcommand=sb.set)

    _snapshot = widget.get("1.0", "end-1c")  # 打开瞬间的原文快照，供「恢复」回滚
    editor.insert("1.0", _snapshot)  # 复制原文
    editor.focus_set()

    def _save_and_close():
        try:
            content = editor.get("1.0", "end-1c")
            widget.delete("1.0", tk.END)
            widget.insert("1.0", content)  # 同步回原文本框
        except Exception:
            pass
        top.destroy()

    def _restore(_e=None):
        # 恢复：把编辑器重置为打开瞬间的原文（Ctrl+Z 之外的后悔药）
        editor.delete("1.0", tk.END)
        editor.insert("1.0", _snapshot)
        editor.focus_set()

    def _close_no_save(_e=None):
        top.destroy()

    top.protocol("WM_DELETE_WINDOW", _close_no_save)  # 点 X = 不保存（可用「恢复」找回原文）
    top.bind("<Escape>", _close_no_save)

    # 底部操作栏：左提示 + 中「恢复原文」+ 右「完成」主按钮
    bar = tk.Frame(top, bg=BG)
    bar.pack(fill=tk.X, padx=12, pady=(0, 12))
    tk.Label(bar, text=_pd("完成保存回原框；恢复回到打开时原文；Esc/X 放弃", "Done saves back; Restore reverts to the opened text; Esc/X discards"),
             bg=BG, fg="#9CA3AF", font=("Segoe UI", 9)).pack(side=tk.LEFT)
    ttk.Button(bar, text=_pd("↺ 恢复原文", "↺ Restore"), style="Muted.TButton",
               command=_restore).pack(side=tk.LEFT, padx=(10, 0))
    ttk.Button(bar, text=_pd("✅ 完成", "✅ Done"), style="Primary.TButton",
               command=_save_and_close).pack(side=tk.RIGHT)


def _attach_fullscreen_button(wrap_frame, text_widget, title):
    """在文本框右下角叠加一个低调的「全屏编辑」小按钮（⤢）。

    对应 Tauri 版 TTS 面板的 Maximize2 按钮：点击进入全屏大字编辑。
    - place(in_=text_widget) 把按钮锚定在 Text 自身右下角（不遮滚动条）
    - 悬停时高亮（绿底），平时与输入框底色融合的低调样式
    """
    btn = tk.Label(
        wrap_frame, text="⤢", bg=INPUT, fg="#10B981", font=("Segoe UI Symbol", 9, "bold"),
        cursor="hand2", padx=2, pady=0,
    )
    btn.place(in_=text_widget, relx=1.0, rely=1.0, x=0, y=0, anchor="se")  # 紧贴 Text 右下角
    btn.bind("<Button-1>", lambda e: _open_fullscreen_editor(text_widget, title))
    btn.bind("<Enter>", lambda e: btn.configure(bg=ACCENT, fg="#090D0A"))
    btn.bind("<Leave>", lambda e: btn.configure(bg=INPUT, fg="#10B981"))
    return btn


def _platform_name(key: str) -> str:
    """平台名称多语言映射。"""
    mapping = {
        "groq": "Groq",
        "siliconflow": _pd("硅基流动 SiliconFlow", "SiliconFlow"),
        "volcengine": _pd("火山引擎 Volcengine", "Volcengine"),
        "custom": _pd("自定义托管平台", "Custom Provider"),
    }
    return mapping.get(key, key)


def _output_lang_values() -> list:
    """输出语言下拉菜单：根据界面语言显示语言名称。
    中文界面显示母语名称，英文界面显示英文名称。
    """
    langs = [
        ("简体中文", "Chinese (Simplified)"),
        ("繁體中文", "Chinese (Traditional)"),
        ("English", "English"),
        ("日本語", "Japanese"),
        ("한국어", "Korean"),
        ("Español", "Spanish"),
        ("Français", "French"),
        ("Deutsch", "German"),
    ]
    if _LANG in ("zh-CN", "zh-TW"):
        return [zh for zh, en in langs]
    else:
        return [en for zh, en in langs]


def _default_output_lang() -> str:
    """输出语言默认值：跟界面语言一致，无法一致则英语兜底。"""
    vals = _output_lang_values()
    if _LANG == "zh-CN":
        # 简中界面默认简体中文
        for v in vals:
            if "简体" in v or "Simplified" in v:
                return v
    elif _LANG == "zh-TW":
        # 繁中界面默认繁体中文
        for v in vals:
            if "繁體" in v or "Traditional" in v:
                return v
    # 英语兜底（下拉菜单里没有对应语言时才走这里）
    for v in vals:
        if v == "English":
            return v
    return vals[0] if vals else "English"



def _classify_error(e: Exception) -> str:
    """把请求失败归类为可操作提示（网络未触达 / 认证 / 模型 / 额度 / 超时）。"""
    s = str(e)
    low = s.lower()
    if ("timed out" in low) or ("timeout" in low) or ("超过 10 秒" in s):
        return _pd("请求超时：服务器响应过慢，请稍后重试", "Request timed out: server is slow, retry later")
    if ("connection" in low) or ("eof" in low) or ("proxy" in low) or ("tls" in low) or ("network" in low) or ("无法连接到" in s):
        return _pd("网络未触达服务器：请检查网络连接或代理（Clash）是否开启", "Cannot reach the server: check your network or proxy (Clash)")
    if ("401" in s) or ("403" in s) or ("unauthorized" in low) or ("authentication" in low) or ("invalid api key" in low):
        return _pd("API Key 无效或无权限：请检查 Key，或到平台重新生成", "API Key invalid or unauthorized: check the key, or regenerate it on the console")
    if ("404" in s) or ("model does not exist" in low) or ("model not found" in low) or ("not found" in low):
        return _pd("模型不存在或名称错误：请检查模型名", "Model not found: check the model name")
    if ("429" in s) or ("rate limit" in low) or ("quota" in low) or ("insufficient" in low) or ("credit" in low) or ("limit reached" in low):
        return _pd("达到速率限制或额度不足：请稍候重试，或检查平台免费额度", "Rate limit or quota exceeded: retry later, or check your free quota")
    return _pd("请求失败", "Request failed") + ": " + s


_CLICK_WAV = None


def _click_wav():
    """合成"咔嗒"（像合上盒子）：低频衰减闷响 + 短促瞬态。返回 WAV bytes，缓存全局。"""
    global _CLICK_WAV
    if _CLICK_WAV is not None:
        return _CLICK_WAV
    try:
        import io
        import wave
        import numpy as np
        sr = 22050
        n = int(sr * 0.10)
        t = np.arange(n) / sr
        body = np.sin(2 * np.pi * 215 * t) * np.exp(-t * 40)          # 盒盖闷响：215Hz 快衰减
        cn = int(0.012 * sr)
        click = np.random.randn(cn) * np.exp(-np.arange(cn) / (0.0045 * sr)) * 0.85  # "咔"瞬态
        sig = body.copy()
        sig[:cn] += click
        sig = sig / max(1e-6, float(np.max(np.abs(sig)))) * 0.55
        pcm = (sig * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        _CLICK_WAV = buf.getvalue()
        return _CLICK_WAV
    except Exception:
        return None


# ============ Moss Black 主题常量（苔藓荧光绿，模块级供对话框使用） ============
import tkinter.font as tkfont  # noqa: F401  统一字体常量的类型参照

# 全局字体：优先系统内置现代 UI 字体，禁止组件内硬编码 font
UI_FONT_FAMILY = "Microsoft YaHei UI" if sys.platform == "win32" else "PingFang SC"

def _mono_family() -> str:
    """等宽字体：优先 Cascadia Code，未安装则回退 Consolas（Tk 找不到字体会静默回退宋体）。"""
    if sys.platform != "win32":
        return "Menlo"
    windir = os.environ.get("WINDIR", r"C:\Windows")
    fontdir = os.path.join(windir, "Fonts")
    if any(os.path.exists(os.path.join(fontdir, n))
           for n in ("CascadiaCode.ttf", "CascadiaCodePL.ttf", "CascadiaCode-Regular.otf")):
        return "Cascadia Code"
    return "Consolas"  # Windows 标配等宽

MONO_FONT_FAMILY = _mono_family()
FONT_TITLE = (UI_FONT_FAMILY, 11, "bold")  # 卡片/Tab 标题
FONT_BODY = (UI_FONT_FAMILY, 9)            # 主体控件/标签/输入框
FONT_SMALL = (UI_FONT_FAMILY, 8)           # 底部 Footer/提示小字
FONT_LOG = (MONO_FONT_FAMILY, 9)          # 抽屉终端日志（等宽；Tk 字号须为整数，8.5 取整为 9）
FONT_LARGE = (UI_FONT_FAMILY, 16)          # PIN 输入等大字号
FONT_HOTKEY = (UI_FONT_FAMILY, 12, "bold")  # 快捷键输入框
FONT_STRONG = (UI_FONT_FAMILY, 9, "bold")   # 保存按钮等强调文字
FONT_SCENE = (UI_FONT_FAMILY, 13, "bold")   # 三大场景卡片标题（加大两个字号）
BG = "#090D0A"          # 窗口背景
CARD = "#121A15"        # 卡片/容器背景
ACCENT = "#10B981"      # 翡翠绿
ACCENT2 = "#34D399"     # 薄荷荧光绿
TEXT = "#ECFDF5"        # 主文字
MUTED = "#6EE7B7"       # 次要文字（薄荷绿）
MUTED2 = "#6B7280"      # 冷灰
INPUT = "#1A261F"       # 输入/按钮背景
BORDER = "#27372D"      # 边框
LOG_BG = "#050806"      # 日志背景
LOG_FG = "#059669"      # 日志文字
HUD_BG = "#0B1E14"      # HUD 状态条背景
HUD_KEY = "#010203"          # HUD 透明色键（圆角四角露出）
HUD_KEY_COLORREF = 0x00030201  # HUD_KEY 的 COLORREF（RGB(1,2,3)）


def _bind_tooltip(master, widget, text) -> None:
    """轻量悬停提示：<Enter> 延迟 250ms 显示，移出/点击立即隐藏。零依赖。
    用 widget.after() 调度（widget 是悬停目标，比 master.after() 更可靠），
    tooltip 挂到 widget.winfo_toplevel() 下（始终是包含 widget 的顶层窗口）。
    """
    tip = [None, None]

    def _tip_hide():
        if tip[1] is not None:
            try:
                widget.after_cancel(tip[1])
            except Exception:
                pass
            tip[1] = None
        if tip[0] is not None:
            try:
                tip[0].destroy()
            except Exception:
                pass
            tip[0] = None

    def _tip_show():
        _tip_hide()
        try:
            top = widget.winfo_toplevel()
        except Exception:
            top = master
        tl = tk.Toplevel(top)
        tl.overrideredirect(True)
        tl.attributes("-topmost", True)
        tl.configure(bg=ACCENT)
        lbl = tk.Label(
            tl, text=text, bg=INPUT, fg=TEXT, font=FONT_SMALL,
            padx=10, pady=6, justify="left", wraplength=300,
        )
        lbl.pack(padx=1, pady=1)
        tl.update_idletasks()
        x = widget.winfo_rootx() + 6
        y = widget.winfo_rooty() + widget.winfo_height() + 8
        tl.geometry(f"+{x}+{y}")
        tip[0] = tl

    def _on_enter(_ev=None):
        _tip_hide()
        tip[1] = widget.after(250, _tip_show)

    widget.bind("<Enter>", _on_enter, add="+")
    widget.bind("<Leave>", lambda _ev: _tip_hide(), add="+")
    widget.bind("<Button-1>", lambda _ev: _tip_hide(), add="+")


def _auto_tooltip(master, widget, full_text=None, _retries=0) -> None:
    """通用溢出检测：widget 渲染完成后，如果文字可能被截断，自动绑定悬浮提示显示完整内容。
    适用于所有注释/说明 Label 和 Radiobutton——文字够宽不截断时不绑定，截断了才弹提示，零干扰。
    带重试机制：widget 未渲染时（winfo_width<=1）每 50ms 重试，最多 10 次。
    """
    if full_text is None:
        try:
            full_text = widget.cget("text")
        except Exception:
            return
    if not full_text:
        return

    def _check():
        try:
            widget_w = widget.winfo_width()
            # widget 还没渲染好，重试
            if widget_w <= 1:
                if _retries < 10:
                    widget.after(50, lambda: _auto_tooltip(master, widget, full_text, _retries + 1))
                return
            # 用字体测量文字实际像素宽度
            font_spec = widget.cget("font")
            from tkinter import font as _tkfont
            if isinstance(font_spec, _tkfont.Font):
                _f = font_spec
            else:
                _f = _tkfont.Font(font=font_spec)
            text_w = _f.measure(full_text)
            # 敏感阈值：文字宽度超过可用宽度 90% 即绑定（留余量，避免差一点被截断）
            if text_w > (widget_w - 8) * 0.9:
                _bind_tooltip(master, widget, full_text)
        except Exception:
            # 字体测量失败时兜底：直接绑定，保证用户能看到完整文字
            _bind_tooltip(master, widget, full_text)

    # 等待 widget 映射渲染后再检测
    widget.after(80, _check)


def open_provider_dialog(parent, cfg, save_cfg, prov_status=None) -> None:
    """语音服务配置：三个平台单选，选中显示对应配置区；保存写入 cfg['provider']。"""
    import tkinter as tk
    from tkinter import ttk, messagebox

    win = tk.Toplevel(parent)
    win.title(_pd("语音服务配置", "Voice Service Setup"))
    win.configure(bg=BG)
    win.resizable(False, False)
    # 只读文本框样式：低亮度灰色文字 + 稍暗背景，让用户知道不用填
    _style = ttk.Style()
    _style.configure("ReadOnly.TEntry",
                     foreground="#5A6560",
                     fieldbackground="#0A0F0C",
                     insertcolor="#5A6560",
                     selectbackground="#1F2E26",
                     selectforeground="#9CA3AF",
                     bordercolor="#1A261F",
                     lightcolor="#1A261F",
                     darkcolor="#1A261F")
    _style.map("ReadOnly.TEntry",
               foreground=[("readonly", "#5A6560"), ("disabled", "#5A6560"), ("focus", "#5A6560")],
               fieldbackground=[("readonly", "#0A0F0C"), ("disabled", "#0A0F0C"), ("focus", "#0A0F0C")],
               bordercolor=[("readonly", "#1A261F"), ("focus", "#1A261F")])
    win.transient(parent)
    win.grab_set()
    win.attributes("-topmost", True)

    card = tk.Frame(win, bg=CARD, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=BORDER)
    card.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

    cur = cfg.get("provider", {}) or {}
    platform_var = tk.StringVar(value=cur.get("platform", "groq"))
    key_var = tk.StringVar(value=cur.get("api_key", ""))
    asr_var = tk.StringVar(value=cur.get("asr_model", ""))
    llm_var = tk.StringVar(value=cur.get("llm_model", ""))
    url_var = tk.StringVar(value=cur.get("base_url", ""))
    llm_key_var = tk.StringVar(value=cur.get("llm_key", ""))  # LLM 独立 Key（火山=方舟 Key；自定义=LLM 平台 Key）
    llm_url_var = tk.StringVar(value=cur.get("llm_base_url", ""))  # 自定义平台：LLM 独立端点（留空与 ASR 共用）

    # 每个平台独立记忆已填字段；从 cfg 持久化加载，关闭对话框后再打开也能自动回填
    # 为什么要持久化：用户可能在 Groq 和火山之间切换，每次都重新贴 Key 很麻烦
    platform_cache: dict = dict(cur.get("providers", {}))
    current_platform: str = platform_var.get()

    def _save_current():
        platform_cache[current_platform] = {
            "api_key": key_var.get(),
            "llm_key": llm_key_var.get(),
            "asr": asr_var.get(),
            "llm": llm_var.get(),
            "url": url_var.get(),
            "llm_url": llm_url_var.get(),
        }

    def switch_platform():
        nonlocal current_platform
        _save_current()
        current_platform = platform_var.get()
        refresh_platform()

    def refresh_platform():
        defn = _defaults()
        custom = _is_custom()
        cache = platform_cache.get(platform_var.get(), {})
        # 极简平台提示 + 超链接（替代原大段免费额度/注册说明）
        cur_plat = platform_var.get()
        if cur_plat == "groq":
            hint_text = ""  # 平台名后已标"（推荐！）"，此处不重复
            link_text = _pd("🔗 获取 API Key", "🔗 Get API Key")
            link_url = defn.signup_url
        elif cur_plat == "volcengine":
            hint_text = _pd("💡 国内直连（ASR + ARK 两个 Key）", "💡 Direct in China (ASR + ARK keys)")
            link_text = _pd("🔗 控制台", "🔗 Console")
            link_url = ""  # 火山不直接跳转，悬停弹出悬浮面板选择
        elif cur_plat == "siliconflow":
            hint_text = _pd("💡 国内直连", "💡 Direct in China")
            link_text = _pd("🔗 获取 API Key", "🔗 Get API Key")
            link_url = defn.signup_url
        else:
            hint_text = _pd("💡 自定义托管平台", "💡 Custom provider")
            link_text = ""
            link_url = ""
        if defn.proxy_hint and _LANG in ("zh-CN", "zh-TW") and cur_plat == "groq":
            hint_text += _pd(" · 国内需代理", " · Proxy required")
        hint_var.set(hint_text)

        # 链接文本和交互
        link_lbl.configure(text=link_text, cursor="hand2" if (link_url or cur_plat == "volcengine") else "")
        link_lbl.unbind("<Button-1>")
        link_lbl.unbind("<Enter>")
        link_lbl.unbind("<Leave>")
        if cur_plat == "volcengine":
            # 火山：悬停显示可交互悬浮面板（ASR+LLM 两个链接），点击也显示
            link_lbl.bind("<Enter>", lambda _e: _show_volc_popup())
            link_lbl.bind("<Leave>", lambda _e: _hide_volc_popup())
            link_lbl.bind("<Button-1>", lambda _e: _show_volc_popup())
        elif link_url:
            # 其他平台：点击直接跳转
            link_lbl.bind("<Button-1>", lambda _e: webbrowser.open(link_url))
        # 字段可用性：Base URL 仅自定义/火山显示（Groq/SiliconFlow 隐藏，后台静默用默认值）
        if custom or cur_plat == "volcengine":
            url_row.pack(fill=tk.X, pady=2, before=key_row)
        else:
            url_row.pack_forget()
        url_entry.configure(state="normal" if custom else "readonly")
        asr_cb.configure(state="normal")
        llm_cb.configure(state="normal")
        # 恢复该平台缓存 / 平台默认值；内置平台 URL 置灰显示内置地址（用户无需填写）
        key_var.set(cache.get("api_key", ""))
        llm_key_var.set(cache.get("llm_key", ""))
        if custom:
            url_var.set(cache.get("url", ""))
        else:
            url_var.set(defn.base_url)
        llm_url_var.set(cache.get("llm_url", ""))
        cur_plat = platform_var.get()
        # 行顺序：ASR 地址/Key/模型 → LLM 地址/Key/模型（插入到 LLM 模型行之前，先插地址后插 Key）
        try:
            if custom:
                url_lbl.configure(text=_pd("ASR 地址 (base_url)", "ASR Base URL"))
                key_lbl.configure(text=_pd("ASR API Key", "ASR API Key"))
                llm_url_lbl.configure(text=_pd("LLM 地址 (llm_base_url)", "LLM Base URL"))
                llm_url_row.pack(fill=tk.X, pady=2, before=llm_row)
                url_entry.configure(style="TEntry", state="normal")
                llm_url_entry.configure(style="TEntry", state="normal")
            elif cur_plat == "volcengine":
                # 火山引擎：ASR 是 WebSocket 流式，LLM 是方舟 HTTP 端点
                url_lbl.configure(text=_pd("ASR 地址（已内置）", "ASR URL (built-in)"))
                key_lbl.configure(text=_pd("API Key", "API Key"))
                url_var.set(_pd("（WebSocket 流式，无需配置）", "(WebSocket streaming, no config)"))
                llm_url_lbl.configure(text=_pd("LLM 地址（已内置）", "LLM URL (built-in)"))
                llm_url_var.set("https://ark.cn-beijing.volces.com/api/v3")
                llm_url_row.pack(fill=tk.X, pady=2, before=llm_row)
                url_entry.configure(style="ReadOnly.TEntry", state="readonly")
                llm_url_entry.configure(style="ReadOnly.TEntry", state="readonly")
            else:
                url_lbl.configure(text=_pd("平台地址（已内置）", "Base URL (built-in)"))
                key_lbl.configure(text=_pd("API Key", "API Key"))
                llm_url_row.pack_forget()
                url_entry.configure(style="ReadOnly.TEntry", state="readonly")
                llm_url_entry.configure(style="ReadOnly.TEntry", state="readonly")
        except Exception:
            pass
        # LLM Key 行：火山（方舟 Key）与自定义平台显示（位于 LLM 地址之下、LLM 模型之上）
        try:
            if cur_plat in ("volcengine", "custom"):
                llm_key_lbl.configure(
                    text=_pd("方舟 LLM Key", "ARK LLM Key") if cur_plat == "volcengine" else _pd("LLM API Key", "LLM API Key")
                )
                llm_key_row.pack(fill=tk.X, pady=2, before=llm_row)
            else:
                llm_key_row.pack_forget()
        except Exception:
            pass
        if custom:
            # 自定义平台：模型由用户手填（默认空）
            asr_var.set(cache.get("asr", ""))
            llm_var.set(cache.get("llm", ""))
        else:
            _known_asr = {d.default_asr for d in PROVIDERS.values() if d.default_asr}
            _known_llm = {d.default_llm for d in PROVIDERS.values() if d.default_llm}
            for d in PROVIDERS.values():
                _known_llm.update(d.llm_pinned)
            # 若当前值仍是某内置默认（用户未手改），跟随新平台默认
            asr_var.set(cache.get("asr", "") or defn.default_asr)
            llm_var.set(cache.get("llm", "") or defn.default_llm)
            if platform_var.get() == "volcengine":
                # 火山 ASR 无模型列表接口（资源 ID 即模型），内置下拉
                from provider import VOLC_ASR_IDS
                asr_cb.configure(values=list(dict.fromkeys([asr_var.get()] + list(VOLC_ASR_IDS))))
            else:
                asr_cb.configure(values=[defn.default_asr] + list(FALLBACK_ASR()))
            llm_cb.configure(values=list(defn.llm_pinned) + list(FALLBACK_LLM()))

    def FALLBACK_ASR():
        from provider import FALLBACK_MODELS
        return FALLBACK_MODELS["asr"]

    def FALLBACK_LLM():
        from provider import FALLBACK_MODELS
        return FALLBACK_MODELS["llm"]


    # ---- 逻辑 ----
    def _signup_url() -> str:
        return PROVIDERS[platform_var.get()].signup_url

    def _open_signup():
        defn = _defaults()
        url = defn.asr_signup_url or defn.signup_url  # 火山：ASR 与 LLM 分属两个控制台，主链接指向 ASR
        if url:
            webbrowser.open(url)

    def _is_custom() -> bool:
        return platform_var.get() == "custom"

    def _defaults() -> PlatformDef:
        return PROVIDERS[platform_var.get()]

    def _build_provider() -> Provider:
        return Provider(
            platform=platform_var.get(),
            api_key=sanitize_api_key(key_var.get()),
            asr_model=asr_var.get(),
            llm_model=llm_var.get(),
            base_url=url_var.get(),
            llm_key=sanitize_api_key(llm_key_var.get()),
            llm_base_url=llm_url_var.get(),
        )

    def on_fetch():
        try:
            prov = _build_provider()
            asr_list, llm_list = prov.fetch_models_split()
        except ProviderError as e:
            messagebox.showwarning(_pd("刷新失败", "Fetch failed"), str(e), parent=win)
            return
        defn = _defaults()
        # 当前值（含用户手输/粘贴的未命中模型）始终置顶保留，不被覆盖
        if platform_var.get() == "volcengine":
            from provider import VOLC_ASR_IDS
            asr_cb.configure(values=list(dict.fromkeys([asr_var.get()] + list(VOLC_ASR_IDS))))
        else:
            asr_cb.configure(values=list(dict.fromkeys([asr_var.get()] + asr_list)))
        llm_cb.configure(values=list(dict.fromkeys([llm_var.get()] + list(defn.llm_pinned) + llm_list)))
        ui_log(_pd("模型列表已刷新", "Model list refreshed"))

    def on_test():
        test_lbl.configure(text=_pd("测试中…", "Testing…"), style="Muted.TLabel")
        try:
            prov = _build_provider()
        except Exception as e:
            test_lbl.configure(text=_classify_error(e), foreground="#ff6b6b")
            return

        def work():
            try:
                msg = prov.test_connection(timeout=12)
                def ok():
                    test_lbl.configure(text=msg, foreground=ACCENT2)
                win.after(0, ok)
            except Exception as e:
                m = _classify_error(e)
                def bad():
                    test_lbl.configure(text=m, foreground="#ff6b6b")
                win.after(0, bad)

        threading.Thread(target=work, daemon=True).start()

    def on_save():
        _save_current()  # 先把当前平台的输入存到 platform_cache
        prov = {
            "platform": platform_var.get(),
            "api_key": sanitize_api_key(key_var.get()),
            "llm_key": sanitize_api_key(llm_key_var.get()),
            "asr_model": asr_var.get().strip(),
            "llm_model": llm_var.get().strip(),
            "base_url": url_var.get().strip(),
            "llm_base_url": llm_url_var.get().strip(),
            # 持久化所有平台的配置：切换平台时不用重新贴 Key
            # 字段名是对话框内部用的（asr/llm/url），与运行时字段（asr_model/llm_model/base_url）不同，互不干扰
            "providers": dict(platform_cache),
        }
        if prov["platform"] != "custom" and not prov["api_key"]:
            messagebox.showwarning(
                _pd("提示", "Notice"),
                _pd("请先申请并粘贴 API Key（点击上方注册链接）", "Please get and paste your API Key first"),
                parent=win,
            )
            return
        cfg["provider"] = prov
        save_cfg(cfg)
        if prov_status is not None:
            prov_status.set(_pd("已配置：", "Configured: ") + PROVIDERS[prov["platform"]].name)
        ui_log(_pd("语音服务配置已保存", "Voice service config saved"))
        win.destroy()

    # ---- 平台选择 ----
    top = ttk.LabelFrame(card, text=_pd("选择平台", "Platform"), style="Card.TLabelframe")
    top.pack(fill=tk.X, padx=8, pady=(4, 8))

    radio_row = ttk.Frame(top, style="Card.TFrame")
    radio_row.pack(fill=tk.X, padx=8, pady=(6, 0))
    def _platform_choices():
        """平台列表：火山引擎在英/简中/繁中界面展示（国内用户可能用英文界面），其他语言不提供。"""
        choices = [("groq", PROVIDERS["groq"]), ("siliconflow", PROVIDERS["siliconflow"]), ("custom", PROVIDERS["custom"])]
        if _LANG in ("en", "zh-CN", "zh-TW"):
            choices.insert(1, ("volcengine", PROVIDERS["volcengine"]))
        return choices

    for key, defn in _platform_choices():
        if key == "volcengine":
            label = _platform_name(key) + _pd("（推荐国内使用）", " (Recommended for China)")
        elif defn.recommended:
            label = _platform_name(key) + _pd("（推荐！）", " (Recommended)")
        else:
            label = _platform_name(key)
        ttk.Radiobutton(
            radio_row, text=label, value=key, variable=platform_var, command=switch_platform,
            style="Card.TRadiobutton",
        ).pack(side=tk.LEFT, padx=(0, 14))

    hint_var = tk.StringVar()
    ttk.Label(top, textvariable=hint_var, foreground="#9CA3AF", font=FONT_BODY, wraplength=460, justify="left",
              style="Card.TLabel").pack(anchor="w", padx=8, pady=(4, 4))
    # 链接区域：一行链接文本(翡翠绿可点击)
    # Groq/硅基流动：点击直接跳转；火山引擎：悬停弹出可交互悬浮面板(ASR+LLM)
    link_row = tk.Frame(top, bg=BG)
    link_row.pack(anchor="w", padx=8, pady=(0, 4), fill=tk.X)
    link_lbl = ttk.Label(link_row, text="", foreground=ACCENT2, font=FONT_BODY, style="Card.TLabel")
    link_lbl.pack(side=tk.LEFT)

    # 火山引擎的可交互悬浮面板（悬停链接时显示，里面有 ASR+LLM 两个可点击链接）
    # 用 Frame + place 实现（不用 Toplevel，避免 overrideredirect+withdraw 在 Windows 上不可靠）
    volc_popup = tk.Frame(card, bg="#121A15", bd=1, highlightbackground="#27372D", highlightthickness=1)
    _volc_popup_after_id = [None]  # 用于延迟隐藏的 after id

    def _show_volc_popup():
        """显示火山引擎悬浮面板，定位在链接右下方"""
        # 取消之前的延迟隐藏
        if _volc_popup_after_id[0]:
            try:
                win.after_cancel(_volc_popup_after_id[0])
            except Exception:
                pass
            _volc_popup_after_id[0] = None
        # 定位在链接右下方（换算成相对于 card 的坐标）
        win.update_idletasks()
        x = link_lbl.winfo_rootx() - card.winfo_rootx() + link_lbl.winfo_width() + 4
        y = link_lbl.winfo_rooty() - card.winfo_rooty() + link_lbl.winfo_height() + 2
        volc_popup.place(x=x, y=y)
        volc_popup.lift()

    def _hide_volc_popup():
        """延迟 800ms 隐藏悬浮面板，让用户有时间把鼠标移过去点击"""
        if _volc_popup_after_id[0]:
            try:
                win.after_cancel(_volc_popup_after_id[0])
            except Exception:
                pass
        _volc_popup_after_id[0] = win.after(800, lambda: volc_popup.place_forget())

    # 悬浮面板里的两个链接
    _volc_asr_lbl = tk.Label(volc_popup, text="  🔗 ASR 控制台  ", bg="#121A15", fg="#10B981",
                              font=FONT_BODY, cursor="hand2", padx=8, pady=6)
    _volc_asr_lbl.pack(anchor="w", fill=tk.X)
    _volc_asr_lbl.bind("<Button-1>", lambda _e: (webbrowser.open("https://console.volcengine.com/speech/new/"), volc_popup.place_forget()))
    _volc_asr_lbl.bind("<Enter>", lambda _e: _show_volc_popup())
    _volc_asr_lbl.bind("<Leave>", lambda _e: _hide_volc_popup())

    _volc_llm_lbl = tk.Label(volc_popup, text="  🔗 LLM 控制台  ", bg="#121A15", fg="#10B981",
                              font=FONT_BODY, cursor="hand2", padx=8, pady=6)
    _volc_llm_lbl.pack(anchor="w", fill=tk.X)
    _volc_llm_lbl.bind("<Button-1>", lambda _e: (webbrowser.open("https://console.volcengine.com/ark"), volc_popup.place_forget()))
    _volc_llm_lbl.bind("<Enter>", lambda _e: _show_volc_popup())
    _volc_llm_lbl.bind("<Leave>", lambda _e: _hide_volc_popup())

    # ---- 配置区 ----
    body = ttk.Frame(card, style="Card.TFrame")
    body.pack(fill=tk.X, padx=8, pady=(0, 6))

    url_row = ttk.Frame(body, style="Card.TFrame")
    url_row.pack(fill=tk.X, pady=2)
    url_lbl = ttk.Label(url_row, text=_pd("平台地址 (base_url)", "Base URL"), width=22, style="Card.TLabel")
    url_lbl.pack(side=tk.LEFT)
    url_entry = _bind_text_menu(ttk.Entry(url_row, textvariable=url_var, width=40))
    url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _paste_key(var):
        """从剪贴板读取文本 -> 清洗（剔除非 ASCII/换行/隐藏字符）-> 填入 Key 框。"""
        try:
            raw = win.clipboard_get()
        except Exception:
            raw = ""
        cleaned = sanitize_api_key(raw)
        if cleaned:
            var.set(cleaned)
        else:
            ui_log(_pd("剪贴板中没有可用的 Key（已自动清洗）", "No usable key in clipboard (auto-cleaned)"))

    key_row = ttk.Frame(body, style="Card.TFrame")
    key_row.pack(fill=tk.X, pady=2)
    key_lbl = ttk.Label(key_row, text=_pd("API Key", "API Key"), width=22, style="Card.TLabel")
    key_lbl.pack(side=tk.LEFT)
    ttk.Button(key_row, text=_pd("粘贴", "Paste"), command=lambda: _paste_key(key_var),
               style="Paste.TButton").pack(side=tk.LEFT, padx=(6, 0))

    key_entry = _bind_text_menu(ttk.Entry(key_row, textvariable=key_var, show="*", width=40))
    key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # 第二 Key 行（火山=方舟 Key / 自定义=LLM 平台 Key，与主 Key 分开存）
    llm_key_row = ttk.Frame(body, style="Card.TFrame")

    llm_key_lbl = ttk.Label(llm_key_row, text=_pd("LLM API Key", "LLM API Key"), width=22, style="Card.TLabel")
    llm_key_lbl.pack(side=tk.LEFT)
    ttk.Button(llm_key_row, text=_pd("粘贴", "Paste"), command=lambda: _paste_key(llm_key_var),
               style="Paste.TButton").pack(side=tk.LEFT, padx=(6, 0))
    llm_key_entry = _bind_text_menu(ttk.Entry(llm_key_row, textvariable=llm_key_var, show="*", width=40))
    llm_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # LLM 地址行（火山引擎/自定义平台显示）：LLM 可独立于 ASR 配置端点
    llm_url_row = ttk.Frame(body, style="Card.TFrame")
    llm_url_lbl = ttk.Label(llm_url_row, text=_pd("LLM 地址 (llm_base_url)", "LLM Base URL"), width=22, style="Card.TLabel")
    llm_url_lbl.pack(side=tk.LEFT)
    llm_url_entry = _bind_text_menu(ttk.Entry(llm_url_row, textvariable=llm_url_var, width=40))
    llm_url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    asr_row = ttk.Frame(body, style="Card.TFrame")
    asr_row.pack(fill=tk.X, pady=2)
    ttk.Label(asr_row, text=_pd("ASR 模型（听得准不准）", "ASR model"), width=22, style="Card.TLabel").pack(side=tk.LEFT)
    asr_cb = ttk.Combobox(asr_row, textvariable=asr_var, width=37)
    asr_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

    llm_row = ttk.Frame(body, style="Card.TFrame")
    llm_row.pack(fill=tk.X, pady=2)
    ttk.Label(llm_row, text=_pd("LLM 模型（写得地不地道）", "LLM model"), width=22, style="Card.TLabel").pack(side=tk.LEFT)
    llm_cb = ttk.Combobox(llm_row, textvariable=llm_var, width=37)
    llm_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

    tool_row = ttk.Frame(body, style="Card.TFrame")
    tool_row.pack(anchor="w", padx=0, pady=(4, 0))

    def _open_hotwords():
        import os
        hw = stt_engine.HOTWORDS_FILE
        if not hw.exists():
            try:
                hw.parent.mkdir(parents=True, exist_ok=True)
                hw.write_text(
                    "# FewType ASR hotwords\n# One word per line, # starts a comment.\n",
                    encoding="utf-8",
                )
            except Exception as e:
                ui_log(_pd("热词文件创建失败", "Failed to create hotwords file") + f": {e}")
                return
        try:
            os.startfile(str(hw))
        except Exception:
            try:
                import subprocess
                subprocess.Popen(["open", str(hw)])
            except Exception as e:
                ui_log(_pd("无法打开热词文件", "Cannot open hotwords file") + f": {e}")
                return
        ui_log(_pd("热词文件已打开；每行一个词，保存后下次转写自动生效", "Hotwords file opened; one word per line, takes effect on next transcription"))

    fetch_btn = ttk.Button(tool_row, text=_pd("🔄 刷新模型", "🔄 Refresh models"), command=on_fetch)
    fetch_btn.pack(side=tk.LEFT)
    ttk.Button(tool_row, text=_pd("📝 编辑热词", "📝 Edit hotwords"), command=_open_hotwords).pack(side=tk.LEFT, padx=(6, 0))
    ttk.Label(
        body,
        text=_pd("模型下拉可点选，也可直接输入/粘贴模型名（列表未命中时）", "Pick from dropdown, or type/paste a model name (for models not listed)"),
        style="Card.Muted.TLabel", font=FONT_SMALL,
    ).pack(anchor="w", padx=0, pady=(0, 4))

    # ---- 按钮 ----
    btn_row = ttk.Frame(card, style="Card.TFrame")
    btn_row.pack(fill=tk.X, padx=8, pady=(6, 0))
    test_lbl = ttk.Label(btn_row, text="", foreground=ACCENT2, style="Card.TLabel")
    test_lbl.pack(side=tk.LEFT, padx=(10, 0))
    ttk.Button(btn_row, text=_pd("🧪 测试连接", "🧪 Test"), command=on_test).pack(side=tk.LEFT)
    save_btn = tk.Button(
        btn_row, text=_pd("保存", "Save"), command=on_save,
        bg=ACCENT, fg=BG, activebackground=ACCENT2, activeforeground=BG,
        font=FONT_STRONG, relief="flat", bd=0, padx=16, pady=4, cursor="hand2",
    )
    save_btn.bind("<Enter>", lambda e: save_btn.config(bg=ACCENT2))
    save_btn.bind("<Leave>", lambda e: save_btn.config(bg=ACCENT))
    save_btn.pack(side=tk.RIGHT)
    ttk.Button(btn_row, text=_pd("取消", "Cancel"), command=win.destroy).pack(side=tk.RIGHT, padx=6)

    refresh_platform()
    win.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - win.winfo_reqwidth()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - win.winfo_reqheight()) // 3)
    win.geometry(f"+{x}+{y}")


def open_about_dialog(parent):
    """关于对话框：版本号、简介、项目链接、开源协议。"""
    import tkinter as tk
    from tkinter import ttk
    win = tk.Toplevel(parent)
    win.title("关于 " + APP_NAME)
    win.configure(bg="#090D0A")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()
    # 居中
    win.update_idletasks()
    w, h = 380, 260
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry("+%d+%d" % ((sw - w) // 2, (sh - h) // 2))

    card = tk.Frame(win, bg="#121A15", bd=0, highlightthickness=0)
    card.pack(fill="both", expand=True, padx=12, pady=12)

    tk.Label(card, text=APP_NAME, font=("Segoe UI", 16, "bold"),
             fg="#ECFDF5", bg="#121A15").pack(pady=(16, 2))
    tk.Label(card, text=_pd("版本 ", "Version ") + APP_VERSION, font=("Segoe UI", 10),
             fg="#6EE7B7", bg="#121A15").pack(pady=(0, 8))
    tk.Label(card, text=_pd("语音处理平台：电子书朗读 + 长文本转语音 + 语音输入", "Voice Platform: E-book Reading + Text-to-Speech + Speech Input"),
             font=("Segoe UI", 9), fg="#9CA3AF", bg="#121A15",
             wraplength=320, justify="center").pack(pady=(0, 4))
    tk.Label(card, text=_pd("电子书朗读（微信读书/Google Play Books/Koodo）\n微软 TTS 自然发音 + Whisper STT 实时转写", "E-book Reading (Google Play Books/Koodo)\nMicrosoft TTS + Whisper STT"),
             font=("Segoe UI", 8), fg="#6B7280", bg="#121A15",
             wraplength=320, justify="center").pack(pady=(0, 10))

    link_frame = tk.Frame(card, bg="#121A15")
    link_frame.pack(pady=(0, 8))
    gh_lbl = tk.Label(link_frame, text=_pd("GitHub 项目主页", "GitHub Project"), font=("Segoe UI", 9, "underline"),
                      fg="#34D399", bg="#121A15", cursor="hand2")
    gh_lbl.pack(side="left", padx=8)
    gh_lbl.bind("<Button-1>", lambda _e: webbrowser.open(GITHUB_URL))
    kofi_frame = tk.Frame(link_frame, bg="#121A15", cursor="hand2")
    kofi_frame.pack(side="left", padx=8)
    try:
        from PIL import Image, ImageTk
        _base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        _kofi_img = Image.open(str(_base / "kofi_badge.png"))
        _kofi_img = _kofi_img.resize((24, 24), Image.LANCZOS)
        _kofi_photo = ImageTk.PhotoImage(_kofi_img)
        kofi_icon = tk.Label(kofi_frame, image=_kofi_photo, bg="#121A15", cursor="hand2")
        kofi_icon.image = _kofi_photo  # 保持引用防止垃圾回收
        kofi_icon.pack(side="left")
    except Exception:
        kofi_icon = tk.Label(kofi_frame, text="\u2615", font=("Segoe UI", 9, "bold"),
                              fg="white", bg="#13C3FF", padx=4, pady=1, cursor="hand2")
        kofi_icon.pack(side="left")
    kofi_lbl = tk.Label(kofi_frame, text=_pd("去 Ko-fi 支持", "Support on Ko-fi"), font=("Segoe UI", 9, "underline"),
                        fg="#34D399", bg="#121A15", cursor="hand2")
    kofi_lbl.pack(side="left", padx=(0, 0))
    for w in (kofi_frame, kofi_icon, kofi_lbl):
        w.bind("<Button-1>", lambda _e: webbrowser.open(KO_FI_URL))

    tk.Label(card, text=_pd("开源协议：MIT License", "License: MIT License"), font=("Segoe UI", 8),
             fg="#6B7280", bg="#121A15").pack(pady=(0, 12))

    tk.Button(card, text=_pd("关闭", "Close"), command=win.destroy,
              bg="#10B981", fg="#090D0A", font=("Segoe UI", 9, "bold"),
              bd=0, highlightthickness=0, padx=24, pady=6, cursor="hand2",
              activebackground="#34D399", activeforeground="#090D0A").pack(pady=(0, 12))


def open_stt_styles_dialog(parent, cfg, save_cfg, on_saved=None) -> None:
    """自定义风格配置：左侧最多 5 个风格（增/重命名/删），右侧 Prompt 积木编辑。"""
    import tkinter as tk
    from tkinter import ttk, messagebox

    styles = cfg.setdefault("stt_styles", [])
    win = tk.Toplevel(parent)
    win.title(_pd("自定义风格配置", "Custom Styles"))
    win.configure(bg=BG)
    win.geometry("800x480")
    win.transient(parent)
    win.grab_set()

    card = tk.Frame(win, bg=CARD, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=BORDER)
    card.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

    left = ttk.Frame(card, style="Card.TFrame")
    left.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=4)
    ttk.Label(left, text=_pd("风格列表（最多 5 个）", "Styles (max 5)"), style="Card.TLabel").pack(anchor="w")

    list_frame = tk.Frame(left, bg=CARD)
    list_frame.pack(fill=tk.Y, pady=6)
    selected_idx = [-1]
    edit_row = [-1]  # 正在行内编辑的槽位
    row_widgets = []

    def highlight():
        for i, (row, lbl, _p, _t) in enumerate(row_widgets):
            bg = INPUT if i == selected_idx[0] else CARD
            row.config(bg=bg)
            if isinstance(lbl, tk.Label):
                lbl.config(bg=bg)

    def commit_edit(i, ent):
        edit_row[0] = -1
        name = ent.get().strip()
        if i < len(styles):
            s = styles[i]
            if name:
                s["name"] = name
            elif not s.get("name"):
                del styles[i]  # 新建但未命名：取消该条目
        refresh_list()

    def refresh_list():
        for row, _lbl, _p, _t in row_widgets:
            row.destroy()
        row_widgets.clear()
        for i in range(5):  # 5 个固定槽位
            row = tk.Frame(list_frame, bg=CARD)
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.columnconfigure(0, weight=1)
            if i < len(styles):
                s = styles[i]
                if edit_row[0] == i:  # 行内编辑：直接是输入框（平边框），回车/失焦保存
                    ent = _bind_text_menu(tk.Entry(row, width=18, relief=tk.FLAT, bd=0, bg=INPUT, fg=TEXT, insertbackground=TEXT,
                                       highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT))
                    ent.insert(0, s.get("name", ""))
                    ent.grid(row=0, column=0, sticky="ew", padx=(0, 0))
                    ent.focus_set()
                    ent.select_range(0, tk.END)
                    ent.bind("<Return>", lambda e, i=i: commit_edit(i, ent))
                    ent.bind("<FocusOut>", lambda e, i=i: commit_edit(i, ent))
                    lbl = ent
                else:
                    lbl = tk.Label(row, text=s.get("name", _pd("(未命名)", "(unnamed)")), anchor="w", bg=CARD, fg=TEXT, width=16)
                    lbl.grid(row=0, column=0, sticky="ew", padx=(4, 0))
                    lbl.bind("<Button-1>", lambda e, i=i: select_style(i))
                pen = tk.Button(row, text="✏️", relief=tk.FLAT, bd=0, bg=INPUT, fg=TEXT, activebackground=ACCENT, activeforeground="#04120B",
                       font=FONT_BODY, command=lambda i=i: start_edit(i))
                pen.grid(row=0, column=1)
                _bind_tooltip(win, pen, _pd("重命名", "Rename"))
                trash = tk.Button(row, text="🗑", relief=tk.FLAT, bd=0, bg=INPUT, fg=TEXT, activebackground=ACCENT, activeforeground="#04120B",
                       font=FONT_BODY, command=lambda i=i: del_style(i))
                trash.grid(row=0, column=2)
                _bind_tooltip(win, trash, _pd("删除", "Delete"))
            else:
                # 空槽位：右侧一个加号（与编辑/删除按钮同位同尺寸），新建后该行才变成可编辑/可删除
                lbl = tk.Label(row, text="", width=16, bg=CARD, fg=TEXT)
                lbl.grid(row=0, column=0, sticky="ew", padx=(4, 0))
                addb = tk.Button(row, text="➕", relief=tk.FLAT, bd=0, bg=INPUT, fg=TEXT, activebackground=ACCENT, activeforeground="#04120B",
                       font=FONT_BODY, command=lambda i=i: add_style(i))
                addb.grid(row=0, column=1)
                _bind_tooltip(win, addb, _pd("新增", "Add"))
                pen = addb
                trash = None
            row_widgets.append((row, lbl, pen, trash))
        highlight()

    def select_style(i):
        selected_idx[0] = i
        prompt_txt.delete("1.0", tk.END)
        if i < len(styles):
            prompt_txt.insert("1.0", styles[i].get("prompt", ""))
        highlight()

    def start_edit(i):
        edit_row[0] = i
        refresh_list()

    def add_style(i):
        if len(styles) >= 5:
            messagebox.showinfo(_pd("提示", "Notice"), _pd("最多 5 个自定义风格", "Max 5 custom styles"), parent=win)
            return
        # 点加号 → 直接在左侧行内输入名称（不弹框）
        styles.insert(i, {"name": "", "prompt": ""})
        edit_row[0] = i
        refresh_list()

    def del_style(i):
        if messagebox.askyesno(_pd("删除", "Delete"), _pd("删除该风格？", "Delete this style?"), parent=win):
            del styles[i]
            selected_idx[0] = -1
            refresh_list()

    right = ttk.Frame(card, style="Card.TFrame")
    right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=4)
    ttk.Label(right, text=_pd("Prompt（你的风格积木）", "Prompt (your building block)"), style="Card.TLabel").pack(anchor="w")
    prompt_txt = _bind_text_menu(tk.Text(right, height=15, width=48, wrap="word", bg=INPUT, fg=TEXT,
                          insertbackground=TEXT, font=FONT_BODY, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                          padx=8, pady=6))
    prompt_txt.pack(fill=tk.BOTH, expand=True, pady=6)
    ttk.Label(
        right,
        text=_pd("系统会自动拼接「保留原语言 / 翻译」指令，无需在此重复。", "Language/translation instructions are appended automatically; do not repeat them here."),
        style="Card.Muted.TLabel",
    ).pack(anchor="w")

    def save():
        if 0 <= selected_idx[0] < len(styles):
            styles[selected_idx[0]]["prompt"] = prompt_txt.get("1.0", tk.END).strip()
        save_cfg(cfg)
        if on_saved:
            on_saved()
        ui_log(_pd("自定义风格已保存", "Custom styles saved"))
        win.destroy()

    save_btn = tk.Button(
        right, text=_pd("保存", "Save"), command=save,
        bg=ACCENT, fg=BG, activebackground=ACCENT2, activeforeground=BG,
        font=FONT_STRONG, relief="flat", bd=0, padx=16, pady=6, cursor="hand2",
    )
    save_btn.bind("<Enter>", lambda e: save_btn.config(bg=ACCENT2))
    save_btn.bind("<Leave>", lambda e: save_btn.config(bg=ACCENT))
    save_btn.pack(anchor="e")

    refresh_list()
    win.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - win.winfo_reqwidth()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - win.winfo_reqheight()) // 3)
    win.geometry(f"+{x}+{y}")


def _hotkey_display(combo: str) -> str:
    """'ctrl+win' -> 'CTRL+Win'；'alt+space' -> 'ALT+Space'；'double_ctrl' -> 'Double Ctrl (Hold)'"""
    if (combo or "").strip().lower() == "double_ctrl":
        return "Double Ctrl (Hold)"
    names = {"ctrl": "CTRL", "alt": "ALT", "win": "Win", "shift": "Shift", "space": "Space"}
    return "+".join(names.get(p.strip().lower(), p.strip().title()) for p in (combo or "ctrl+win").split("+") if p.strip())


def open_stt_hotkey_dialog(parent, cfg, save_cfg, on_saved=None) -> None:
    """自定义语音输入快捷键：在输入框里按下组合键即预览，点保存立即生效。"""
    import tkinter as tk
    from tkinter import ttk, messagebox

    win = tk.Toplevel(parent)
    win.title(_pd("自定义快捷键", "Customize Hotkey"))
    win.configure(bg=BG)
    win.geometry("460x340")
    win.transient(parent)
    win.grab_set()

    # 卡片容器：暗苔绿黑 + 1px 边框（与主界面卡片一致）
    card = tk.Frame(win, bg=CARD, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=BORDER)
    card.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

    # 顶部提示：淡灰小字
    top_hint_text = _pd("选择触发方式，保存后立即生效。", "Select trigger mode, takes effect immediately after saving.")
    top_hint = tk.Label(
        card, text=top_hint_text,
        bg=CARD, fg="#9CA3AF", font=FONT_SMALL, wraplength=380, justify="left",
    )
    top_hint.pack(pady=(2, 14), padx=16, anchor="w")
    _bind_tooltip(win, top_hint, top_hint_text)

    cur = [cfg.get("stt_hotkey", "ctrl+win")]
    is_double = (cur[0].strip().lower() == "double_ctrl")
    mode_var = tk.StringVar(value="double_ctrl" if is_double else "combo")

    def combo_from_event(ev) -> str:
        # Windows 上 Tk 的 event.state 不一定反映 Win 键位，改用物理键状态查询，最可靠
        try:
            import ctypes
            def down(vk):
                return bool(ctypes.windll.user32.GetKeyState(vk) & 0x8000)
            mods = []
            if down(0x11):
                mods.append("ctrl")
            if down(0x12):
                mods.append("alt")
            if down(0x5B) or down(0x5C):
                mods.append("win")
            if down(0x10):
                mods.append("shift")
        except Exception:
            mods = []
            if ev.state & 0x0004:
                mods.append("ctrl")
            if ev.state & 0x0008:
                mods.append("alt")
            if ev.state & 0x20000:
                mods.append("win")
            if ev.state & 0x0001:
                mods.append("shift")
        key = (ev.keysym or "").lower()
        if key in ("shift_l", "shift_r", "control_l", "control_r", "alt_l", "alt_r", "super_l", "super_r", "win_l", "win_r"):
            key = ""
        return "+".join(mods + ([key] if key else []))

    # ---- 选项 1 卡片：自定义组合键 ----
    card1 = tk.Frame(card, bg=INPUT, highlightthickness=1,
                     highlightbackground="#27372D", highlightcolor="#27372D")
    card1.pack(fill=tk.X, padx=16, pady=(0, 8))
    card1_inner = tk.Frame(card1, bg=INPUT)
    card1_inner.pack(fill=tk.X, padx=10, pady=8)

    rb1_text = _pd("自定义组合键", "Custom key combination")
    rb1 = tk.Radiobutton(
        card1_inner, text=rb1_text,
        variable=mode_var, value="combo",
        bg=INPUT, fg="#D1D5DB", selectcolor="#1A261F",
        activebackground=INPUT, activeforeground=ACCENT2,
        font=FONT_BODY, bd=0, highlightthickness=0, cursor="hand2",
    )
    rb1.pack(side=tk.LEFT)
    _bind_tooltip(win, rb1, rb1_text)

    # 录入框：绿框 #10B981 + 深色背景 #121A15 + 高亮文字
    entry = _bind_text_menu(tk.Entry(card1_inner, font=FONT_HOTKEY, justify="center",
                     bg="#121A15", fg=ACCENT2, insertbackground=ACCENT2,
                     relief="flat", bd=0, highlightthickness=1,
                     highlightbackground=ACCENT, highlightcolor=ACCENT,
                     width=14))
    entry.pack(side=tk.RIGHT, padx=(8, 0))

    def on_key(ev):
        if mode_var.get() != "combo":
            return "break"
        combo = combo_from_event(ev)
        if combo:
            cur[0] = combo
            entry.delete(0, tk.END)
            entry.insert(0, _hotkey_display(combo))
        return "break"

    entry.bind("<KeyPress>", on_key)

    # ---- 选项 2 卡片：双击长按 Control ----
    card2 = tk.Frame(card, bg=INPUT, highlightthickness=1,
                     highlightbackground="#27372D", highlightcolor="#27372D")
    card2.pack(fill=tk.X, padx=16, pady=(0, 8))
    card2_inner = tk.Frame(card2, bg=INPUT)
    card2_inner.pack(fill=tk.X, padx=10, pady=8)

    rb2_text = _pd("双击长按 Control", "Double-tap & hold Control")
    rb2 = tk.Radiobutton(
        card2_inner, text=rb2_text,
        variable=mode_var, value="double_ctrl",
        bg=INPUT, fg="#D1D5DB", selectcolor="#1A261F",
        activebackground=INPUT, activeforeground=ACCENT2,
        font=FONT_BODY, bd=0, highlightthickness=0, cursor="hand2",
    )
    rb2.pack(anchor="w")
    _bind_tooltip(win, rb2, rb2_text)

    card2_desc_text = _pd("第二下按住讲话，松开自动发送。", "Hold on the second press to speak, release to send.")
    card2_desc = tk.Label(
        card2_inner, text=card2_desc_text,
        bg=INPUT, fg="#6B7280", font=FONT_SMALL, justify="left",
        wraplength=340,
    )
    card2_desc.pack(anchor="w", padx=(22, 0), pady=(2, 0))
    _bind_tooltip(win, card2_desc, card2_desc_text)

    # ---- 联动逻辑 ----
    def _apply_mode():
        if mode_var.get() == "double_ctrl":
            cur[0] = "double_ctrl"
            entry.delete(0, tk.END)
            entry.insert(0, _hotkey_display("double_ctrl"))
            # 禁用：背景变暗 #1E2923，文字变灰
            entry.config(state="disabled", disabledbackground="#1E2923", disabledforeground="#6B7280")
            card1.config(highlightbackground="#1E2923", highlightcolor="#1E2923")
        else:
            if cur[0] == "double_ctrl":
                cur[0] = "ctrl+win"
            entry.config(state="normal")
            entry.delete(0, tk.END)
            entry.insert(0, _hotkey_display(cur[0]))
            card1.config(highlightbackground=ACCENT, highlightcolor=ACCENT)

    rb1.config(command=_apply_mode)
    rb2.config(command=_apply_mode)
    _apply_mode()

    # ---- 保存按钮 ----
    def save():
        combo = cur[0].strip().lower()
        if mode_var.get() == "combo" and not any(m in combo for m in ("ctrl", "alt", "win", "shift")):
            messagebox.showinfo(
                _pd("提示", "Notice"),
                _pd("请至少包含一个修饰键（Ctrl / Alt / Win / Shift）。",
                    "Include at least one modifier key (Ctrl / Alt / Win / Shift)."),
                parent=win,
            )
            return
        cfg["stt_hotkey"] = combo
        save_cfg(cfg)
        if on_saved:
            on_saved()
        win.destroy()

    save_btn = tk.Button(
        card, text=_pd("保存", "Save"), command=save,
        bg=ACCENT, fg=BG, activebackground=ACCENT2, activeforeground=BG,
        font=FONT_STRONG, relief="flat", bd=0, padx=32, pady=8, cursor="hand2",
    )
    save_btn.bind("<Enter>", lambda e: save_btn.config(bg=ACCENT2))
    save_btn.bind("<Leave>", lambda e: save_btn.config(bg=ACCENT))
    save_btn.pack(pady=(12, 4))
    win.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - win.winfo_reqwidth()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - win.winfo_reqheight()) // 3)
    win.geometry(f"+{x}+{y}")


# Groq 限速：ASR 20/min、LLM 30/min → 门控各留余量（ASR 18、LLM 28）。
# 流式窗口被限流则跳过（音频由收尾统一补转）；LLM 调用等待至可用或超时。
_rate_ts: dict = {"asr": [], "llm": []}
_RATE_LIMIT: dict = {"asr": 18, "llm": 28}


def _rate_check(kind: str) -> bool:
    """令牌门控：61s 窗口内调用数 < 上限则记录并放行。"""
    now = time.time()
    _rate_ts[kind] = [t for t in _rate_ts[kind] if now - t < 61.0]
    if len(_rate_ts[kind]) >= _RATE_LIMIT[kind]:
        return False
    _rate_ts[kind].append(now)
    return True


def _rate_wait(kind: str, deadline: float) -> bool:
    """等待至可用或超时（留 1s 余量）。返回是否可用。"""
    while time.time() < deadline - 1.0:
        if _rate_check(kind):
            return True
        time.sleep(0.5)
    return False




def run_gui(test_hook=None):
    _install_crash_hook()
    set_windows_app_id()
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    cfg = load_config()
    # 预制示例风格（仅首次：键不存在时），用户可编辑/删除
    if "stt_styles" not in cfg:
        cfg["stt_styles"] = [
            {
                "name": {
                    "zh-CN": "Karwai Wong", "zh-TW": "Karwai Wong",
                    "en": "Karwai Wong", "ja": "Karwai Wong",
                    "ko": "Karwai Wong", "es": "Karwai Wong",
                    "fr": "Karwai Wong", "de": "Karwai Wong",
                }.get(_LANG, "Karwai Wong"),
                "prompt": (
                    "Role: You are a scriptwriter specializing in Wong Kar-wai's signature cinematic monologue style.\n\n"
                    "Task: Rewrite the user's input into a reflective, poetic, and atmospheric monologue reminiscent of classic Hong Kong cinema (e.g., Chungking Express, In the Mood for Love).\n\n"
                    "Style Guidelines:\n"
                    "1. Temporal Anchors: Frequently frame thoughts around ultra-specific timestamps, precise distances, or shelf-life expiration dates (e.g., \"At 0.01mm apart,\" \"57 minutes past midnight,\" \"Canned pineapples expiring on May 1st\").\n"
                    "2. Sensory & Visual Imagery: Evoke neon lights, rain-slicked streets, lingering smoke, retro songs, and quiet solitary moments.\n"
                    "3. Tone: Melancholic, nostalgic, detached yet emotionally deeply yearning. Use short, rhythmic sentences with reflective pauses.\n"
                    "4. Core Retention: Keep the essential meaning or main event from the original speech, but reframe it as a memory or interior monologue.\n\n"
                    "Output Constraint:\n"
                    "Output ONLY the final polished text in the requested target language. Do NOT add meta commentary, markdown formatting, or cinematic scene directions (like [Camera cuts])."
                ),
            }
        ]
        save_config(cfg)
    # 迁移旧风格名 → Karwai Wong（已有配置也生效，界面统一显示新名）
    _migrated = False
    for _s in cfg.get("stt_styles", []):
        _n = _s.get("name")
        if isinstance(_n, str) and _n in (
            "王家卫风", "王家衛風", "Karwai Wong Style",
            "ウォン・カーウァイ・スタイル", "왕가위 스타일",
            "Estilo Wong Kar-wai", "Style Wong Kar-wai", "Wong Kar-wai Stil",
        ):
            _s["name"] = "Karwai Wong"
            _migrated = True
    if _migrated:
        save_config(cfg)
    root = tk.Tk()
    root.title(t("title"))
    root.geometry("530x400")
    root.minsize(500, 400)   # 最小尺寸：宽 500 防三卡片文字截断，高 450

    # ============ Moss Black 主题（常量见模块级定义） ============
    root.configure(bg=BG)
    style = ttk.Style()
    try:
        style.theme_use("default")  # 确保自定义样式全面生效
    except Exception:
        pass
    style.configure(".", background=BG, foreground=TEXT, font=FONT_BODY,
                    fieldbackground=INPUT, bordercolor=BORDER, lightcolor=BORDER,
                    darkcolor=BORDER, troughcolor=BG)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_BODY)
    style.configure("Muted.TLabel", foreground=MUTED2)
    style.configure("Accent.TLabel", foreground=ACCENT2)
    style.configure("TButton", background=INPUT, foreground=TEXT, bordercolor=BORDER,
                    font=FONT_BODY, padding=(10, 4))
    style.map("TButton", background=[("active", ACCENT), ("pressed", ACCENT)],
              foreground=[("active", "#04120B")])
    # ---- Combobox：框内深苔绿 + 亮白文字 + 薄荷箭头，下拉弹窗同步暗色 ----
    style.configure("TCombobox",
                    fieldbackground=INPUT,   # 框内背景色：深苔绿 #1A261F
                    background=BORDER,       # 右侧下拉箭头按钮背景 #27372D
                    foreground=TEXT,         # 文字颜色：高亮微绿白 #ECFDF5
                    darkcolor=INPUT, lightcolor=INPUT,
                    bordercolor=BORDER, arrowcolor=ACCENT2, insertcolor=TEXT, padding=3)
    style.map("TCombobox",
              fieldbackground=[("readonly", INPUT)],
              foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", ACCENT)],
              selectforeground=[("readonly", BG)],
              bordercolor=[("focus", ACCENT)])
    # ---- Entry：与 Combobox 同暗色，避免弹窗内输入框默认白底 ----
    style.configure("TEntry",
                    fieldbackground=INPUT, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    insertcolor=TEXT, padding=3)
    style.map("TEntry",
              bordercolor=[("focus", ACCENT)],
              fieldbackground=[("disabled", BG)],     # 禁用输入框：背景更暗，与可填框区分
              foreground=[("disabled", MUTED2)])      # 禁用输入框：文字冷灰
    style.configure("Paste.TButton", background=INPUT, foreground=ACCENT2, bordercolor=BORDER,
                    font=FONT_SMALL, padding=(8, 2))
    style.map("Paste.TButton", background=[("active", BORDER)])
    # 次要按钮（暗绿底 + 冷灰文字）与主按钮（翡翠绿底 + 深色粗体）
    style.configure("Muted.TButton", background=INPUT, foreground="#9CA3AF", bordercolor=BORDER,
                    font=FONT_BODY, padding=(6, 2))
    style.map("Muted.TButton", background=[("active", BORDER)])
    style.configure("Primary.TButton", background=ACCENT, foreground="#090D0A", bordercolor=ACCENT,
                    font=(UI_FONT_FAMILY, 9, "bold"), padding=(14, 5))
    style.map("Primary.TButton",
              background=[("active", ACCENT2), ("disabled", "#374151")],
              foreground=[("active", "#090D0A"), ("disabled", "#6B7280")])
    # 下拉弹出列表（Popdown Listbox）暗色化
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", BG)
    # ---- Radio/Checkbutton：未选中 #9CA3AF，选中高亮 #34D399 ----
    style.configure("TCheckbutton", background=BG, foreground="#9CA3AF", font=FONT_BODY,
                    indicatorbackground=INPUT)
    style.map("TCheckbutton", background=[("active", BG)],
              foreground=[("selected", ACCENT2), ("active", "#9CA3AF")],
              indicatorbackground=[("selected", ACCENT)])
    style.configure("TRadiobutton", background=BG, foreground="#9CA3AF", font=FONT_BODY,
                    indicatorbackground=INPUT)
    style.map("TRadiobutton", background=[("active", BG)],
              foreground=[("selected", ACCENT2), ("active", "#9CA3AF")],
              indicatorbackground=[("selected", ACCENT)])
    style.configure("TSeparator", background=BORDER)

    # ---- 弹窗卡片样式（Toplevel 子窗口内统一 CARD 背景） ----
    style.configure("Card.TFrame", background=CARD)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=FONT_BODY)
    style.configure("Card.Muted.TLabel", background=CARD, foreground=MUTED2)
    style.configure("Card.TRadiobutton", background=CARD, foreground="#9CA3AF", font=FONT_BODY,
                    indicatorbackground=INPUT)
    style.map("Card.TRadiobutton", background=[("active", CARD)],
              foreground=[("selected", ACCENT2), ("active", "#9CA3AF")],
              indicatorbackground=[("selected", ACCENT)])
    style.configure("Card.TLabelframe", background=CARD, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER)
    style.configure("Card.TLabelframe.Label", background=CARD, foreground=ACCENT2, font=FONT_BODY)
    style.configure("Status.TLabel", background=BG, foreground="#6B7280",
                    font=(UI_FONT_FAMILY, 8))

    apply_window_icons(root)

    if test_hook:
        test_hook(root, cfg)

    body_row = ttk.Frame(root, style="TFrame")
    root.columnconfigure(0, weight=1)  # 左侧主界面（按需伸缩）
    root.columnconfigure(1, weight=0)  # 右侧日志抽屉（固定宽度，不抢占左侧）
    root.rowconfigure(0, weight=1)     # 内容区占满剩余
    body_row.grid(row=0, column=0, sticky="nsew", padx=20, pady=(15, 0))
    body_row.columnconfigure(0, weight=1)  # 主内容区占满
    body_row.columnconfigure(1, weight=0)  # 箭头按钮
    right = ttk.Frame(body_row, style="TFrame")
    right.grid(row=0, column=0, sticky="nsew")

    # 底部常驻条：赞助链接（左）+ 开机自启动（右），任何场景下固定显示
    bottom = ttk.Frame(root, style="TFrame")
    # grid 固定底部行：常驻条（sponsor/开机自启）任何场景与高度下都完整可见
    bottom.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 12))
    bottom.columnconfigure(0, weight=1)  # sponsor 区（含 ℹ）可扩展
    bottom.columnconfigure(1, weight=0)  # 语言切换器固定宽度
    bottom.columnconfigure(2, weight=0)  # 开机自启固定宽度
    sponsor_img = tk.PhotoImage(data=KO_FI_ICON_B64)
    sponsor_row = tk.Frame(bottom, bg=BG)
    sponsor_row.grid(row=0, column=0, sticky="w")
    sponsor_lbl = tk.Label(
        sponsor_row,
        image=sponsor_img,
        text=t("sponsor"),
        compound="left",
        fg="#4B5563",
        bg=BG,
        font=FONT_SMALL,
        cursor="hand2",
        justify="left",
        anchor="w",
        wraplength=160,
    )
    sponsor_lbl.pack(side=tk.LEFT)
    sponsor_lbl.bind("<Button-1>", lambda _e: webbrowser.open(KO_FI_URL))
    root._fewtype_sponsor_img = sponsor_img  # 防止图片被 GC 回收

    # 关于按钮（微型 ℹ）：紧贴「支持 FewType」右侧
    info_btn = tk.Label(
        sponsor_row, text="\u2139", font=("Segoe UI", 11, "bold"),
        fg="#9CA3AF", bg=BG, cursor="hand2", padx=4,
    )
    info_btn.pack(side=tk.LEFT, padx=(2, 0))
    info_btn.bind("<Button-1>", lambda _e: open_about_dialog(root))
    info_btn.bind("<Enter>", lambda _e: info_btn.config(fg="#34D399"))
    info_btn.bind("<Leave>", lambda _e: info_btn.config(fg="#9CA3AF"))
    _bind_tooltip(root, info_btn, _pd("关于", "About"))

    # ---- 极简语言切换器（无框 Label + 上拉菜单）----
    # 显示当前语言，默认灰色，悬停翡翠绿，点击向上弹出单选菜单
    _lang_display = {
        "en": "🌐 English ▾",
        "zh-CN": "🌐 简体中文 ▾",
        "zh-TW": "🌐 繁體中文 ▾",
    }
    lang_lbl = tk.Label(
        bottom,
        text=_lang_display.get(_LANG, "🌐 English ▾"),
        fg="#9CA3AF", bg=BG,
        font=FONT_SMALL,
        cursor="hand2",
        padx=6,
    )
    lang_lbl.grid(row=0, column=1, sticky="e", padx=(0, 8))
    lang_lbl.bind("<Enter>", lambda _e: lang_lbl.config(fg="#10B981"))
    lang_lbl.bind("<Leave>", lambda _e: lang_lbl.config(fg="#9CA3AF"))

    def _show_lang_menu(event):
        """点击语言 Label，在文字正上方弹出单选菜单。"""
        m = tk.Menu(root, tearoff=0,
                    bg="#1A261F", fg="#D1D5DB",
                    activebackground="#10B981", activeforeground="#090D0A",
                    bd=0, relief="flat")
        for code, label in [("en", "English"), ("zh-CN", "简体中文"), ("zh-TW", "繁體中文")]:
            m.add_radiobutton(
                label=label,
                value=code,
                command=lambda c=code: _switch_language(c),
            )
        # 向上弹出：菜单底部对齐 Label 顶部
        x = event.widget.winfo_rootx()
        y = event.widget.winfo_rooty() - 1
        m.tk.call("tk_popup", m, x, y)

    lang_lbl.bind("<Button-1>", _show_lang_menu)

    def _switch_language(lang_code: str):
        """切换界面语言：保存配置 → 更新全局变量 → 自动重启生效。
        Tkinter 无内置国际化，已创建的 widget 文本无法即时刷新，故采用重启方案。"""
        global _LANG
        _LANG = lang_code
        cfg["ui_lang"] = lang_code
        save_config(cfg)
        # 延迟 100ms 后重启，让菜单先关闭
        root.after(100, _restart_app)

    def _restart_app():
        """重启当前程序（exe 或 py 模式均适用）。"""
        try:
            root.destroy()
        except Exception:
            pass
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv)

    auto_var = tk.BooleanVar(value=bool(cfg.get("autostart")))

    def on_auto():
        cfg["autostart"] = bool(auto_var.get())
        save_config(cfg)
        set_autostart(cfg["autostart"])

    tk.Checkbutton(
        bottom, text=t("autostart"), variable=auto_var, command=on_auto,
        fg="#4B5563", bg=BG, activebackground=BG, activeforeground="#4B5563",
        selectcolor=CARD, font=FONT_SMALL, highlightthickness=0, bd=0,
        cursor="hand2",
    ).grid(row=0, column=2, sticky="e")

    # ================ 右侧运行日志抽屉（默认折叠，任意场景可打开） ================
    _log_open = [False]
    _LOG_W = 300
    log_panel = ttk.Frame(root, style="TFrame")
    log_panel.pack_propagate(False)
    log_panel.configure(width=320)
    log_head = ttk.Label(log_panel, text=t("log_title"), style="Accent.TLabel", font=FONT_SMALL)
    log_head.pack(anchor="w", pady=(6, 4), padx=10)
    log_wrap = ttk.Frame(log_panel, style="TFrame")
    log_wrap.pack(fill=tk.BOTH, expand=True)
    log_text = _bind_text_menu(tk.Text(
        log_wrap, wrap=tk.WORD, font=FONT_LOG,
        bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG,
        relief="flat", bd=0, highlightthickness=0, padx=8, pady=4,
    ))
    log_sb = tk.Scrollbar(
        log_wrap, width=6, bg=LOG_BG, troughcolor=BG, activebackground=INPUT,
        relief="flat", bd=0, highlightthickness=0,
    )
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    log_sb.pack(side=tk.RIGHT, fill=tk.Y)
    log_sb.config(command=log_text.yview)
    log_text.config(yscrollcommand=log_sb.set)
    log_text.configure(state=tk.DISABLED)

    def append_log_line(line: str):
        log_text.configure(state=tk.NORMAL)
        log_text.insert(tk.END, line + "\n")
        log_text.see(tk.END)
        log_text.configure(state=tk.DISABLED)

    def poll_logs():
        try:
            while True:
                append_log_line(log_queue.get_nowait())
        except queue.Empty:
            pass
        root.after(100, poll_logs)

    _anim_w = {"after": None}

    def _animate_width(start: int, end: int, after=None):
        if _anim_w["after"] is not None:
            try:
                root.after_cancel(_anim_w["after"])
            except Exception:
                pass
            _anim_w["after"] = None
        step = 16 if end > start else -16
        cur = [start]

        def tick():
            cur[0] += step
            if (step > 0 and cur[0] >= end) or (step < 0 and cur[0] <= end):
                cur[0] = end
                root.geometry(f"{cur[0]}x{root.winfo_height()}")
                _anim_w["after"] = None
                if after:
                    try:
                        after()
                    except Exception:
                        pass
                return
            root.geometry(f"{cur[0]}x{root.winfo_height()}")
            _anim_w["after"] = root.after(8, tick)

        tick()

    def _toggle_log():
        _h = 520 if scene_var.get() == "scene2" else 400
        if _log_open[0]:
            log_btn.configure(text="‹")
            log_panel.grid_remove()
            root.geometry("530x%d" % _h)  # 收起时恢复标准宽度
        else:
            log_panel.grid(row=0, column=1, sticky="nsew")
            log_panel.config(width=320)  # 强制日志区固定宽度 320px
            log_btn.configure(text="›")
            root.geometry("850x%d" % _h)  # 展开时向右延伸 320px，左侧 530px 保持原封不动
        _log_open[0] = not _log_open[0]

    log_btn = tk.Label(
        body_row, text="‹", font=FONT_BODY, fg=ACCENT2, bg=BG,
        cursor="hand2", padx=3, pady=4,
    )
    log_btn.grid(row=0, column=1, sticky="ne")  # 主界面右上角，始终可见
    _bind_tooltip(root, log_btn, _pd("运行日志", "Log"))
    # tooltip 用 add="+" 追加绑定，不会覆盖；这里再追加悬停高亮与抽屉切换
    log_btn.bind("<Enter>", lambda _e: log_btn.configure(bg=INPUT), add="+")
    log_btn.bind("<Leave>", lambda _e: log_btn.configure(bg=BG), add="+")
    log_btn.bind("<Button-1>", lambda _e: _toggle_log(), add="+")
    root._fewtype_toggle_log = _toggle_log  # 测试钩子：回归冒烟直接调用
    poll_logs()

    # 语音服务配置状态（供各场景状态行/配置对话框刷新）
    prov_cfg = cfg.get("provider", {}) or {}
    prov_status = tk.StringVar(
        value=(
            _pd("已配置：", "Configured: ")
            + (PROVIDERS[prov_cfg["platform"]].name if prov_cfg.get("platform") in PROVIDERS else _pd("自定义/未知", "Custom/Unknown"))
            if prov_cfg.get("api_key")
            else _pd("未配置（翻译/语音输入需要）", "Not configured (needed for translation / voice input)")
        )
    )

    # ================ 顶部导航：三大场景等宽卡片（向两边顶格 + 2px 荧光指示线） ================
    scene_var = tk.StringVar(value="scene3")
    tab_holder = tk.Frame(right, bg=BG)
    tab_holder.pack(fill=tk.X, pady=(0, 6))
    for _i in range(3):
        tab_holder.columnconfigure(_i, weight=1, uniform="scene")  # uniform 组强制三列严格等宽
    tabs: dict = {}

    def _pick_scene(key: str):
        scene_var.set(key)
        _update_tabs()
        show_scene()
    root._fewtype_pick_scene = _pick_scene  # 测试钩子：回归验证直接调用（后台窗口 event_generate 不可靠）

    def _update_tabs():
        for k, (cell, lbl, line) in tabs.items():
            sel = scene_var.get() == k
            lbl.configure(fg=ACCENT2 if sel else TEXT)
            line.configure(bg=ACCENT if sel else BG)
            cell.configure(highlightbackground=ACCENT if sel else BORDER,
                           highlightcolor=ACCENT if sel else BORDER)


    scene1 = ttk.Frame(right)
    scene2 = ttk.Frame(right)
    scene3 = ttk.Frame(right)

    def show_scene():
        for f in (scene1, scene2, scene3):
            f.pack_forget()
        cur = {"scene1": scene1, "scene2": scene2, "scene3": scene3}[scene_var.get()]
        cur.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        if cur is scene2:
            # 场景2 内容较高：窗口下延，确保底部常驻条（sponsor/开机自启）与
            # 输出预览、生成音频、打开输出文件夹等按钮不被窗口下沿裁掉
            root.minsize(500, 620)
            if root.winfo_height() != 620:
                root.geometry("500x620")
            # 首次 pack 会高估请求高度撑大窗口，布局稳定后再强制一次几何
            root.after(120, lambda: root.geometry("500x620"))
            root.after(100, load_voices)
        else:
            # 其他场景恢复默认高度（固定 500 宽，避免依赖未映射时的 winfo_width）
            root.minsize(500, 400)
            if root.winfo_height() != 450:
                root.geometry("500x400")

    _update_tabs()

    # ---------- 场景 1：电子书朗读（静默服务介绍页，无需任何配置） ----------
    ttk.Label(
        scene1,
        text=_pd(
            "静默运行的服务，无需任何配置。安装浏览器扩展后，打开微信读书 / Google Play Books / Koodo Reader 的阅读页，即可自动朗读。",
            "A silent background service — no configuration needed. Install the browser extension, then open a reading page in Google Play Books / Koodo Reader and it will read aloud automatically.",
        ),
        wraplength=420, justify="left", style="Muted.TLabel",
    ).pack(anchor="w", pady=(8, 12))
    ttk.Separator(scene1, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 10))
    ttk.Label(scene1, text=t("howto"), wraplength=420, justify="left", style="Muted.TLabel").pack(anchor="w")

    # ---------- 场景 2：长文本转语音 ----------
    ttk.Label(
        scene2,
        text=_pd("粘贴文本，输入与输出语言不一致时自动翻译，用任意音色生成音频", "Paste text, auto-translates when input and output languages differ, then generate audio"),
        style="Muted.TLabel",
        wraplength=420,
    ).pack(anchor="w", pady=(0, 4))

    text_wrap = ttk.Frame(scene2)
    text_wrap.pack(fill=tk.X, pady=(0, 4))   # 固定请求高：不把下方按钮推贴底
    text_input = _bind_text_menu(tk.Text(
        text_wrap, height=10, wrap=tk.WORD, bg=INPUT, fg=TEXT, insertbackground=TEXT,
        font=FONT_BODY, relief="flat", bd=0, highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=ACCENT, padx=8, pady=6,
    ))
    text_sb = tk.Scrollbar(text_wrap, width=6, bg=INPUT, troughcolor=BG,
                           activebackground=ACCENT, relief="flat", bd=0, highlightthickness=0)
    text_input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    text_sb.pack(side=tk.RIGHT, fill=tk.Y)
    text_sb.config(command=text_input.yview)
    text_input.config(yscrollcommand=text_sb.set)

    # 控件区：Grid+Sticky 两列（标签列固定 / 控件列 weight=1 拉伸，右缘与大输入框对齐）
    form_frame = ttk.Frame(scene2)
    form_frame.pack(fill=tk.X, pady=(4, 0))
    form_frame.columnconfigure(0, minsize=64)  # 标签列
    form_frame.columnconfigure(1, weight=1)    # 控件列：自动拉伸填充剩余宽度

    # 行1：输出语言 + 语速 + 注释（注释紧随控件 pack，紧贴下拉框）
    ttk.Label(form_frame, text=_pd("输出语言", "Output language")).grid(row=0, column=0, sticky="w")
    row_lang = ttk.Frame(form_frame)
    row_lang.grid(row=0, column=1, sticky="ew")
    target_var = tk.StringVar(value=_default_output_lang())
    target_cb = ttk.Combobox(
        row_lang, textvariable=target_var, values=_output_lang_values(), width=12, state="readonly",
    )
    target_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)
    target_cb.bind("<<ComboboxSelected>>", lambda e: refresh_voices_by_lang())
    rate_var = tk.StringVar(value="+0%")
    rate_cb = ttk.Combobox(
        row_lang, textvariable=rate_var, values=["-50%", "-25%", "-10%", "+0%", "+10%", "+25%", "+50%"], width=5,
    )
    rate_cb.pack(side=tk.LEFT, padx=(4, 0))
    tts_trans_note = ttk.Label(
        row_lang, text=_pd("输入与输出语言一致时自动跳过翻译", "Translation is skipped when input matches output language"),
        style="Muted.TLabel",
    )
    tts_trans_note.pack(side=tk.LEFT, padx=(10, 0))
    _auto_tooltip(root, tts_trans_note)

    # 行2：音色 + 注释（注释紧贴音色下拉框右侧 padx=10）
    ttk.Label(form_frame, text=_pd("音色", "Voice")).grid(row=1, column=0, sticky="w", pady=(4, 0))
    row_voice = ttk.Frame(form_frame)
    row_voice.grid(row=1, column=1, sticky="ew", pady=(4, 0))
    voice_var = tk.StringVar()
    # 固定宽度：右缘与「输出语言+语速」组合对齐，形成整齐矩形。
    # 长音色名会截断显示，可接受（多数人只看性别/国家前缀）。
    voice_cb = ttk.Combobox(row_voice, textvariable=voice_var, width=22)
    voice_cb.pack(side=tk.LEFT)
    tts_voice_note = ttk.Label(
        row_voice, text=_pd("音色为实时抓取，可能需要等待", "Voices are fetched live, may take a moment"),
        style="Muted.TLabel",
    )
    tts_voice_note.pack(side=tk.LEFT, padx=(10, 0))
    _auto_tooltip(root, tts_voice_note)

    preview_frame = ttk.Frame(scene2)
    preview_frame.pack(fill=tk.X, pady=(4, 0))   # 固定请求高：按钮行不贴底
    ttk.Label(preview_frame, text=_pd("输出预览（可编辑，用于生成音频）", "Output preview (editable, used for audio)"), style="Accent.TLabel", font=FONT_SMALL).pack(anchor="w")
    preview_wrap = ttk.Frame(preview_frame)
    preview_wrap.pack(fill=tk.X)
    preview_text = _bind_text_menu(tk.Text(
        preview_wrap, height=6, wrap=tk.WORD, bg=INPUT, fg=TEXT, insertbackground=TEXT,
        font=FONT_BODY, relief="flat", bd=0, highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=ACCENT, padx=8, pady=6,
    ))
    preview_sb = tk.Scrollbar(preview_wrap, width=6, bg=INPUT, troughcolor=BG,
                              activebackground=ACCENT, relief="flat", bd=0, highlightthickness=0)
    preview_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    preview_sb.pack(side=tk.RIGHT, fill=tk.Y)
    preview_sb.config(command=preview_text.yview)
    preview_text.config(yscrollcommand=preview_sb.set)

    btn_row = ttk.Frame(scene2)
    btn_row.pack(fill=tk.X, pady=6)
    ttk.Button(btn_row, text=_pd("输出预览", "Preview output"), command=lambda: do_preview()).pack(side=tk.LEFT, padx=(0, 12))
    preview_lbl = tk.Label(btn_row, text="▶", fg=ACCENT, bg=BG, cursor="hand2", font=FONT_BODY)
    preview_lbl.pack(side=tk.LEFT, padx=(0, 12))
    preview_lbl.bind("<Button-1>", lambda e: preview_voice())
    _bind_tooltip(root, preview_lbl, _pd("试听", "Preview"))
    ttk.Button(btn_row, text=_pd("生成音频", "Generate audio"), command=lambda: do_tts()).pack(side=tk.LEFT)
    btn_open = ttk.Button(btn_row, text=_pd("打开文件夹", "Open folder"), command=lambda: open_output())
    btn_open.pack(side=tk.RIGHT)
    _bind_tooltip(root, btn_open, _pd("打开输出文件夹", "Open output folder"))
    btn_set = ttk.Button(btn_row, text=_pd("指定文件夹", "Set folder"), command=lambda: pick_output_dir())
    btn_set.pack(side=tk.RIGHT, padx=(0, 8))
    _bind_tooltip(root, btn_set, _pd("指定输出文件夹", "Set output folder"))
    scene2_status = tk.StringVar(value="")
    ttk.Label(scene2, textvariable=scene2_status, style="Accent.TLabel", wraplength=420).pack(anchor="w")

    last_tts_path: list[str] = [""]

    TARGET_LOCALE = {
        # 母语名称（中文界面显示）
        "English": "en-US", "简体中文": "zh-CN", "繁體中文": "zh-TW",
        "日本語": "ja-JP", "한국어": "ko-KR", "Español": "es-ES",
        "Français": "fr-FR", "Deutsch": "de-DE",
        # 英文名称（英文界面显示）
        "Chinese (Simplified)": "zh-CN",
        "Chinese (Traditional)": "zh-TW",
        "Japanese": "ja-JP",
        "Korean": "ko-KR",
        "Spanish": "es-ES",
        "French": "fr-FR",
        "German": "de-DE",
    }

    _lang_voices: dict = {}  # locale -> [(shortName, gender), ...]
    _voice_display_to_real: dict = {}  # 显示名 -> 实际 shortName
    _voice_retry = [0]

    _HANT_CHARS = set(
        "這個說時後來裡為與無沒這樣麼還讓過們對點嗎請開關體學書讀話語聽寫見車門風會愛國問題決發現認識覺應夠臺灣東興歡長"
    )

    def _is_cjk_char(ch: str) -> bool:
        """跟电子书 chunking.js 同样的 CJK 判断：中日韩文字符（含谚文/假名）。"""
        o = ord(ch)
        return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
                or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF)

    def _is_latin_letter(ch: str) -> bool:
        # 用 isalpha() 而非 isalpha()+isascii()，否则法语 é/è/à/ç/û、德语 ä/ö/ü 等带重音字母不被识别，会打断单词导致"字"数计算错误
        return ch.isalpha()

    def _is_ascii_digit(ch: str) -> bool:
        return ch.isdigit() and ch.isascii()

    def first_n_chars(text: str, n: int = 20) -> str:
        """跟电子书同样的"字"定义取前 n 个字：CJK每个算1个，拉丁字母连续串算1个，
        数字连续串算1个（小数点不算分隔符），标点/空白不算字但保留在输出里。"""
        count = 0
        i = 0
        in_latin = False
        in_digit = False
        while i < len(text) and count < n:
            ch = text[i]
            if _is_cjk_char(ch):
                count += 1
                in_latin = False
                in_digit = False
            elif _is_latin_letter(ch):
                if not in_latin:
                    count += 1
                    in_latin = True
                in_digit = False
            elif _is_ascii_digit(ch):
                if not in_digit:
                    count += 1
                    in_digit = True
                in_latin = False
            elif ch == ".":
                # 小数点不算分隔符（3.14 算一个数字串）
                pass
            else:
                # 标点/空白/其他符号：不算字，重置状态
                in_latin = False
                in_digit = False
            i += 1
        return text[:i].strip()

    def detect_input_lang(text: str) -> str:
        """启发式语言检测：'zh' / 'zh-hant' / 'ja' / 'ko' / 'other'（拉丁等）。"""
        if re.search(r"[\uAC00-\uD7A3]", text):
            return "ko"
        if re.search(r"[\u3040-\u30FF]", text):
            return "ja"
        han = re.findall(r"[\u4E00-\u9FFF]", text)
        if not han or len(han) / max(len(text), 1) < 0.2:
            return "other"
        hant = sum(1 for ch in han if ch in _HANT_CHARS)
        return "zh-hant" if hant / len(han) > 0.1 else "zh"

    def needs_translation() -> tuple:
        """返回 (是否需要翻译, 目标 locale)。拉丁语言之间不自动翻译（保守）。"""
        src = text_input.get("1.0", tk.END).strip()
        loc = TARGET_LOCALE.get(target_var.get(), "zh-CN")
        if not src:
            return False, loc
        in_lang = detect_input_lang(src)
        out_primary = loc.split("-", 1)[0].lower()
        if in_lang == "other":
            return out_primary not in ("en", "es", "fr", "de"), loc
        if in_lang.startswith("zh"):
            if out_primary != "zh":
                return True, loc
            in_hant = in_lang == "zh-hant"
            out_hant = loc in ("zh-TW", "zh-HK")
            return in_hant != out_hant, loc
        return in_lang != out_primary, loc

    def _format_voice_name(sn: str, gender: str) -> str:
        """去掉 Neural 后缀，加上性别标签。"""
        name = sn
        if name.endswith("Neural"):
            name = name[:-6]
        gender_label = ""
        if gender:
            g = gender.lower()
            if g.startswith("f"):
                gender_label = "♀ "
            elif g.startswith("m"):
                gender_label = "♂ "
        return gender_label + name

    def refresh_voices_by_lang():
        loc = TARGET_LOCALE.get(target_var.get(), "zh-CN")
        primary = loc.split("-", 1)[0].lower()
        # 中文：zh-CN 单独显示（简中音色够多），zh-TW+zh-HK 合并（繁中+粤语）
        # 其他语言：合并同语言所有变体（如西班牙语 es-ES/es-MX/es-AR 全部合并）
        pairs = []
        if primary == "zh":
            if loc == "zh-CN":
                pairs = list(_lang_voices.get("zh-CN", []))
            else:
                pairs = list(_lang_voices.get("zh-TW", [])) + list(_lang_voices.get("zh-HK", []))
        else:
            for k, v in _lang_voices.items():
                if k.split("-", 1)[0].lower() == primary:
                    pairs.extend(v)
        # 去重（按 shortName）
        seen = set()
        unique_pairs = []
        for sn, g in pairs:
            if sn not in seen:
                seen.add(sn)
                unique_pairs.append((sn, g))
        # 英语音色按区域优先级排序：US > GB > 其他
        if primary == "en":
            def _region_priority(item):
                sn = item[0]
                parts = sn.split("-")
                if len(parts) >= 2:
                    region = parts[1].upper()
                    if region == "US":
                        return 0
                    if region == "GB":
                        return 1
                return 2
            unique_pairs.sort(key=_region_priority)
        # 构建显示名→实际名映射
        _voice_display_to_real.clear()
        display_names = []
        for sn, g in unique_pairs:
            disp = _format_voice_name(sn, g)
            _voice_display_to_real[disp] = sn
            display_names.append(disp)
        if display_names:
            voice_cb.configure(values=display_names)
            current_real = _voice_display_to_real.get(voice_var.get(), voice_var.get())
            if current_real in [sn for sn, _ in unique_pairs]:
                for disp, real in _voice_display_to_real.items():
                    if real == current_real:
                        voice_var.set(disp)
                        break
            else:
                voice_var.set(display_names[0])
        else:
            voice_cb.configure(values=[])
            voice_var.set("")

    def load_voices():
        """后台线程拉取音色（主线程只负责调度，避免 /voices 抓取慢时卡死 UI）。"""
        if _voice_retry[0] > 10:
            return
        _voice_retry[0] += 1

        def fetch():
            try:
                import urllib.request
                with urllib.request.urlopen("http://%s:%d/voices" % (HOST, PORT), timeout=25) as r:
                    data = json.loads(r.read().decode("utf-8"))
                grouped = {}
                for v in data.get("voices", []):
                    sn = v.get("shortName") or v.get("short_name")
                    if not sn:
                        continue
                    loc = v.get("locale") or sn.split("-", 1)[0]
                    gender = v.get("gender") or ""
                    grouped.setdefault(loc, []).append((sn, gender))
                if not grouped:
                    raise RuntimeError("no voices")
            except Exception:
                root.after(2000, load_voices)
                return

            def apply():
                _voice_retry[0] = 0
                _lang_voices.clear()
                _lang_voices.update(grouped)
                refresh_voices_by_lang()
                ui_log(_pd("已加载 %d 个音色 / %d 种语言", "Loaded %d voices / %d languages") % (sum(len(x) for x in grouped.values()), len(grouped)))

            root.after(0, apply)

        threading.Thread(target=fetch, daemon=True).start()

    def _provider_from_cfg():
        from provider import Provider
        pc = cfg.get("provider", {}) or {}
        return Provider(
            pc.get("platform", "groq"),
            pc.get("api_key", ""),
            pc.get("asr_model", ""),
            pc.get("llm_model", ""),
            pc.get("base_url", ""),
            pc.get("llm_key", ""),
            pc.get("llm_base_url", ""),
        )

    def _ensure_provider() -> bool:
        if (cfg.get("provider", {}) or {}).get("api_key"):
            return True
        open_provider_dialog(root, cfg, save_config, prov_status)
        return bool((cfg.get("provider", {}) or {}).get("api_key"))

    def _translate_async(src: str, on_done, on_err):
        target = target_var.get()  # 主线程预读：Tk 变量在工作线程访问可能使 Tcl 崩溃
        def work():
            try:
                from provider import build_messages
                sys_p = (
                    "You are a professional translator. Translate the user's text into %s "
                    "in natural, native, idiomatic style. Preserve meaning, tone and structure. "
                    "Output ONLY the translated text, no explanations, no quotes." % target
                )
                out = _provider_from_cfg().chat(build_messages(sys_p, src))
                root.after(0, lambda out=out: on_done(out))
            except Exception as e:
                root.after(0, lambda e=e: on_err(e))

        threading.Thread(target=work, daemon=True).start()

    def do_preview():
        try:
            ui_log("[preview] start")
            src = text_input.get("1.0", tk.END).strip()
            ui_log(f"[preview] src len={len(src)}")
            if not src:
                _show_fewtype_dialog(root, _pd("提示", "Notice"), _pd("请先输入或粘贴文本", "Enter or paste text first"))
                return
            need, _loc = needs_translation()
            ui_log(f"[preview] need_translate={need}, target={_loc}")
            if not need:
                preview_text.delete("1.0", tk.END)
                preview_text.insert("1.0", src)
                scene2_status.set(_pd("输入与输出语言一致，无需翻译，可直接生成", "Input matches output language - generate directly"))
                ui_log("[preview] done (no translate)")
                return
            if not _ensure_provider():
                ui_log("[preview] provider not ready, aborted")
                return
            scene2_status.set(_pd("翻译中…", "Translating…"))
            root.update_idletasks()
            ui_log("[preview] calling _translate_async")
            _translate_async(src, _show_translation, _show_translation_error)
        except Exception as e:
            ui_log(f"[preview] ERROR: {type(e).__name__}: {e}")
            import traceback
            ui_log(traceback.format_exc())

    def _show_translation(out: str):
        preview_text.delete("1.0", tk.END)
        preview_text.insert("1.0", out)
        scene2_status.set(_pd("翻译完成，可编辑后生成音频", "Translated. Edit if needed, then generate"))

    def _show_translation_error(e: Exception):
        scene2_status.set(_pd("翻译失败", "Translation failed") + ": %s" % e)
        ui_log(_pd("翻译失败", "Translation failed") + ": %s" % e)

    def _tts_with_text(text: str):
        if not health_ok():
            messagebox.showwarning(_pd("提示", "Notice"), _pd("请先启动服务", "Start the service first"), parent=root)
            return
        scene2_status.set(_pd("正在合成（长文本自动分段拼接）…", "Synthesizing (chunked for long text)…"))
        root.update_idletasks()
        voice = _voice_display_to_real.get(voice_var.get(), voice_var.get()) or "zh-CN-XiaoxiaoNeural"
        rate = rate_var.get()

        # 共享变量：子线程存结果，主线程轮询
        _result_holder = [None]  # [0]=成功结果, [1]=异常
        _start_time = [time.time()]

        def work():
            """子线程：只做网络请求，把结果存到共享变量，不调用 root.after。"""
            try:
                import urllib.request
                _out_dir = cfg.get("tts_output_dir") or ""
                payload = json.dumps({"text": text, "voice": voice, "rate": rate, "output_dir": _out_dir}).encode("utf-8")
                req = urllib.request.Request(
                    "http://%s:%d/tts_file" % (HOST, PORT),
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    raw = r.read()
                    data = json.loads(raw.decode("utf-8"))
                _result_holder[0] = ("ok", data)
            except Exception as e:
                _result_holder[0] = ("err", e)

        threading.Thread(target=work, daemon=True).start()

        def _poll_result():
            """主线程轮询：检查子线程是否完成，完成则调用 _tts_done/_tts_fail。
            为什么用主线程轮询而不是子线程 root.after：
              子线程里的 root.after 回调在某些情况下不执行（线程安全问题），
              导致按钮永远 disabled。主线程轮询更可靠。
            """
            if _result_holder[0] is not None:
                kind, val = _result_holder[0]
                if kind == "ok":
                    _tts_done(val)
                else:
                    _tts_fail(val)
                return
            # 超时保护：120 秒还没完成，强制恢复按钮
            if time.time() - _start_time[0] > 120:
                _tts_fail(TimeoutError("TTS 超时（120秒）"))
                return
            root.after(200, _poll_result)  # 每 200ms 轮询一次

        root.after(200, _poll_result)

    def preview_voice():
        """试听：输入输出语言一致→用输入文本前20字；不一致→输出预览为空则提示先点输出预览，否则用输出预览前20字。"""
        src = text_input.get("1.0", tk.END).strip()
        if not src:
            _show_fewtype_dialog(root, _pd("提示", "Notice"), _pd("请先输入或粘贴文本", "Enter or paste text first"))
            return
        voice = _voice_display_to_real.get(voice_var.get(), voice_var.get())
        if not voice:
            messagebox.showwarning(_pd("提示", "Notice"), _pd("请先选择音色", "Please select a voice first"), parent=root)
            return
        # 判断输入输出语言是否一致
        need_translate, _ = needs_translation()
        if need_translate:
            # 不一致：检查输出预览框是否已有翻译内容
            preview_content = preview_text.get("1.0", tk.END).strip()
            if not preview_content:
                _show_fewtype_dialog(
                    root,
                    _pd("提示", "Notice"),
                    _pd("输入与输出语言不一致，请先点「输出预览」完成翻译，再试听",
                        "Input and output languages differ. Please click 'Preview output' to translate first, then preview.")
                )
                return
            preview_src = preview_content
        else:
            # 一致：直接用输入文本
            preview_src = src
        preview_text_content = first_n_chars(preview_src, 20)
        if not preview_text_content:
            return
        scene2_status.set(_pd("试听合成中…", "Previewing…") + ": " + preview_text_content)
        root.update_idletasks()

        def work():
            try:
                import tempfile, os
                import urllib.request
                payload = json.dumps({"text": preview_text_content, "voice": voice, "rate": rate_var.get()}).encode("utf-8")
                req = urllib.request.Request(
                    "http://%s:%d/tts_file" % (HOST, PORT),
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode("utf-8"))
                path = data.get("path")
                if not path or not Path(path).exists():
                    raise RuntimeError("audio file not found")
                # 播放：用 Windows 自带的 MCI 接口（winmm.dll），无需额外库，不弹外部窗口
                try:
                    import ctypes
                    alias = "fewtype_preview"
                    # 先关闭可能存在的旧播放
                    ctypes.windll.winmm.mciSendStringW(f"close {alias}", None, 0, None)
                    # 打开文件
                    ctypes.windll.winmm.mciSendStringW(f'open "{path}" alias {alias}', None, 0, None)
                    # 播放
                    ctypes.windll.winmm.mciSendStringW(f"play {alias}", None, 0, None)
                except Exception as play_err:
                    # MCI 失败时 fallback 到系统默认播放器
                    os.startfile(path)
                root.after(0, lambda: scene2_status.set(_pd("试听完成", "Preview done") + ": " + preview_text_content))
            except Exception as e:
                root.after(0, lambda: scene2_status.set(_pd("试听失败", "Preview failed") + ": %s" % e))
                ui_log("试听失败: %s" % e)

        threading.Thread(target=work, daemon=True).start()

    def do_tts():
        print("[tts] do_tts 被调用")  # 调试日志
        text = preview_text.get("1.0", tk.END).strip()
        if not text:
            text = text_input.get("1.0", tk.END).strip()
        print("[tts] 获取到文本, len=", len(text))  # 调试日志
        if not text:
            print("[tts] 文本为空，提示用户")  # 调试日志
            _show_fewtype_dialog(root, _pd("提示", "Notice"), _pd("请先输入或粘贴文本", "Enter or paste text first"))
            return
        need, _loc = needs_translation()
        print("[tts] needs_translation=", need, "loc=", _loc)  # 调试日志
        if need:
            print("[tts] 需要翻译，走翻译分支")  # 调试日志
            if not _ensure_provider():
                print("[tts] _ensure_provider 失败，return")  # 调试日志
                return
            scene2_status.set(_pd("正在翻译…", "Translating…"))
            root.update_idletasks()
            src = text_input.get("1.0", tk.END).strip()
            print("[tts] 调用 _translate_async, src_len=", len(src))  # 调试日志
            _translate_async(src, lambda out: _tts_with_text(out), _show_translation_error)
            return
        print("[tts] 不需要翻译，直接调用 _tts_with_text")  # 调试日志
        _tts_with_text(text)

    def _tts_timeout():
        """TTS 超时保护：如果超过 3 分钟还没完成，自动恢复按钮。"""
        if _tts_busy[0]:
            print("[tts] 超时保护触发，强制恢复按钮")  # 调试日志
            _tts_busy[0] = False
            gen_btn.configure(state="normal")
            scene2_status.set(_pd("生成超时，请重试", "Generation timed out, please retry"))

    def _tts_done(data: dict):
        last_tts_path[0] = data.get("path", "")
        scene2_status.set(
            _pd("已生成", "Done") + ": %s（%d 段，%d KB）" % (data.get("filename"), data.get("segments", 0), data.get("bytes", 0) // 1024)
        )
        ui_log("TTS 文件: %s" % data.get("path"))

    def _tts_fail(e: Exception):
        msg = str(e)
        if "404" in msg:
            msg += _pd("（服务版本过旧，请退出旧版 FewType-bridge 后重试）", " (old bridge version, exit the old FewType-bridge and retry)")
        scene2_status.set(_pd("生成失败", "Failed") + ": %s" % msg)
        ui_log("TTS 生成失败: %s" % e)

    def open_output():
        # 打开优先级：最近生成文件目录 > 用户指定目录 > 默认 tts_output
        d = None
        if last_tts_path[0]:
            d = Path(last_tts_path[0]).parent
        if d is None and cfg.get("tts_output_dir"):
            d = Path(cfg["tts_output_dir"])
        if d is None:
            d = Path(APP_DIR) / "tts_output"
        if d.exists():
            os.startfile(str(d))
        else:
            ui_log(_pd("输出目录还不存在", "Output dir does not exist yet"))

    def pick_output_dir():
        from tkinter import filedialog
        init = str(cfg.get("tts_output_dir") or APP_DIR)
        d = filedialog.askdirectory(
            parent=root, initialdir=init,
            title=_pd("指定输出文件夹", "Choose output folder"),
        )
        if d:
            cfg["tts_output_dir"] = d
            save_config(cfg)
            scene2_status.set(_pd("输出文件夹已指定", "Output folder set") + ": %s" % d)
            ui_log(_pd("输出文件夹", "Output folder") + ": %s" % d)

    # ---------- 场景 3：语音输入（P2） ----------
    def _mk_card(parent):
        c = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
        c.pack(fill=tk.X, pady=(10, 5))
        return c

    def _card_body(card):
        b = tk.Frame(card, bg=CARD)
        b.pack(fill=tk.X, padx=15, pady=10)
        return b

    # ================= 顶部主卡片 =================
    main_card = _mk_card(scene3)
    main_body = _card_body(main_card)

    # ---- 区域 A：快捷键与触发逻辑 ----
    area_a = tk.Frame(main_body, bg=CARD)
    area_a.pack(fill=tk.X)
    hot_btn = ttk.Button(
        area_a,
        command=lambda: open_stt_hotkey_dialog(root, cfg, save_config, on_saved=_restart_hotkey),
    )
    hot_btn.pack(side=tk.LEFT)
    _bind_tooltip(root, hot_btn, _pd("自定义快捷键", "Customize hotkey"))
    hotkey_hint_lbl = ttk.Label(
        area_a, text=_pd("按住说话，松开自动上屏", "Hold to speak, release to commit"),
        foreground="#9CA3AF",
    )
    hotkey_hint_lbl.pack(side=tk.LEFT, padx=(10, 0))
    _auto_tooltip(root, hotkey_hint_lbl)

    def _update_hotkey_btn():
        hot_btn.configure(text=_hotkey_display(cfg.get("stt_hotkey", "ctrl+win")))
        # 说明文字根据热键模式更新
        if (cfg.get("stt_hotkey") or "").strip().lower() == "double_ctrl":
            hotkey_hint_lbl.configure(text=_pd("双击 Ctrl 并按住讲话，松开自动上屏",
                                                  "Double-tap Ctrl and hold to speak, release to commit"))
        else:
            hotkey_hint_lbl.configure(text=_pd("按住说话，松开自动上屏", "Hold to speak, release to commit"))

    _update_hotkey_btn()
    root._fewtype_update_hotkey = _update_hotkey_btn  # 供 _restart_hotkey 调用更新按钮文字

    # ---- 区域 B：模式选择器（Segmented Control 分段样式） ----
    stt_mode_var = tk.StringVar(value="verbatim")
    STT_MODE_DESC = {
        "verbatim": _pd("只加标点，保留所有口头废话", "Adds punctuation only, keeps all fillers"),
        "fluent": _pd("去除口头废话，句子更通顺", "Removes fillers, smoother sentences"),
        "formal": _pd("改写为正式、规范的文档", "Rewrites into formal documentation"),
        "custom": _pd("使用你自己的 Prompt 作为风格积木", "Uses your own prompt as the style"),
    }
    mode_segments: dict = {}

    def _update_mode_segments():
        for key, rb in mode_segments.items():
            sel = stt_mode_var.get() == key
            rb.configure(
                bg=ACCENT if sel else INPUT,
                selectcolor=ACCENT if sel else INPUT,  # 按钮式 Radio 选中背景由 selectcolor 决定
                fg="#090D0A" if sel else "#9CA3AF",
                activebackground=ACCENT if sel else BORDER,
                activeforeground="#090D0A" if sel else "#9CA3AF",
                font=(UI_FONT_FAMILY, 9, "bold") if sel else FONT_BODY,
            )

    def _on_stt_mode_change():
        m = stt_mode_var.get()
        stt_mode_desc.set(STT_MODE_DESC.get(m, ""))
        stt_custom_cb.configure(state="readonly" if m == "custom" else "disabled")
        _update_mode_segments()

    area_b = tk.Frame(main_body, bg=CARD)
    area_b.pack(fill=tk.X, pady=(10, 0))
    for key, zh, en in (
        ("verbatim", "忠实记录", "Verbatim"),
        ("fluent", "智能润色", "Polish"),
        ("formal", "严肃文档", "Formal"),
    ):
        rb = tk.Radiobutton(
            area_b, text=_pd(zh, en), variable=stt_mode_var, value=key,
            command=_on_stt_mode_change, indicatoron=False,
            bg=INPUT, fg="#9CA3AF", activebackground=BORDER, activeforeground="#9CA3AF",
            bd=0, relief="flat", highlightthickness=0, selectcolor=CARD,
            padx=14, pady=5, font=FONT_BODY, cursor="hand2",
        )
        rb.pack(side=tk.LEFT, padx=(0, 6))
        mode_segments[key] = rb
    stt_custom_radio = tk.Radiobutton(
        area_b, text=_pd("自定义风格", "Custom"), variable=stt_mode_var,
        value="custom", command=_on_stt_mode_change, indicatoron=False,
        bg=INPUT, fg="#9CA3AF", activebackground=BORDER, activeforeground="#9CA3AF",
        bd=0, relief="flat", highlightthickness=0, selectcolor=CARD,
        padx=14, pady=5, font=FONT_BODY, cursor="hand2",
    )
    stt_custom_radio.pack(side=tk.LEFT)
    mode_segments["custom"] = stt_custom_radio
    _update_mode_segments()

    # ---- 区域 C：风格下拉框 + 配置风格按钮 + 淡灰模式说明 ----
    area_c = tk.Frame(main_body, bg=CARD)
    area_c.pack(fill=tk.X, pady=(10, 0))
    stt_custom_var = tk.StringVar()
    stt_custom_cb = ttk.Combobox(area_c, textvariable=stt_custom_var, width=22, state="disabled")
    stt_custom_cb.pack(side=tk.LEFT)
    ttk.Button(
        area_c,
        text=_pd("配置风格…", "Configure styles…"),
        command=lambda: open_stt_styles_dialog(root, cfg, save_config, on_saved=_refresh_stt_styles),
    ).pack(side=tk.LEFT, padx=(6, 0))

    stt_mode_desc = tk.StringVar(value=STT_MODE_DESC["verbatim"])
    ttk.Label(main_body, textvariable=stt_mode_desc, foreground="#9CA3AF").pack(anchor="w", pady=(6, 0))

    def _refresh_stt_styles():
        names = [s.get("name", "") for s in cfg.get("stt_styles", [])]
        stt_custom_cb.configure(values=names)
        if stt_custom_var.get() not in names:
            stt_custom_var.set(names[0] if names else "")
        if names:
            stt_custom_radio.configure(state="normal")
        else:
            stt_custom_radio.configure(state="disabled")
            if stt_mode_var.get() == "custom":
                stt_mode_var.set("verbatim")
                _on_stt_mode_change()

    _refresh_stt_styles()

    # ================= 底部偏好卡片（自动上屏 + 自动翻译） =================
    pref_card = _mk_card(scene3)
    pref_body = _card_body(pref_card)
    stt_auto_commit = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        pref_body,
        text=_pd("自动上屏到光标所在文本框（Ctrl+V）", "Commit into the focused text box (Ctrl+V)"),
        variable=stt_auto_commit,
    ).pack(side=tk.LEFT)
    stt_translate_var = tk.BooleanVar(value=False)
    stt_target_var = tk.StringVar(value=_pd("简体中文", "English"))
    ttk.Checkbutton(pref_body, text=_pd("自动翻译", "Translate"), variable=stt_translate_var).pack(
        side=tk.LEFT, padx=(20, 0))
    stt_target_cb = ttk.Combobox(pref_body, textvariable=stt_target_var,
                                 values=list(TARGET_LOCALE.keys()), width=12, state="readonly")
    stt_target_cb.pack(side=tk.LEFT, padx=6)

    # ================= 底部服务控制状态栏（微缩） =================
    def _stt_open_cfg():
        open_provider_dialog(root, cfg, save_config, prov_status)

    def _stt_abort():
        """中止当前服务：终止当前转写/润色任务并恢复 UI。
        底层网络请求无法强杀，由各自的 timeout 兜底；取消后其结果会被丢弃。"""
        was_busy = _stt_busy[0]
        _stt_cancel.set()
        _stt_busy[0] = False
        _stt_anim_stop()
        _stt_wave_stop()
        try:
            _stt_recorder.stop_stream()
        except Exception:
            pass
        if _stt_stream.get("session") is not None:
            try:
                _stt_stream["session"].abort()
            except Exception:
                pass
            _stt_stream["session"] = None
        try:
            _stt_recorder.set_on_block(None)
        except Exception:
            pass
        if was_busy:
            stt_status.set(_pd("🟡 已终止当前任务，可重新开始", "🟡 Aborted, you can start again"))
            _stt_banner_show(_pd("已终止当前任务", "Current task aborted"), "#ffb74d")
            root.after(1800, _stt_banner_hide)
        else:
            stt_status.set(_pd("🟢 语音服务正常", "🟢 Voice service OK"))

    svc_row = tk.Frame(scene3, bg=BG)
    svc_row.pack(fill=tk.X, pady=(8, 0))
    stt_status = tk.StringVar(value=_pd("🟢 语音服务正常", "🟢 Voice service OK"))
    ttk.Label(svc_row, textvariable=stt_status, style="Muted.TLabel").pack(side=tk.LEFT)
    cfg_btn = ttk.Button(svc_row, text=_pd("⚙️ 高级配置", "⚙️ Setup"),
                         command=_stt_open_cfg, style="Paste.TButton")
    cfg_btn.pack(side=tk.RIGHT)
    abort_btn = ttk.Button(svc_row, text=_pd("🛑 重启服务", "🛑 Restart"),
                           command=_stt_abort, style="Paste.TButton")
    abort_btn.pack(side=tk.RIGHT, padx=(6, 0))
    _bind_tooltip(root, abort_btn, _pd(
        "如果出现状态条假死，可中止当前转写",
        "If the status bar appears frozen, abort the current transcription",
    ))


    root._fewtype_pick_scene = _pick_scene  # 测试钩子：回归验证直接调用（后台窗口 event_generate 不可靠）

    def _update_tabs():
        for k, (cell, lbl, line) in tabs.items():
            sel = scene_var.get() == k
            lbl.configure(fg=ACCENT2 if sel else TEXT)
            line.configure(bg=ACCENT if sel else BG)
            cell.configure(highlightbackground=ACCENT if sel else BORDER,
                           highlightcolor=ACCENT if sel else BORDER)

    for idx, (key, label) in enumerate(
        (
            ("scene3", _pd("🎙 语音输入", "Voice Input")),
            ("scene1", _pd("📖 电子书朗读", "E-book Reader")),
            ("scene2", _pd("📝 长文本转语音", "Text to Speech")),
        )
    ):
        # 等宽（grid weight=1 严格均分）+ 首尾顶格（首张左、末张右不留空隙）
        lpad = 0 if idx == 0 else 4
        rpad = 0 if idx == 2 else 4
        cell = tk.Frame(tab_holder, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER, cursor="hand2")
        cell.grid(row=0, column=idx, sticky="ew", padx=(lpad, rpad))
        lbl = tk.Label(cell, text=label, bg=CARD, fg=TEXT, font=FONT_SCENE,
                       cursor="hand2", padx=4, pady=10)
        lbl.pack()
        line = tk.Frame(cell, bg=BG, height=2)
        line.pack(fill=tk.X, side=tk.BOTTOM)
        for w in (cell, lbl, line):
            w.bind("<Button-1>", lambda e, k=key: _pick_scene(k))
        tabs[key] = (cell, lbl, line)

    scene1 = ttk.Frame(right)
    scene2 = ttk.Frame(right)
    scene3 = ttk.Frame(right)

    def show_scene():
        for f in (scene1, scene2, scene3):
            f.pack_forget()
        cur = {"scene1": scene1, "scene2": scene2, "scene3": scene3}[scene_var.get()]
        cur.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        if cur is scene2:
            # 场景2 内容较高：窗口下延，确保底部常驻条（sponsor/开机自启）与
            # 输出预览、生成音频、打开输出文件夹等按钮不被窗口下沿裁掉
            root.minsize(500, 520)
            if _log_open[0]:
                root.geometry("850x520")
            elif root.winfo_height() != 520:
                root.geometry("530x520")
            # 首次 pack 会高估请求高度撑大窗口，布局稳定后再强制一次几何
            root.after(120, lambda: root.geometry("850x520" if _log_open[0] else "530x520"))
            root.after(100, load_voices)
        else:
            # 其他场景恢复默认高度（统一 530x450，减少底部留白）
            root.minsize(500, 400)
            if _log_open[0]:
                root.geometry("850x400")
            elif root.winfo_height() != 450:
                root.geometry("530x400")
            # 布局稳定后再强制一次，确保窗口映射后高度生效
            root.after(150, lambda: root.geometry("850x400" if _log_open[0] else "530x400"))

    _update_tabs()

    # ---------- 场景 1：电子书朗读（静默服务介绍页，无需任何配置） ----------
    def _mk_card_s1(parent):
        c = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
        c.pack(fill=tk.X, pady=(10, 5))
        return c

    # 卡片一：实时服务桥接
    card_svc = _mk_card_s1(scene1)
    body_svc = tk.Frame(card_svc, bg=CARD)
    body_svc.pack(fill=tk.X, padx=15, pady=12)
    row_svc = tk.Frame(body_svc, bg=CARD)
    row_svc.pack(fill=tk.X)

    def _open_extension():
        ext_dir = Path(APP_DIR).parent / "extension"
        if ext_dir.exists():
            os.startfile(str(ext_dir))
        else:
            import webbrowser
            webbrowser.open("chrome://extensions")

    # Chrome 扩展状态：动态心跳检测（每3秒轮询 /extension_status）
    # 在线显示"已就绪"，离线显示"请加载扩展"并高亮按钮提示用户
    ext_status_var = tk.StringVar(value=_pd("⚪ 检测扩展状态…", "⚪ Checking extension…"))
    ext_status_lbl = tk.Label(row_svc, textvariable=ext_status_var,
                               bg=CARD, fg="#9CA3AF", font=(UI_FONT_FAMILY, 9, "bold"))
    ext_status_lbl.pack(side=tk.LEFT)
    ext_btn = tk.Button(row_svc, text=_pd("🧩 加载扩展", "🧩 Load extension"), command=_open_extension,
                        bg=ACCENT, fg=BG, activebackground=ACCENT2, activeforeground=BG,
                        relief="flat", bd=0, font=FONT_SMALL, cursor="hand2", padx=10, pady=3)
    ext_btn.pack(side=tk.RIGHT)

    # 扩展状态轮询：异步执行，不阻塞 Tkinter 主线程
    # 为什么用线程：urllib.request.urlopen 是阻塞调用，直接在主线程调用会卡住 UI
    # （鼠标动不了、窗口无响应），所以放到单独线程，完成后用 root.after 回主线程更新 UI。
    _ext_polling = False  # 防止上一个请求还没完成就发起下一个
    _ext_poll_count = 0  # 轮询次数计数器：前几次用更短间隔快速同步

    _ext_online_state = [None]  # 记录上一次的 online 状态，只有变化时才更新 UI，避免跳字

    def _update_ext_ui(data):
        """在主线程更新扩展状态 UI（只能在主线程操作 Tkinter 控件）。
        只有当 online 状态变化时才更新，避免每 5 秒重渲染导致"跳字"。
        """
        is_online = bool(data and data.get("online"))
        if _ext_online_state[0] == is_online:
            return  # 状态没变化，不更新 UI，避免跳字
        _ext_online_state[0] = is_online
        if is_online:
            ext_status_var.set(_pd("🟢 Chrome 扩展服务已就绪", "🟢 Chrome extension service ready"))
            ext_status_lbl.config(fg=ACCENT2)
            ext_btn.config(text=_pd("🧩 Chrome 扩展", "🧩 Chrome extension"),
                           bg=INPUT, fg=ACCENT2, activebackground=BORDER, activeforeground=ACCENT2)
        else:
            ext_status_var.set(_pd("⚪ 请加载 Chrome 扩展", "⚪ Please load Chrome extension"))
            ext_status_lbl.config(fg="#F59E0B")
            ext_btn.config(text=_pd("🧩 加载扩展", "🧩 Load extension"),
                           bg=ACCENT, fg=BG, activebackground=ACCENT2, activeforeground=BG)

    def _poll_extension_status():
        """每3秒轮询本地服务的 /extension_status，更新扩展在线状态。
        网络请求在单独线程执行，不阻塞主线程。
        """
        nonlocal _ext_polling
        if _ext_polling:
            # 上一个请求还没完成，跳过这次，3秒后再试
            root.after(3000, _poll_extension_status)
            return
        _ext_polling = True

        def _do_request():
            """子线程：执行网络请求，完成后回主线程更新 UI。"""
            nonlocal _ext_polling
            try:
                import urllib.request
                import json
                # 超时设为 1.5 秒：本地服务应该很快响应，太长会占用线程
                with urllib.request.urlopen("http://127.0.0.1:5005/extension_status", timeout=1.5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                root.after(0, lambda: _update_ext_ui(data))
            except Exception:
                # 本地服务未启动或请求失败，显示离线
                root.after(0, lambda: _update_ext_ui(None))
            finally:
                _ext_polling = False

        import threading
        threading.Thread(target=_do_request, daemon=True).start()
        # 动态间隔：前 5 次每 1 秒快速重试（确保 server.py 启动后尽快同步），
        # 之后每 3 秒稳定轮询。心跳超时 90 秒，扩展关闭/打开后 90 秒内能认出。
        nonlocal _ext_poll_count
        _ext_poll_count += 1
        interval = 1000 if _ext_poll_count < 5 else 5000
        root.after(interval, _poll_extension_status)

    # 启动时立即检测一次（不等待），前 5 次每 1 秒快速同步，之后每 3 秒轮询
    _poll_extension_status()
    # 支持平台列表：中文界面显示 WeRead，非中文界面不显示。
    # 为什么要做两套（语言条件判断）：
    #   1. WeRead（微信读书）是中国本土平台，仅中文用户在用，海外用户根本不知道它是什么；
    #   2. 非中文界面列出 WeRead 会让海外用户困惑（"这是什么平台？我能用吗？"）；
    #   3. 同理，火山引擎仅在英/简中/繁中界面显示（见 _platform_choices），因为它需要
    #      中国大陆身份证实名认证，其他语言用户无法注册；
    #   4. 这不是"歧视性隐藏"，而是"相关性过滤"——只给用户看他们实际能用的平台。
    tk.Label(body_svc, text=_pd("支持 微信读书 / Google Play 图书 / Koodo Reader",
                                "Supports Google Play Books / Koodo Reader"),
             bg=CARD, fg="#9CA3AF", font=FONT_SMALL).pack(anchor="w", pady=(8, 0))

    # 卡片二：使用流程
    card_how = _mk_card_s1(scene1)
    body_how = tk.Frame(card_how, bg=CARD)
    body_how.pack(fill=tk.X, padx=15, pady=12)
    tk.Label(body_how, text=_pd("⚡ 快速使用指南", "⚡ Quick start"),
             bg=CARD, fg=ACCENT2, font=(UI_FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(0, 10))
    for _line in [
        _pd("[1] 本程序保持运行（托盘后台驻留）", "[1] Keep this program running (tray resident)"),
        _pd("[2] 在网页端阅读器中 鼠标划选文本，即可自动朗读", "[2] Select text in the web reader to read aloud"),
        _pd("[3] 暂停 / 恢复：按 . 键（主键盘 / 小键盘均可）", "[3] Pause / Resume: press . key (main keyboard or numpad)"),
    ]:
        tk.Label(body_how, text=_line, bg=CARD, fg="#9CA3AF", font=FONT_BODY, justify="left").pack(anchor="w", pady=(3, 0))

    # ---------- 场景 2：长文本转语音 ----------
    ttk.Label(
        scene2,
        text=_pd("粘贴文本，输入与输出语言不一致时自动翻译，用任意音色生成音频", "Paste text, auto-translates when input and output languages differ, then generate audio"),
        style="Muted.TLabel",
        wraplength=420,
    ).pack(anchor="w", pady=(0, 4))

    text_wrap = ttk.Frame(scene2)
    text_wrap.pack(fill=tk.X, pady=(0, 4))   # 固定请求高：不把下方按钮推贴底
    text_input = _bind_text_menu(tk.Text(
        text_wrap, height=6, wrap=tk.WORD, bg=INPUT, fg=TEXT, insertbackground=TEXT,
        font=FONT_BODY, relief="flat", bd=0, highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=ACCENT, padx=8, pady=9,
    ))
    text_sb = tk.Scrollbar(text_wrap, width=6, bg=INPUT, troughcolor=BG,
                           activebackground=ACCENT, relief="flat", bd=0, highlightthickness=0)
    text_input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    text_sb.pack(side=tk.RIGHT, fill=tk.Y)
    text_sb.config(command=text_input.yview)
    text_input.config(yscrollcommand=text_sb.set)
    # 右下角全屏编辑按钮（对应 Tauri 版 Maximize2）
    _attach_fullscreen_button(text_wrap, text_input, _pd("输入文本 - 全屏编辑", "Input text - Full-screen edit"))

    # 控件区：Grid+Sticky 两列（标签列固定 / 控件列 weight=1 拉伸，右缘与大输入框对齐）
    form_frame = ttk.Frame(scene2)
    form_frame.pack(fill=tk.X, pady=(2, 0))
    form_frame.columnconfigure(0, minsize=64)  # 标签列
    form_frame.columnconfigure(1, weight=1)    # 控件列：自动拉伸填充剩余宽度

    # 行1：输出语言 + 语速 + 注释（注释紧随控件 pack，紧贴下拉框）
    ttk.Label(form_frame, text=_pd("输出语言", "Output language")).grid(row=0, column=0, sticky="w")
    row_lang = ttk.Frame(form_frame)
    row_lang.grid(row=0, column=1, sticky="ew")
    target_var = tk.StringVar(value=_default_output_lang())
    target_cb = ttk.Combobox(
        row_lang, textvariable=target_var, values=_output_lang_values(), width=12, state="readonly",
    )
    target_cb.pack(side=tk.LEFT)  # 固定尺寸，禁止拉伸
    target_cb.bind("<<ComboboxSelected>>", lambda e: refresh_voices_by_lang())
    rate_var = tk.StringVar(value="+0%")
    rate_cb = ttk.Combobox(
        row_lang, textvariable=rate_var, values=["-50%", "-25%", "-10%", "+0%", "+10%", "+25%", "+50%"], width=6,
    )
    rate_cb.pack(side=tk.LEFT, padx=(4, 0))
    tts_trans_note = ttk.Label(
        row_lang, text=_pd("输入与输出语言一致时自动跳过翻译", "Translation is skipped when input matches output language"),
        style="Muted.TLabel",
    )
    tts_trans_note.pack(side=tk.LEFT, padx=(10, 0))
    _auto_tooltip(root, tts_trans_note)

    # 行2：音色 + 注释（注释紧贴音色下拉框右侧 padx=10）
    ttk.Label(form_frame, text=_pd("音色", "Voice")).grid(row=1, column=0, sticky="w", pady=(4, 0))
    row_voice = ttk.Frame(form_frame)
    row_voice.grid(row=1, column=1, sticky="ew", pady=(4, 0))
    voice_var = tk.StringVar()
    # 固定宽度：右缘与「输出语言+语速」组合对齐，形成整齐矩形。
    # 长音色名会截断显示，可接受（多数人只看性别/国家前缀）。
    # 固定宽度：右缘与上方「输出语言+语速」组合右缘对齐（width=22 实测对齐）
    voice_cb = ttk.Combobox(row_voice, textvariable=voice_var, width=22)
    voice_cb.pack(side=tk.LEFT)
    tts_voice_note = ttk.Label(
        row_voice, text=_pd("音色为实时抓取，可能需要等待", "Voices are fetched live, may take a moment"),
        style="Muted.TLabel",
    )
    tts_voice_note.pack(side=tk.LEFT, padx=(10, 0))
    _auto_tooltip(root, tts_voice_note)

    preview_frame = ttk.Frame(scene2)
    preview_frame.pack(fill=tk.X, pady=(2, 0))   # 固定请求高：按钮行不贴底
    ttk.Label(preview_frame, text=_pd("输出预览（可编辑，用于生成音频）", "Output preview (editable, used for audio)"), style="Accent.TLabel", font=FONT_SMALL).pack(anchor="w")
    preview_wrap = ttk.Frame(preview_frame)
    preview_wrap.pack(fill=tk.X)
    preview_text = _bind_text_menu(tk.Text(
        preview_wrap, height=4, wrap=tk.WORD, bg=INPUT, fg=TEXT, insertbackground=TEXT,
        font=FONT_BODY, relief="flat", bd=0, highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=ACCENT, padx=8, pady=6,
    ))
    preview_sb = tk.Scrollbar(preview_wrap, width=6, bg=INPUT, troughcolor=BG,
                              activebackground=ACCENT, relief="flat", bd=0, highlightthickness=0)
    preview_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    preview_sb.pack(side=tk.RIGHT, fill=tk.Y)
    preview_sb.config(command=preview_text.yview)
    preview_text.config(yscrollcommand=preview_sb.set)
    # 右下角全屏编辑按钮（对应 Tauri 版 Maximize2）
    _attach_fullscreen_button(preview_wrap, preview_text, _pd("输出预览 - 全屏编辑", "Output preview - Full-screen edit"))

    btn_row = ttk.Frame(scene2)
    btn_row.pack(fill=tk.X, pady=6)
    # 左：预览文本（次要按钮）
    ttk.Button(btn_row, text=_pd("预览文本", "Preview text"), style="Muted.TButton",
               command=lambda: do_preview()).pack(side=tk.LEFT)
    # 中：试听 + 生成音频（主按钮最强视觉焦点）
    mid_grp = ttk.Frame(btn_row)
    mid_grp.pack(side=tk.LEFT, padx=8)
    ttk.Button(mid_grp, text=_pd("▶ 试听 3秒", "▶ Preview 3s"), style="Muted.TButton",
               command=lambda: preview_voice()).pack(side=tk.LEFT)
    gen_btn = ttk.Button(mid_grp, text=_pd("⚡ 生成音频", "⚡ Generate"), style="Primary.TButton",
               command=lambda: do_tts())
    gen_btn.pack(side=tk.LEFT, padx=(8, 0))
    # 右：保存路径区（紧凑组：指定 + 打开，完整路径显示在状态行）
    path_grp = ttk.Frame(btn_row)
    path_grp.pack(side=tk.RIGHT)
    ttk.Button(path_grp, text=_pd("📂 指定", "📂 Set"), style="Muted.TButton",
               command=lambda: pick_output_dir()).pack(side=tk.LEFT)
    ttk.Button(path_grp, text=_pd("↗ 打开", "↗ Open"), style="Muted.TButton",
               command=lambda: open_output()).pack(side=tk.LEFT, padx=(6, 0))
    scene2_status = tk.StringVar(value=_pd("🟢 就绪", "🟢 Ready"))
    ttk.Label(scene2, textvariable=scene2_status, style="Status.TLabel", wraplength=460).pack(anchor="w", pady=(6, 0))

    last_tts_path: list[str] = [""]

    TARGET_LOCALE = {
        # 母语名称（中文界面显示）
        "English": "en-US", "简体中文": "zh-CN", "繁體中文": "zh-TW",
        "日本語": "ja-JP", "한국어": "ko-KR", "Español": "es-ES",
        "Français": "fr-FR", "Deutsch": "de-DE",
        # 英文名称（英文界面显示）
        "Chinese (Simplified)": "zh-CN",
        "Chinese (Traditional)": "zh-TW",
        "Japanese": "ja-JP",
        "Korean": "ko-KR",
        "Spanish": "es-ES",
        "French": "fr-FR",
        "German": "de-DE",
    }

    _lang_voices: dict = {}  # locale -> [(shortName, gender), ...]
    _voice_display_to_real: dict = {}  # 显示名 -> 实际 shortName
    _voice_retry = [0]
    _tts_busy = [False]  # TTS 是否正在进行（防止 load_voices 覆盖状态 + 禁用生成按钮）

    _HANT_CHARS = set(
        "這個說時後來裡為與無沒這樣麼還讓過們對點嗎請開關體學書讀話語聽寫見車門風會愛國問題決發現認識覺應夠臺灣東興歡長"
    )

    def _is_cjk_char(ch: str) -> bool:
        """跟电子书 chunking.js 同样的 CJK 判断：中日韩文字符（含谚文/假名）。"""
        o = ord(ch)
        return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
                or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF)

    def _is_latin_letter(ch: str) -> bool:
        # 用 isalpha() 而非 isalpha()+isascii()，否则法语 é/è/à/ç/û、德语 ä/ö/ü 等带重音字母不被识别，会打断单词导致"字"数计算错误
        return ch.isalpha()

    def _is_ascii_digit(ch: str) -> bool:
        return ch.isdigit() and ch.isascii()

    def first_n_chars(text: str, n: int = 20) -> str:
        """跟电子书同样的"字"定义取前 n 个字：CJK每个算1个，拉丁字母连续串算1个，
        数字连续串算1个（小数点不算分隔符），标点/空白不算字但保留在输出里。"""
        count = 0
        i = 0
        in_latin = False
        in_digit = False
        while i < len(text) and count < n:
            ch = text[i]
            if _is_cjk_char(ch):
                count += 1
                in_latin = False
                in_digit = False
            elif _is_latin_letter(ch):
                if not in_latin:
                    count += 1
                    in_latin = True
                in_digit = False
            elif _is_ascii_digit(ch):
                if not in_digit:
                    count += 1
                    in_digit = True
                in_latin = False
            elif ch == ".":
                # 小数点不算分隔符（3.14 算一个数字串）
                pass
            else:
                # 标点/空白/其他符号：不算字，重置状态
                in_latin = False
                in_digit = False
            i += 1
        return text[:i].strip()

    def detect_input_lang(text: str) -> str:
        """启发式语言检测：'zh' / 'zh-hant' / 'ja' / 'ko' / 'other'（拉丁等）。"""
        if re.search(r"[\uAC00-\uD7A3]", text):
            return "ko"
        if re.search(r"[\u3040-\u30FF]", text):
            return "ja"
        han = re.findall(r"[\u4E00-\u9FFF]", text)
        if not han or len(han) / max(len(text), 1) < 0.2:
            return "other"
        hant = sum(1 for ch in han if ch in _HANT_CHARS)
        return "zh-hant" if hant / len(han) > 0.1 else "zh"

    def needs_translation() -> tuple:
        """返回 (是否需要翻译, 目标 locale)。拉丁语言之间不自动翻译（保守）。"""
        src = text_input.get("1.0", tk.END).strip()
        loc = TARGET_LOCALE.get(target_var.get(), "zh-CN")
        if not src:
            return False, loc
        in_lang = detect_input_lang(src)
        out_primary = loc.split("-", 1)[0].lower()
        if in_lang == "other":
            return out_primary not in ("en", "es", "fr", "de"), loc
        if in_lang.startswith("zh"):
            if out_primary != "zh":
                return True, loc
            in_hant = in_lang == "zh-hant"
            out_hant = loc in ("zh-TW", "zh-HK")
            return in_hant != out_hant, loc
        return in_lang != out_primary, loc

    def _format_voice_name(sn: str, gender: str) -> str:
        """去掉 Neural 后缀，加上性别标签。"""
        name = sn
        if name.endswith("Neural"):
            name = name[:-6]
        gender_label = ""
        if gender:
            g = gender.lower()
            if g.startswith("f"):
                gender_label = "♀ "
            elif g.startswith("m"):
                gender_label = "♂ "
        return gender_label + name

    def refresh_voices_by_lang():
        loc = TARGET_LOCALE.get(target_var.get(), "zh-CN")
        primary = loc.split("-", 1)[0].lower()
        # 中文：zh-CN 单独显示（简中音色够多），zh-TW+zh-HK 合并（繁中+粤语）
        # 其他语言：合并同语言所有变体（如西班牙语 es-ES/es-MX/es-AR 全部合并）
        pairs = []
        if primary == "zh":
            if loc == "zh-CN":
                pairs = list(_lang_voices.get("zh-CN", []))
            else:
                pairs = list(_lang_voices.get("zh-TW", [])) + list(_lang_voices.get("zh-HK", []))
        else:
            for k, v in _lang_voices.items():
                if k.split("-", 1)[0].lower() == primary:
                    pairs.extend(v)
        # 去重（按 shortName）
        seen = set()
        unique_pairs = []
        for sn, g in pairs:
            if sn not in seen:
                seen.add(sn)
                unique_pairs.append((sn, g))
        # 英语音色按区域优先级排序：US > GB > 其他
        if primary == "en":
            def _region_priority(item):
                sn = item[0]
                parts = sn.split("-")
                if len(parts) >= 2:
                    region = parts[1].upper()
                    if region == "US":
                        return 0
                    if region == "GB":
                        return 1
                return 2
            unique_pairs.sort(key=_region_priority)
        # 构建显示名→实际名映射
        _voice_display_to_real.clear()
        display_names = []
        for sn, g in unique_pairs:
            disp = _format_voice_name(sn, g)
            _voice_display_to_real[disp] = sn
            display_names.append(disp)
        if display_names:
            voice_cb.configure(values=display_names)
            current_real = _voice_display_to_real.get(voice_var.get(), voice_var.get())
            if current_real in [sn for sn, _ in unique_pairs]:
                for disp, real in _voice_display_to_real.items():
                    if real == current_real:
                        voice_var.set(disp)
                        break
            else:
                voice_var.set(display_names[0])
        else:
            voice_cb.configure(values=[])
            voice_var.set("")

    def load_voices():
        """后台线程拉取音色（主线程只负责调度，避免 /voices 抓取慢时卡死 UI）。"""
        if _voice_retry[0] > 10:
            return
        _voice_retry[0] += 1

        def fetch():
            try:
                import urllib.request
                with urllib.request.urlopen("http://%s:%d/voices" % (HOST, PORT), timeout=25) as r:
                    data = json.loads(r.read().decode("utf-8"))
                grouped = {}
                for v in data.get("voices", []):
                    sn = v.get("shortName") or v.get("short_name")
                    if not sn:
                        continue
                    loc = v.get("locale") or sn.split("-", 1)[0]
                    gender = v.get("gender") or ""
                    grouped.setdefault(loc, []).append((sn, gender))
                if not grouped:
                    raise RuntimeError("no voices")
            except Exception:
                root.after(2000, load_voices)
                return

            def apply():
                _voice_retry[0] = 0
                _lang_voices.clear()
                _lang_voices.update(grouped)
                refresh_voices_by_lang()
                ui_log(_pd("已加载 %d 个音色 / %d 种语言", "Loaded %d voices / %d languages") % (sum(len(x) for x in grouped.values()), len(grouped)))
                # 只有 TTS 不在进行中时才更新状态，避免覆盖"正在合成…"
                if not _tts_busy[0]:
                    scene2_status.set(_pd("🟢 就绪 | 已加载 %d 个音色", "🟢 Ready | %d voices loaded") % sum(len(x) for x in grouped.values()))

            root.after(0, apply)

        threading.Thread(target=fetch, daemon=True).start()

    def _provider_from_cfg():
        from provider import Provider
        pc = cfg.get("provider", {}) or {}
        return Provider(
            pc.get("platform", "groq"),
            pc.get("api_key", ""),
            pc.get("asr_model", ""),
            pc.get("llm_model", ""),
            pc.get("base_url", ""),
            pc.get("llm_key", ""),
            pc.get("llm_base_url", ""),
        )

    def _ensure_provider() -> bool:
        if (cfg.get("provider", {}) or {}).get("api_key"):
            return True
        open_provider_dialog(root, cfg, save_config, prov_status)
        return bool((cfg.get("provider", {}) or {}).get("api_key"))

    def _translate_async(src: str, on_done, on_err):
        target = target_var.get()  # 主线程预读：Tk 变量在工作线程访问可能使 Tcl 崩溃
        def work():
            try:
                from provider import build_messages
                sys_p = (
                    "You are a professional translator. Translate the user's text into %s "
                    "in natural, native, idiomatic style. Preserve meaning, tone and structure. "
                    "Output ONLY the translated text, no explanations, no quotes." % target
                )
                out = _provider_from_cfg().chat(build_messages(sys_p, src))
                root.after(0, lambda out=out: on_done(out))
            except Exception as e:
                root.after(0, lambda e=e: on_err(e))

        threading.Thread(target=work, daemon=True).start()

    def do_preview():
        try:
            ui_log("[preview] start")
            src = text_input.get("1.0", tk.END).strip()
            ui_log(f"[preview] src len={len(src)}")
            if not src:
                _show_fewtype_dialog(root, _pd("提示", "Notice"), _pd("请先输入或粘贴文本", "Enter or paste text first"))
                return
            need, _loc = needs_translation()
            ui_log(f"[preview] need_translate={need}, target={_loc}")
            if not need:
                preview_text.delete("1.0", tk.END)
                preview_text.insert("1.0", src)
                scene2_status.set(_pd("输入与输出语言一致，无需翻译，可直接生成", "Input matches output language - generate directly"))
                ui_log("[preview] done (no translate)")
                return
            if not _ensure_provider():
                ui_log("[preview] provider not ready, aborted")
                return
            scene2_status.set(_pd("翻译中…", "Translating…"))
            root.update_idletasks()
            ui_log("[preview] calling _translate_async")
            _translate_async(src, _show_translation, _show_translation_error)
        except Exception as e:
            ui_log(f"[preview] ERROR: {type(e).__name__}: {e}")
            import traceback
            ui_log(traceback.format_exc())

    def _show_translation(out: str):
        preview_text.delete("1.0", tk.END)
        preview_text.insert("1.0", out)
        scene2_status.set(_pd("翻译完成，可编辑后生成音频", "Translated. Edit if needed, then generate"))

    def _show_translation_error(e: Exception):
        scene2_status.set(_pd("翻译失败", "Translation failed") + ": %s" % e)
        ui_log(_pd("翻译失败", "Translation failed") + ": %s" % e)

    def _tts_with_text(text: str):
        print("[tts] _tts_with_text 被调用, _tts_busy=", _tts_busy[0])  # 调试日志
        if not health_ok():
            print("[tts] 服务不健康，return")  # 调试日志
            messagebox.showwarning(_pd("提示", "Notice"), _pd("请先启动服务", "Start the service first"), parent=root)
            return
        if _tts_busy[0]:
            print("[tts] 已在生成中，忽略重复点击")  # 调试日志
            return  # 已经在生成中，忽略重复点击
        _tts_busy[0] = True  # 标记 TTS 进行中，防止 load_voices 覆盖状态
        # TTS 超时保护：3 分钟后如果还没完成，自动恢复按钮，避免 _tts_busy 永远卡住
        root.after(180000, lambda: _tts_timeout())
        gen_btn.config(state="disabled")  # 禁用生成按钮，防止重复提交
        scene2_status.set(_pd("正在合成（长文本自动分段拼接）…", "Synthesizing (chunked for long text)…"))
        root.update_idletasks()
        voice = _voice_display_to_real.get(voice_var.get(), voice_var.get()) or "zh-CN-XiaoxiaoNeural"
        rate = rate_var.get()

        # 共享变量：子线程存结果，主线程轮询（不依赖子线程 root.after，更可靠）
        _result_holder = [None]
        _start_time = [time.time()]

        def work():
            """子线程：只做网络请求，把结果存到共享变量。
            用全局 _tts_session 复用 HTTP 长连接，减少连接建立开销。
            """
            try:
                _out_dir = cfg.get("tts_output_dir") or ""
                payload = json.dumps({"text": text, "voice": voice, "rate": rate, "output_dir": _out_dir})
                resp = _tts_session.post(
                    "http://%s:%d/tts_file" % (HOST, PORT),
                    data=payload,
                    timeout=120
                )
                data = resp.json()
                _result_holder[0] = ("ok", data)
            except Exception as e:
                _result_holder[0] = ("err", e)

        threading.Thread(target=work, daemon=True).start()

        def _poll_result():
            """主线程轮询：检查子线程是否完成，完成则调用 _tts_done/_tts_fail。"""
            if _result_holder[0] is not None:
                kind, val = _result_holder[0]
                if kind == "ok":
                    _tts_done(val)
                else:
                    _tts_fail(val)
                return
            if time.time() - _start_time[0] > 120:
                _tts_fail(TimeoutError("TTS 超时（120秒）"))
                return
            root.after(200, _poll_result)

        root.after(200, _poll_result)

    def preview_voice():
        """试听：输入输出语言一致→用输入文本前20字；不一致→输出预览为空则提示先点输出预览，否则用输出预览前20字。"""
        src = text_input.get("1.0", tk.END).strip()
        if not src:
            _show_fewtype_dialog(root, _pd("提示", "Notice"), _pd("请先输入或粘贴文本", "Enter or paste text first"))
            return
        voice = _voice_display_to_real.get(voice_var.get(), voice_var.get())
        if not voice:
            messagebox.showwarning(_pd("提示", "Notice"), _pd("请先选择音色", "Please select a voice first"), parent=root)
            return
        # 判断输入输出语言是否一致
        need_translate, _ = needs_translation()
        if need_translate:
            # 不一致：检查输出预览框是否已有翻译内容
            preview_content = preview_text.get("1.0", tk.END).strip()
            if not preview_content:
                _show_fewtype_dialog(
                    root,
                    _pd("提示", "Notice"),
                    _pd("输入与输出语言不一致，请先点「输出预览」完成翻译，再试听",
                        "Input and output languages differ. Please click 'Preview output' to translate first, then preview.")
                )
                return
            preview_src = preview_content
        else:
            # 一致：直接用输入文本
            preview_src = src
        preview_text_content = first_n_chars(preview_src, 20)
        if not preview_text_content:
            return
        scene2_status.set(_pd("试听合成中…", "Previewing…") + ": " + preview_text_content)
        root.update_idletasks()

        def work():
            try:
                import tempfile, os
                import urllib.request
                payload = json.dumps({"text": preview_text_content, "voice": voice, "rate": rate_var.get()}).encode("utf-8")
                req = urllib.request.Request(
                    "http://%s:%d/tts_file" % (HOST, PORT),
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode("utf-8"))
                path = data.get("path")
                if not path or not Path(path).exists():
                    raise RuntimeError("audio file not found")
                # 播放：用 Windows 自带的 MCI 接口（winmm.dll），无需额外库，不弹外部窗口
                try:
                    import ctypes
                    alias = "fewtype_preview"
                    # 先关闭可能存在的旧播放
                    ctypes.windll.winmm.mciSendStringW(f"close {alias}", None, 0, None)
                    # 打开文件
                    ctypes.windll.winmm.mciSendStringW(f'open "{path}" alias {alias}', None, 0, None)
                    # 播放
                    ctypes.windll.winmm.mciSendStringW(f"play {alias}", None, 0, None)
                except Exception as play_err:
                    # MCI 失败时 fallback 到系统默认播放器
                    os.startfile(path)
                root.after(0, lambda: scene2_status.set(_pd("试听完成", "Preview done") + ": " + preview_text_content))
            except Exception as e:
                root.after(0, lambda: scene2_status.set(_pd("试听失败", "Preview failed") + ": %s" % e))
                ui_log("试听失败: %s" % e)

        threading.Thread(target=work, daemon=True).start()

    def do_tts():
        text = preview_text.get("1.0", tk.END).strip()
        if not text:
            text = text_input.get("1.0", tk.END).strip()
        if not text:
            _show_fewtype_dialog(root, _pd("提示", "Notice"), _pd("请先输入或粘贴文本", "Enter or paste text first"))
            return
        need, _loc = needs_translation()
        if need:
            # 输入输出语言不一致时，不自动翻译，提示用户先预览翻译结果再合成
            _show_fewtype_dialog(
                root,
                _pd("提示", "Notice"),
                _pd("输入与输出语言不一致，请先点「输出预览」完成翻译，再合成",
                    "Input and output languages differ. Please click 'Preview output' to translate first, then generate.")
            )
            return
        _tts_with_text(text)

    def _tts_timeout():
        """TTS 超时保护：如果超过 3 分钟还没完成，自动恢复按钮。"""
        if _tts_busy[0]:
            _tts_busy[0] = False
            gen_btn.configure(state="normal")
            scene2_status.set(_pd("生成超时，请重试", "Generation timed out, please retry"))

    def _tts_done(data: dict):
        print("[tts] _tts_done 被调用, data=", data)  # 调试日志
        print("[tts] 恢复前 gen_btn state=", gen_btn.cget("state"))  # 调试日志
        try:
            last_tts_path[0] = data.get("path", "")
            fname = data.get("filename") or "unknown"
            segs = data.get("segments") or 0
            nbytes = data.get("bytes") or 0
            scene2_status.set(
                _pd("已生成", "Done") + ": %s（%d 段，%d KB）" % (fname, segs, nbytes // 1024)
            )
            ui_log("TTS 文件: %s" % data.get("path"))
        except Exception as e:
            print("[tts] _tts_done 更新状态时出错:", e)  # 调试日志
        finally:
            # 确保按钮一定恢复，即使上面的状态更新出错
            _tts_busy[0] = False
            gen_btn.configure(state="normal")
            print("[tts] 恢复后 gen_btn state=", gen_btn.cget("state"))  # 调试日志

    def _tts_fail(e: Exception):
        print("[tts] _tts_fail 被调用, e=", e)  # 调试日志
        try:
            msg = str(e)
            if "404" in msg:
                msg += _pd("（服务版本过旧，请退出旧版 FewType-bridge 后重试）", " (old bridge version, exit the old FewType-bridge and retry)")
            scene2_status.set(_pd("生成失败", "Failed") + ": %s" % msg)
            ui_log("TTS 生成失败: %s" % e)
        except Exception as e2:
            print("[tts] _tts_fail 更新状态时出错:", e2)  # 调试日志
        finally:
            # 确保按钮一定恢复
            _tts_busy[0] = False
            gen_btn.configure(state="normal")
            print("[tts] 失败恢复后 gen_btn state=", gen_btn.cget("state"))  # 调试日志

    def open_output():
        # 打开优先级：最近生成文件目录 > 用户指定目录 > 默认 tts_output
        d = None
        if last_tts_path[0]:
            d = Path(last_tts_path[0]).parent
        if d is None and cfg.get("tts_output_dir"):
            d = Path(cfg["tts_output_dir"])
        if d is None:
            d = Path(APP_DIR) / "tts_output"
        if d.exists():
            os.startfile(str(d))
        else:
            ui_log(_pd("输出目录还不存在", "Output dir does not exist yet"))

    def pick_output_dir():
        from tkinter import filedialog
        init = str(cfg.get("tts_output_dir") or APP_DIR)
        d = filedialog.askdirectory(
            parent=root, initialdir=init,
            title=_pd("指定输出文件夹", "Choose output folder"),
        )
        if d:
            cfg["tts_output_dir"] = d
            save_config(cfg)
            scene2_status.set(_pd("输出文件夹已指定", "Output folder set") + ": %s" % d)
            ui_log(_pd("输出文件夹", "Output folder") + ": %s" % d)

    # ---------- 场景 3：语音输入（P2） ----------
    def _mk_card(parent):
        c = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
        c.pack(fill=tk.X, pady=(10, 5))
        return c

    def _card_body(card):
        b = tk.Frame(card, bg=CARD)
        b.pack(fill=tk.X, padx=15, pady=10)
        return b

    # ================= 顶部主卡片 =================
    main_card = _mk_card(scene3)
    main_body = _card_body(main_card)

    # ---- 区域 A：快捷键与触发逻辑 ----
    area_a = tk.Frame(main_body, bg=CARD)
    area_a.pack(fill=tk.X)
    hot_btn = ttk.Button(
        area_a,
        command=lambda: open_stt_hotkey_dialog(root, cfg, save_config, on_saved=_restart_hotkey),
    )
    hot_btn.pack(side=tk.LEFT)
    _bind_tooltip(root, hot_btn, _pd("自定义快捷键", "Customize hotkey"))
    hotkey_hint_lbl = ttk.Label(
        area_a, text=_pd("按住说话，松开自动上屏", "Hold to speak, release to commit"),
        foreground="#9CA3AF",
    )
    hotkey_hint_lbl.pack(side=tk.LEFT, padx=(10, 0))
    _auto_tooltip(root, hotkey_hint_lbl)

    def _update_hotkey_btn():
        hot_btn.configure(text=_hotkey_display(cfg.get("stt_hotkey", "ctrl+win")))
        # 说明文字根据热键模式更新
        if (cfg.get("stt_hotkey") or "").strip().lower() == "double_ctrl":
            hotkey_hint_lbl.configure(text=_pd("双击 Ctrl 并按住讲话，松开自动上屏",
                                                  "Double-tap Ctrl and hold to speak, release to commit"))
        else:
            hotkey_hint_lbl.configure(text=_pd("按住说话，松开自动上屏", "Hold to speak, release to commit"))

    _update_hotkey_btn()
    root._fewtype_update_hotkey = _update_hotkey_btn  # 供 _restart_hotkey 调用更新按钮文字

    # ---- 区域 B：模式选择器（Segmented Control 分段样式） ----
    stt_mode_var = tk.StringVar(value="verbatim")
    STT_MODE_DESC = {
        "verbatim": _pd("只加标点，保留所有口头废话", "Adds punctuation only, keeps all fillers"),
        "fluent": _pd("去除口头废话，句子更通顺", "Removes fillers, smoother sentences"),
        "formal": _pd("改写为正式、规范的文档", "Rewrites into formal documentation"),
        "custom": _pd("使用你自己的 Prompt 作为风格积木", "Uses your own prompt as the style"),
    }
    mode_segments: dict = {}

    def _update_mode_segments():
        for key, rb in mode_segments.items():
            sel = stt_mode_var.get() == key
            rb.configure(
                bg=ACCENT if sel else INPUT,
                selectcolor=ACCENT if sel else INPUT,  # 按钮式 Radio 选中背景由 selectcolor 决定
                fg="#090D0A" if sel else "#9CA3AF",
                activebackground=ACCENT if sel else BORDER,
                activeforeground="#090D0A" if sel else "#9CA3AF",
                font=(UI_FONT_FAMILY, 9, "bold") if sel else FONT_BODY,
            )

    def _on_stt_mode_change():
        m = stt_mode_var.get()
        stt_mode_desc.set(STT_MODE_DESC.get(m, ""))
        stt_custom_cb.configure(state="readonly" if m == "custom" else "disabled")
        _update_mode_segments()

    area_b = tk.Frame(main_body, bg=CARD)
    area_b.pack(fill=tk.X, pady=(10, 0))
    for key, zh, en in (
        ("verbatim", "忠实记录", "Verbatim"),
        ("fluent", "智能润色", "Polish"),
        ("formal", "严肃文档", "Formal"),
    ):
        rb = tk.Radiobutton(
            area_b, text=_pd(zh, en), variable=stt_mode_var, value=key,
            command=_on_stt_mode_change, indicatoron=False,
            bg=INPUT, fg="#9CA3AF", activebackground=BORDER, activeforeground="#9CA3AF",
            bd=0, relief="flat", highlightthickness=0, selectcolor=CARD,
            padx=14, pady=5, font=FONT_BODY, cursor="hand2",
        )
        rb.pack(side=tk.LEFT, padx=(0, 6))
        mode_segments[key] = rb
    stt_custom_radio = tk.Radiobutton(
        area_b, text=_pd("自定义风格", "Custom"), variable=stt_mode_var,
        value="custom", command=_on_stt_mode_change, indicatoron=False,
        bg=INPUT, fg="#9CA3AF", activebackground=BORDER, activeforeground="#9CA3AF",
        bd=0, relief="flat", highlightthickness=0, selectcolor=CARD,
        padx=14, pady=5, font=FONT_BODY, cursor="hand2",
    )
    stt_custom_radio.pack(side=tk.LEFT)
    mode_segments["custom"] = stt_custom_radio
    _update_mode_segments()

    # ---- 区域 C：风格下拉框 + 配置风格按钮 + 淡灰模式说明 ----
    area_c = tk.Frame(main_body, bg=CARD)
    area_c.pack(fill=tk.X, pady=(10, 0))
    stt_custom_var = tk.StringVar()
    stt_custom_cb = ttk.Combobox(area_c, textvariable=stt_custom_var, width=22, state="disabled")
    stt_custom_cb.pack(side=tk.LEFT)
    ttk.Button(
        area_c,
        text=_pd("配置风格…", "Configure styles…"),
        command=lambda: open_stt_styles_dialog(root, cfg, save_config, on_saved=_refresh_stt_styles),
    ).pack(side=tk.LEFT, padx=(6, 0))

    stt_mode_desc = tk.StringVar(value=STT_MODE_DESC["verbatim"])
    ttk.Label(main_body, textvariable=stt_mode_desc, foreground="#9CA3AF").pack(anchor="w", pady=(6, 0))

    def _refresh_stt_styles():
        names = [s.get("name", "") for s in cfg.get("stt_styles", [])]
        stt_custom_cb.configure(values=names)
        if stt_custom_var.get() not in names:
            stt_custom_var.set(names[0] if names else "")
        if names:
            stt_custom_radio.configure(state="normal")
        else:
            stt_custom_radio.configure(state="disabled")
            if stt_mode_var.get() == "custom":
                stt_mode_var.set("verbatim")
                _on_stt_mode_change()

    _refresh_stt_styles()

    # ================= 底部偏好卡片（自动上屏 + 自动翻译） =================
    pref_card = _mk_card(scene3)
    pref_body = _card_body(pref_card)
    stt_auto_commit = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        pref_body,
        text=_pd("自动上屏到光标所在文本框（Ctrl+V）", "Commit into the focused text box (Ctrl+V)"),
        variable=stt_auto_commit,
    ).pack(side=tk.LEFT)
    stt_translate_var = tk.BooleanVar(value=False)
    stt_target_var = tk.StringVar(value=_pd("简体中文", "English"))
    ttk.Checkbutton(pref_body, text=_pd("自动翻译", "Translate"), variable=stt_translate_var).pack(
        side=tk.LEFT, padx=(20, 0))
    stt_target_cb = ttk.Combobox(pref_body, textvariable=stt_target_var,
                                 values=list(TARGET_LOCALE.keys()), width=12, state="readonly")
    stt_target_cb.pack(side=tk.LEFT, padx=6)

    # ================= 底部服务控制状态栏（微缩） =================
    def _stt_open_cfg():
        open_provider_dialog(root, cfg, save_config, prov_status)

    def _stt_abort():
        """中止当前服务：终止当前转写/润色任务并恢复 UI。
        底层网络请求无法强杀，由各自的 timeout 兜底；取消后其结果会被丢弃。"""
        was_busy = _stt_busy[0]
        _stt_cancel.set()
        _stt_busy[0] = False
        _stt_anim_stop()
        _stt_wave_stop()
        try:
            _stt_recorder.stop_stream()
        except Exception:
            pass
        if _stt_stream.get("session") is not None:
            try:
                _stt_stream["session"].abort()
            except Exception:
                pass
            _stt_stream["session"] = None
        try:
            _stt_recorder.set_on_block(None)
        except Exception:
            pass
        if was_busy:
            stt_status.set(_pd("🟡 已终止当前任务，可重新开始", "🟡 Aborted, you can start again"))
            _stt_banner_show(_pd("已终止当前任务", "Current task aborted"), "#ffb74d")
            root.after(1800, _stt_banner_hide)
        else:
            stt_status.set(_pd("🟢 语音服务正常", "🟢 Voice service OK"))

    svc_row = tk.Frame(scene3, bg=BG)
    svc_row.pack(fill=tk.X, pady=(8, 0))
    stt_status = tk.StringVar(value=_pd("🟢 语音服务正常", "🟢 Voice service OK"))
    ttk.Label(svc_row, textvariable=stt_status, style="Muted.TLabel").pack(side=tk.LEFT)
    cfg_btn = ttk.Button(svc_row, text=_pd("⚙️ 高级配置", "⚙️ Setup"),
                         command=_stt_open_cfg, style="Paste.TButton")
    cfg_btn.pack(side=tk.RIGHT)
    abort_btn = ttk.Button(svc_row, text=_pd("🛑 重启服务", "🛑 Restart"),
                           command=_stt_abort, style="Paste.TButton")
    abort_btn.pack(side=tk.RIGHT, padx=(6, 0))
    _bind_tooltip(root, abort_btn, _pd(
        "如果出现状态条假死，可中止当前转写",
        "If the status bar appears frozen, abort the current transcription",
    ))

    show_scene()

    # ---------- 场景 3：全局热键 + 录音/转写/上屏流程 ----------
    _stt_busy = [False]
    _stt_target = {"hwnd": 0}  # 说话前的前台窗口，松开后无感上屏目标
    _tray_mode = [False]  # 主窗口是否在托盘隐藏状态（withdraw），用于强制保持隐藏

    def _force_root_withdrawn():
        """强制主窗口保持隐藏（只要当前是 withdrawn 状态），防止任何 Tk 操作把 root 从托盘拉出来。
        不依赖 _tray_mode 标志（可能在 show/hide 切换时不同步），直接检查 root.state()。"""
        try:
            if root.state() == "withdrawn":
                root.withdraw()
        except Exception:
            pass
    _stt_cancel = threading.Event()  # "中止当前服务"按钮：置位后当前转写/润色任务中止
    _stt_recorder = stt_engine.Recorder()
    _stt_banner = [None]
    _stt_banner_label = [None]
    _stt_banner_canvas = [None]
    _stt_fade = {"after": None, "ready": False}  # ready=True=透明度已到位，内容更新不打断
    _stt_wave = {"active": False, "after": None, "smooth": 0.0}

    def _hud_top_hwnd(w):
        """返回 Tk 顶层窗口真正的 HWND（winfo_id 是子窗口，LWA/样式必须作用在顶层）。"""
        import ctypes
        _raw = int(w.winfo_id())
        _p = ctypes.windll.user32.GetParent(_raw)
        return _p if _p else _raw

    def _hud_set_alpha(w, alpha):
        """LWA_COLORKEY|LWA_ALPHA 一次调用：透明键（圆角）+ 整体透明度共存。
        参数 w 可为 widget（取 winfo_id）或直接传 hwnd int（淡入淡出调用处传 int）。"""
        try:
            import ctypes
            _raw = w if isinstance(w, int) else int(w.winfo_id())
            _p = ctypes.windll.user32.GetParent(_raw)
            _hwnd = _p if _p else _raw
            ctypes.windll.user32.SetLayeredWindowAttributes(
                _hwnd, HUD_KEY_COLORREF, ctypes.c_ubyte(int(alpha)),
                0x00000001 | 0x00000002)
        except Exception:
            pass

    def _hud_round_rect(cv, x1, y1, x2, y2, r, fill, outline, width=1):
        """Canvas 平滑圆角矩形（透明键 + LWA 方案：圆角外透出桌面）。"""
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, fill=fill, outline=outline, width=width)

    def _hud_draw():
        """Pill 胶囊 + 荧光边框 + 10px 发光圆点 + 文本居中 + 录音波形。"""
        try:
            cv = _stt_banner_canvas[0]
            lab = _stt_banner_label[0]
            if cv is None or lab is None:
                return
            cv.delete("all")
            lab.update_idletasks()
            tw, th = lab.winfo_reqwidth(), lab.winfo_reqheight()
            dot_d = 10
            pad_x, pad_y = 18, 12
            gap = 12
            wave_w = 132 if _stt_wave["active"] else 0
            W = pad_x + dot_d + gap + tw + (gap + wave_w if wave_w else 0) + pad_x
            H = max(th + 2 * pad_y, dot_d + 2 * pad_y)
            cv.configure(width=W, height=H)
            _hud_round_rect(cv, 1, 1, W - 2, H - 2, 12, HUD_BG, ACCENT, width=1)
            cx, cy = pad_x + dot_d // 2, H // 2
            # 10px 发光圆点：外圈薄荷荧光 + 内芯翡翠绿
            cv.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, outline=ACCENT2, width=2)
            cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=ACCENT, outline="")
            cv.create_window(pad_x + dot_d + gap, max(1, (H - th) // 2), window=lab, anchor="nw")
            if wave_w:
                x0 = pad_x + dot_d + gap + tw + gap
                bh = H - 2 * pad_y
                bars = (0.30, 0.55, 0.85, 0.55, 0.30)
                bw = 14
                gap2 = max(4, (wave_w - len(bars) * bw) // (len(bars) + 1))
                hgt = _stt_wave["smooth"]
                for i, wgt in enumerate(bars):
                    hb = max(6, int((0.25 + 0.75 * wgt * hgt) * bh))
                    x = x0 + gap2 + i * (bw + gap2)
                    y = (H - hb) // 2
                    cv.create_rectangle(x, y, x + bw, y + hb, fill=ACCENT2, width=0)
        except Exception:
            pass

    def _stt_banner_show(text: str, color: str = "#e8eaed"):
        """Moss Black 悬浮状态条：Pill 胶囊 + 荧光边框 + 发光圆点 + 波形。
        圆角用透明色键；淡入淡出用 SetLayeredWindowAttributes(LWA_COLORKEY|LWA_ALPHA)。"""
        try:
            if _stt_banner[0] is None:
                # 关键：Toplevel(None) 无父窗口，切断与 root 的 master 绑定
                # 避免 Toplevel 显示/更新时通过 WM_TRANSIENT_FOR 消息链激活已 withdraw 的 root
                b = tk.Toplevel(None)
                b.overrideredirect(True)
                b.attributes("-topmost", True)
                # -disabled: 窗口不接收任何键盘/鼠标输入，永不主动抢焦点（纯信息展示窗口）
                try:
                    b.attributes("-disabled", True)
                except Exception:
                    pass
                # 切断 transient 关系，明确告诉窗口管理器它没有所有者
                try:
                    b.wm_transient("")
                except Exception:
                    pass
                # WS_EX_TOOLWINDOW(不显示任务栏按钮) + WS_EX_NOACTIVATE(不激活窗口，不抢焦点)
                # owner 设为桌面(NULL)，避免显示状态条时间接激活主窗口(root)抢占焦点
                try:
                    import ctypes
                    # HWND 兼容：先 GetParent，返回 0 就用 winfo_id()（Toplevel(None)+overrideredirect 时 GetParent 可能返回 0）
                    _hwnd_raw = int(b.winfo_id())
                    _hwnd_parent = ctypes.windll.user32.GetParent(_hwnd_raw)
                    hwnd = _hwnd_parent if _hwnd_parent else _hwnd_raw
                    GWL_EXSTYLE = -20
                    GWLP_HWNDPARENT = -8
                    SWP_FRAMECHANGED = 0x0020
                    SWP_NOMOVE = 0x0002
                    SWP_NOSIZE = 0x0001
                    SWP_NOZORDER = 0x0004
                    WS_EX_TOOLWINDOW = 0x00000080
                    WS_EX_NOACTIVATE = 0x08000000
                    ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
                    # owner 设为桌面(0)，状态条与 root 解耦
                    try:
                        ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, 0)
                    except Exception:
                        ctypes.windll.user32.SetWindowLongW(hwnd, GWLP_HWNDPARENT, 0)
                    # SWP_FRAMECHANGED 强制样式生效（关键！之前漏掉了，导致 WS_EX_TOOLWINDOW 没生效，状态条出现在任务栏）
                    ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
                except Exception:
                    pass
                # 圆角：透明色键（四角露出桌面）
                try:
                    b.attributes("-transparentcolor", HUD_KEY)
                except Exception:
                    pass
                _stt_banner[0] = b
                _force_root_withdrawn()  # 状态条创建后立即强制 root 保持隐藏
                cv = tk.Canvas(b, bg=HUD_KEY, highlightthickness=0)
                cv.pack()
                _stt_banner_canvas[0] = cv
                lab = tk.Label(cv, text="", bg=HUD_BG, fg=color, font=FONT_BODY,
                               wraplength=440, justify="left", bd=0, highlightthickness=0)
                _stt_banner_label[0] = lab
            if _stt_fade["after"] is not None:
                try:
                    root.after_cancel(_stt_fade["after"])
                except Exception:
                    pass
                _stt_fade["after"] = None
            _stt_banner_label[0].config(text=text, fg=color)
            _hud_draw()
            b = _stt_banner[0]
            # 如果窗口已被 withdraw（淡出后隐藏），先 deiconify 恢复，
            # 否则 update_idletasks/geometry/ShowWindow 对已隐藏窗口可能不生效 → 状态条消失
            if not b.winfo_ismapped():
                try:
                    b.deiconify()
                except Exception:
                    pass
            # 已显示时只更新文本和位置，不重复 deiconify/geometry（避免激活 root）
            b.update_idletasks()
            w, h = b.winfo_reqwidth(), b.winfo_reqheight()
            sw, sh = b.winfo_screenwidth(), b.winfo_screenheight()
            b.geometry("+%d+%d" % (max(0, (sw - w) // 2), max(0, sh - h - 80)))  # 任务栏上方居中
            # 用 SW_SHOWNOACTIVATE + SetWindowPos(SWP_NOACTIVATE) 显示，完全不激活、不抢焦点
            try:
                import ctypes
                _hwnd_raw = int(b.winfo_id())
                _hwnd_parent = ctypes.windll.user32.GetParent(_hwnd_raw)
                hwnd = _hwnd_parent if _hwnd_parent else _hwnd_raw
                SW_SHOWNOACTIVATE = 4
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                SWP_FRAMECHANGED = 0x0020
                SWP_NOZORDER = 0x0004
                # 显示前再次确保样式生效（防止 Tkinter 在 geometry/update 时覆盖）
                GWL_EXSTYLE = -20
                GWLP_HWNDPARENT = -8
                WS_EX_TOOLWINDOW = 0x00000080
                WS_EX_NOACTIVATE = 0x08000000
                ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if not (ex & WS_EX_TOOLWINDOW):
                    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
                    ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
                ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            except Exception:
                b.deiconify()  # 只用 deiconify，不用 lift（lift 会激活 root）
            _force_root_withdrawn()  # 显示后立即强制 root 保持隐藏
            if not _stt_fade["ready"]:  # 首次/被淡出打断后淡入；内容更新（anim/逐字）不打断透明度
                try:
                    _hud_set_alpha(int(b.winfo_id()), 0)
                    def _fade_in(n):
                        if _stt_banner[0] is not None:
                            try:
                                _hud_set_alpha(int(_stt_banner[0].winfo_id()), min(170, n * 17))
                            except Exception:
                                pass
                            _force_root_withdrawn()  # 淡入每帧都强制 root 保持隐藏
                            if n < 10:
                                _stt_fade["after"] = root.after(30, lambda: _fade_in(n + 1))
                            else:
                                _stt_fade["after"] = None
                                _stt_fade["ready"] = True
                                _force_root_withdrawn()
                    _fade_in(1)
                except Exception:
                    _stt_fade["ready"] = True
        except Exception:
            pass

    def _stt_banner_hide():
        """0.5s 平滑淡出后隐藏（LWA alpha 170→0，15 帧 × 33ms）。"""
        try:
            if _stt_banner[0] is None:
                return
            if _stt_fade["after"] is not None:
                try:
                    root.after_cancel(_stt_fade["after"])
                except Exception:
                    pass
                _stt_fade["after"] = None
            _stt_fade["ready"] = False

            def _fade_out(n):
                try:
                    if _stt_banner[0] is not None:
                        _hud_set_alpha(int(_stt_banner[0].winfo_id()),
                                       max(0, int(170 * (1 - n / 15.0))))
                except Exception:
                    pass
                if n < 15:
                    _stt_fade["after"] = root.after(33, lambda: _fade_out(n + 1))
                else:
                    _stt_fade["after"] = None
                    try:
                        if _stt_banner[0] is not None:
                            _stt_banner[0].withdraw()
                            _force_root_withdrawn()
                    except Exception:
                        pass
            _fade_out(0)
        except Exception:
            try:
                if _stt_banner[0] is not None:
                    _stt_banner[0].withdraw()
            except Exception:
                pass

    def _stt_wave_start():
        """录音中的声浪波形：五根白色竖条，高度随实时 RMS 平滑起伏（约 14fps）。"""
        _stt_wave["active"] = True
        _stt_wave["smooth"] = 0.0
        if _stt_wave["after"] is None:
            _stt_wave_tick()

    def _stt_wave_stop():
        _stt_wave["active"] = False
        if _stt_wave["after"] is not None:
            try:
                root.after_cancel(_stt_wave["after"])
            except Exception:
                pass
            _stt_wave["after"] = None

    def _stt_wave_tick():
        if not _stt_wave["active"]:
            return
        if not _stt_recorder.active:  # 仅录音中活跃；松开快捷键自动停
            _stt_wave_stop()
            return
        try:
            v = min(1.0, _stt_recorder.rms / 1200.0)
            _stt_wave["smooth"] = 0.7 * _stt_wave["smooth"] + 0.3 * v
            cv = _stt_banner_canvas[0]
            b = _stt_banner[0]
            if cv is not None and b is not None and b.winfo_ismapped():
                _hud_draw()
                b.update_idletasks()
                w, h = b.winfo_reqwidth(), b.winfo_reqheight()
                sw, sh = b.winfo_screenwidth(), b.winfo_screenheight()
                b.geometry("+%d+%d" % (max(0, (sw - w) // 2), max(0, sh - h - 80)))
        except Exception:
            pass
        _stt_wave["after"] = root.after(70, _stt_wave_tick)

    # 测试/调试入口：把 HUD 闭包挂到 root 属性（正常运行无影响）
    root._fewtype_hud_show = _stt_banner_show
    root._fewtype_hud_hide = _stt_banner_hide
    root._fewtype_hud_wave_start = _stt_wave_start
    root._fewtype_hud_wave_stop = _stt_wave_stop
    root._fewtype_hud_banner = _stt_banner
    root._fewtype_hud_recorder = _stt_recorder
    root._fewtype_hud_set_alpha = _hud_set_alpha

    def _stt_anim_start(text_zh: str = "", text_en: str = "", track_elapsed: bool = False, _t0=None):
        """更新状态文本 + 悬浮状态条（banner）+ 启动声浪波形。
        UI 重构后原 _stt_anim_start 被删除，此处提供兼容包装。"""
        try:
            if text_zh:
                stt_status.set(_pd(text_zh, text_en or text_zh))
                # 同时更新悬浮状态条（banner），让用户知道当前在做什么，不会以为冻住
                try:
                    _stt_banner_show(_pd(text_zh, text_en or text_zh), "#34D399")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            _stt_wave_start()
        except Exception:
            pass

    def _stt_anim_stop():
        """兼容旧调用：停止声浪波形。"""
        try:
            _stt_wave_stop()
        except Exception:
            pass

    def _play_done_sound():
        try:
            import winsound
            data = _click_wav()
            if data:
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC)
        except Exception:
            pass

    def _stt_banner_typewrite(full: str, color: str = "#e8eaed", keep_ms: int = 5000):
        """转录文字滚动显示：像微信语音转文字那样逐字滚出。"""
        _stt_anim_stop()
        if not full:
            _stt_banner_hide()
            return

        def tick(i):
            nxt = min(len(full), i + 3)
            _stt_banner_show(full[:nxt], color)
            if nxt >= len(full):
                root.after(keep_ms, _stt_banner_hide)
                return
            delay = 34 if nxt < 60 else 12  # 开头稍慢，长文自动提速
            root.after(delay, lambda: tick(nxt))

        tick(0)


    # ---------- 火山流式（platform=volcengine 时启用）：按住边录边传 + interim 实时上屏 ----------
    _stt_stream = {"session": None, "queue": None, "sender": None}

    def _stt_stream_show(text: str):
        if not _stt_busy[0]:
            return
        _stt_anim_stop()
        stt_status.set(_pd("识别中…", "Listening…"))
        _stt_banner_show(text[:140] if text else _pd("聆听中…", "Listening…"))

    def _stt_stream_start(prov):
        import queue as _q
        from volcengine_asr import VolcengineStreamSession

        def _on_partial(t):
            try:
                root.after(0, lambda t=t: _stt_stream_show(t))
            except Exception:
                pass

        sess = VolcengineStreamSession(
            prov.api_key,
            on_partial=_on_partial,
            resource_id=prov.asr_model or "volc.seedasr.sauc.duration",
            hotwords=stt_engine.load_hotwords(),
        )
        sess.start()
        q = _q.Queue()

        def _sender():
            while True:
                item = q.get()
                if item is None:
                    break
                try:
                    sess.send_audio(item)
                except Exception:
                    break

        th = threading.Thread(target=_sender, daemon=True)
        th.start()
        _stt_stream.update({"session": sess, "queue": q, "sender": th})
        return _stt_stream

    def _stt_finish_stream():
        sess = _stt_stream["session"]
        q = _stt_stream["queue"]
        # 主线程预读 Tk 变量（工作线程访问 Tk 变量可能使 Tcl 解释器崩溃）
        _mode = stt_mode_var.get()
        _tr = stt_translate_var.get()
        _lang = stt_target_var.get()
        _custom_prompt = None
        if _mode == "custom":
            _name = stt_custom_var.get()
            for _s in cfg.get("stt_styles", []):
                if _s.get("name") == _name:
                    _custom_prompt = _s.get("prompt", "")
                    break
            if not _custom_prompt:
                _stt_busy[0] = False
                _stt_stream["session"] = None
                try:
                    sess.abort()
                except Exception:
                    pass
                stt_status.set(_pd("请先在「配置风格」中填写 Prompt", "Set a prompt in Configure styles first"))
                _stt_banner_show(_pd("请先在「配置风格」中填写 Prompt", "Set a prompt in Configure styles first"), "#ff6b6b")
                root.after(1800, _stt_banner_hide)
                return
        try:
            _stt_recorder.stop_stream()
        except Exception:
            pass
        _stt_recorder.set_on_block(None)
        q.put(None)  # 通知发送线程：录音块已全部发出
        _fast_path = _mode == "verbatim" and not _tr

        def work():
            try:
                if _stt_cancel.is_set():
                    return
                try:
                    _stt_stream["sender"].join(timeout=4)
                except Exception:
                    pass
                if _stt_cancel.is_set():
                    return
                ui_log("stt_stream: sender joined")
                if _fast_path:
                    _stt_anim_start("转录中", "Transcribing", track_elapsed=True, _t0=time.time())
                else:
                    _stt_anim_start("正在收尾", "Finalizing", track_elapsed=True, _t0=time.time())
                text = sess.finish(timeout=10)
                if _stt_cancel.is_set():
                    return
                ui_log("stt_stream: finish ok (%d chars)" % len(text))
                if not text.strip():
                    raise ProviderError("转写结果为空")
                if _fast_path:
                    # fast path（忠实记录/单通道）：不调 LLM，但仍需过 normalize 去空格/补标点
                    from normalize import normalize_text
                    final = normalize_text(text.strip())
                    root.after(0, lambda final=final: _stt_commit(final))
                else:
                    # 需要 LLM（风格/翻译）：先弹确认窗口，确认后再翻译上屏
                    _stt_anim_stop()
                    root.after(0, lambda raw=text: _stt_ask_confirm(
                        raw, _mode, _tr, _lang, _custom_prompt))
            except Exception as e:
                root.after(0, lambda e=e: _stt_error(e))
            finally:
                _stt_stream["session"] = None
                try:
                    sess.abort()
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _stt_begin():
        _force_root_withdrawn()  # 托盘模式下强制 root 保持隐藏
        # 注意：不在此清理修饰键——用户还按着修饰键，注入的 KEYUP 会被 Windows 忽略（物理键仍按下）。
        # 真正的清理在 _stt_finish() 开头（用户已松开所有键，注入 KEYUP 生效）。
        if _stt_busy[0]:
            ui_log("stt_begin blocked: busy")
            return
        if not _ensure_provider():
            ui_log("stt_begin blocked: no provider")
            return
        _stt_busy[0] = True
        _stt_cancel.clear()  # 新任务开始，重置"中止当前服务"标记
        _prov = _provider_from_cfg()
        # 火山引擎 → 流式路径：按住时边录边传，interim 实时上屏
        if _prov.platform == "volcengine":
            try:
                _stt_anim_start("", "")
                _sess = _stt_stream_start(_prov)
                _stt_recorder.set_on_block(lambda d, s=_sess: s["queue"].put(d.tobytes()))
                _stt_recorder.start()
            except Exception as e:
                _stt_busy[0] = False
                _stt_anim_stop()
                _stt_recorder.set_on_block(None)
                stt_status.set(_pd("流式启动失败", "Failed to start streaming") + ": %s" % e)
                ui_log(_pd("流式启动失败", "Failed to start streaming") + ": %s" % e)
                _stt_banner_show(_pd("流式启动失败", "Failed to start streaming") + ": %s" % e, "#ff6b6b")
            return
        # Block 模式聆听阶段：状态提示"聆听中"（Streaming 走 _stt_stream_show 逐字上屏）
        _stt_anim_start("聆听中", "Listening")
        try:
            _stt_recorder.start()
        except Exception as e:
            _stt_busy[0] = False
            _stt_anim_stop()
            _stt_wave_stop()
            stt_status.set(_pd("录音启动失败", "Failed to start recording") + ": %s" % e)
            ui_log(_pd("录音启动失败", "Failed to start recording") + ": %s" % e)
            _stt_banner_show(_pd("录音启动失败", "Failed to start recording") + ": %s" % e, "#ff6b6b")
            return
        _stt_wave_start()

    def _stt_finish():
        # 用户已松开所有键，此时清理可能卡住的修饰键。
        # 根因：用户松开修饰键时物理 KEYUP 被钩子 _swallow_keys 吞掉，系统收不到 KEYUP → 永久卡住。
        # 确定性方案：卸载钩子 → 清理 → 重装钩子（和 _simulate_ctrl_v 一样，注入按键绝对不会被吞）。
        _hk_obj = _hotkey_listener[0] if _hotkey_listener else None
        if _hk_obj is not None:
            try:
                _hk_obj.stop()  # 卸载钩子
            except Exception:
                pass
        try:
            import ctypes as _ct
            _u = _ct.windll.user32
            KEYUP = 0x0002
            KEYDOWN = 0x0000
            # 完整按键周期（down+up）确保系统收到，避免只发 up 时状态不同步
            _u.keybd_event(0x12, 0, KEYDOWN, 0); _u.keybd_event(0x12, 0, KEYUP, 0)  # ALT
            _u.keybd_event(0x11, 0, KEYDOWN, 0); _u.keybd_event(0x11, 0, KEYUP, 0)  # Ctrl
            # Win 用 Ctrl 掩护（Ctrl 按着时 Windows 不弹开始菜单）
            _u.keybd_event(0x11, 0, KEYDOWN, 0)  # Ctrl down
            import time as _t; _t.sleep(0.01)
            _u.keybd_event(0x5B, 0, KEYDOWN, 0); _u.keybd_event(0x5B, 0, KEYUP, 0)  # LWIN
            _u.keybd_event(0x5C, 0, KEYDOWN, 0); _u.keybd_event(0x5C, 0, KEYUP, 0)  # RWIN
            _t.sleep(0.01)
            _u.keybd_event(0x11, 0, KEYUP, 0)  # Ctrl up
        except Exception:
            pass
        # 延迟重装钩子（确保模拟按键已被系统处理）
        if _hk_obj is not None:
            try:
                root.after(80, lambda: _hk_obj.start())
            except Exception:
                _hk_obj.start()
        if not _stt_busy[0]:
            return
        _stt_anim_stop()
        if _stt_stream.get("session") is not None:
            _stt_finish_stream()
            return
        _stt_recorder.stop_stream()

        _lang_hint = (TARGET_LOCALE.get(stt_target_var.get(), "zh-CN") or "zh-CN").split("-")[0].lower()
        _fast_path = stt_mode_var.get() == "verbatim" and not stt_translate_var.get()
        _TIMEOUT_MSG = _pd(
            "转写超时（超过 10 秒），已停止。请检查网络/代理或缩短录音",
            "Transcription timeout (>10s), stopped. Check network/proxy or shorten the recording",
        )

        # 整段一次转写：按住说话 → 松开 → 完整音频（降噪+VAD+AGC 已在 stop() 内完成）→ 单次 ASR → 按需 LLM
        raw = _stt_recorder.stop()
        if raw is None:
            _stt_busy[0] = False
            stt_status.set(_pd("未检测到语音，请重试", "No speech detected, try again"))
            _stt_banner_show(_pd("未检测到语音，请重试", "No speech detected, try again"), "#ffb74d")
            root.after(1800, _stt_banner_hide)
            return
        _STT_BUDGET = 25.0
        _deadline = time.time() + _STT_BUDGET

        def _budget_ok() -> bool:
            return time.time() < _deadline - 1.5  # 留 1.5s 余量给提交

        if _fast_path:
            _stt_anim_start("极速记录中", "Fast recording", track_elapsed=True, _t0=time.time())
        else:
            _stt_anim_start("正在转写", "Transcribing", track_elapsed=True, _t0=time.time())

        # 主线程预读 Tk 变量（Tk 变量在工作线程访问可能使 Tcl 解释器崩溃——"说一段就退出"的根因）
        _mode = stt_mode_var.get()
        _tr = stt_translate_var.get()
        _lang = stt_target_var.get()
        _custom_prompt = None
        if _mode == "custom":
            _name = stt_custom_var.get()
            for _s in cfg.get("stt_styles", []):
                if _s.get("name") == _name:
                    _custom_prompt = _s.get("prompt", "")
                    break
            if not _custom_prompt:
                _stt_busy[0] = False
                stt_status.set(_pd("请先在「配置风格」中填写 Prompt", "Set a prompt in Configure styles first"))
                _stt_banner_show(_pd("请先在「配置风格」中填写 Prompt", "Set a prompt in Configure styles first"), "#ff6b6b")
                root.after(1800, _stt_banner_hide)
                return

        def work():
            try:
                import tempfile
                prov = _provider_from_cfg()
                if _stt_cancel.is_set():
                    return  # 已被"中止当前服务"中止，静默退出
                if not _rate_wait("asr", _deadline):
                    raise ProviderError(_pd("达到语音识别速率限制，请稍候重试", "ASR rate limit reached, retry in a moment"))
                if not _budget_ok():
                    raise ProviderError(_TIMEOUT_MSG)
                _remain = _deadline - time.time()
                # 压缩为 MP3 上传（lameenc 编码，64kbps，体积约 WAV 的 1/8-1/10，传输显著变快）；压缩失败回退 WAV
                mp3 = stt_engine.compress_audio(raw, _stt_recorder.sr)
                if mp3:
                    tmp = Path(tempfile.gettempdir()) / "fewtype_stt.mp3"
                    tmp.write_bytes(mp3)
                else:
                    tmp = Path(tempfile.gettempdir()) / "fewtype_stt.wav"
                    tmp.write_bytes(stt_engine.wav_bytes_from_int16(raw, _stt_recorder.sr))
                text = prov.transcribe(
                    tmp,
                    prompt=stt_engine.build_whisper_prompt(_lang_hint),
                    timeout=max(2, int(_remain - 1)),
                )
                if _stt_cancel.is_set():
                    return
                if not text.strip():
                    raise ProviderError("转写结果为空")
                if not _fast_path:
                    if _stt_cancel.is_set():
                        return
                    _stt_anim_start("润色转写中", "Polishing")
                    if time.time() > _deadline - 1.5:
                        # 预算不足：直接上屏 ASR 原始结果，不报错（仍过 normalize 去空格）
                        from normalize import normalize_text
                        final = normalize_text(text.strip())
                        root.after(0, lambda: _stt_banner_show(
                            "润色超时，已上屏原始结果", "#ffb74d"))
                        root.after(0, lambda: root.after(2500, _stt_banner_hide))
                    else:
                        if not _rate_wait("llm", _deadline):
                            raise ProviderError(_pd("达到文本处理速率限制，请稍候重试", "LLM rate limit reached, retry in a moment"))
                        _remain2 = _deadline - time.time()
                        try:
                            final = stt_engine.process_result(
                                prov, text, _mode, _tr, _lang,
                                custom_prompt=_custom_prompt,
                                timeout=max(2, int(_remain2 - 1)),
                            )
                        except Exception as e:
                            # LLM 润色失败/超时：fallback 到 ASR 原始结果上屏（仍过 normalize）
                            ui_log("LLM 润色失败，上屏原始结果: %s" % e)
                            from normalize import normalize_text
                            final = normalize_text(text.strip())
                            root.after(0, lambda: _stt_banner_show(
                                "润色超时，已上屏原始结果", "#ffb74d"))
                            root.after(0, lambda: root.after(2500, _stt_banner_hide))
                else:
                    # fast path（忠实记录/单通道）：不调 LLM，但仍需过 normalize 去空格/补标点
                    from normalize import normalize_text
                    final = normalize_text(text.strip())
                root.after(0, lambda final=final: _stt_commit(final))
            except Exception as e:
                root.after(0, lambda e=e: _stt_error(e))

        threading.Thread(target=work, daemon=True).start()

    def _is_bridge_foreground() -> bool:
        """前台窗口是否属于 bridge（主窗口或横幅）。是则跳过自动上屏，避免把结果粘贴进自己窗口造成"双份"。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            if not fg:
                return True
            if fg == root.winfo_toplevel().winfo_id():
                return True
            for w in root.winfo_children():
                if isinstance(w, tk.Toplevel):
                    try:
                        if fg == w.winfo_id():
                            return True
                    except Exception:
                        pass
            return False
        except Exception:
            return True

    def _simulate_ctrl_v():
        # 彻底方案：卸载钩子 → 强制清理修饰键状态 → 模拟 Ctrl+V → 重装钩子。
        # 根因：keybd_event 异步 + LL 钩子竞态，模拟的 Ctrl up 可能被钩子吞掉
        # → 系统按键状态表记录 Ctrl 一直按下 → 键盘全乱，必须重启恢复。
        # 卸载钩子后不存在任何东西能吞模拟按键，完全消除竞态。
        import ctypes, time
        user32 = ctypes.windll.user32
        VK_CONTROL, VK_V = 0x11, 0x56
        VK_LWIN, VK_RWIN = 0x5B, 0x5C
        VK_MENU = 0x12  # ALT
        KEYUP = 0x0002
        def _keystate(vk):
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)
        hook_obj = _hotkey_listener[0] if _hotkey_listener else None
        # 1. 卸载钩子（完全消除钩子与模拟按键的竞态）
        if hook_obj is not None:
            hook_obj.stop()
        # 1.2 清理 ALT + 按需发 ESC：
        # - ALT KEYUP：只释放用户可能卡住的 ALT（纯释放，无副作用）
        # - ESC：仅在目标窗口【确有 Win32 菜单栏】（GetMenu 非 NULL，如记事本）时才发，
        #   用于关闭 Alt 激活的菜单栏，避免后续 Ctrl+V 的 'V' 被菜单栏当作助记键吃掉。
        #   无条件发 ESC 会误关现代应用浮层：微信被最小化 / Chrome 侧栏收起 / 豆包搜索框消失
        #   （Tauri 3.1.11 同款坑，用户实测 2.0 同样中招）。
        #   自绘菜单应用（Chrome/Firefox/Electron）GetMenu 返回 NULL 且下面焦点恢复
        #   不再模拟 Alt（见 1.5），菜单根本不会被激活，无需也不该发 ESC。
        try:
            # GetMenu 需要 64 位安全的句柄参数声明（不设 argtypes 会按 32 位截断）
            try:
                user32.GetMenu.argtypes = [ctypes.c_void_p]
                user32.GetMenu.restype = ctypes.c_void_p
            except Exception:
                pass
            user32.keybd_event(VK_MENU, 0, KEYUP, 0)  # ALT KEYUP（用户先按 ALT 时 ALT 卡住）
            _need_esc = False
            try:
                _esc_tgt = _stt_target.get("hwnd", 0)
                if _esc_tgt and user32.GetMenu(_esc_tgt):
                    _need_esc = True
            except Exception:
                pass
            if _need_esc:
                user32.keybd_event(0x1B, 0, 0, 0)  # ESC down
                user32.keybd_event(0x1B, 0, KEYUP, 0)  # ESC up（清理 Alt 激活的菜单栏）
            time.sleep(0.03)
        except Exception:
            pass
        # 1.5 强制把焦点还给说话前的前台窗口（AttachThreadInput+SetForegroundWindow）
        # 避免 bridge 主窗口被状态条唤出抢占焦点，导致 Ctrl+V 粘贴到 bridge 自己窗口
        try:
            target_hwnd = _stt_target.get("hwnd", 0)
            try:
                _fg0 = user32.GetForegroundWindow()
            except Exception:
                _fg0 = 0
            ui_log("commit-focus: target=%s fg_before=%s" % (target_hwnd, _fg0))
            _focus_dbg("commit-focus target=%s fg_before=%s" % (target_hwnd, _fg0))
            if target_hwnd:
                import ctypes as _ct
                _user32 = _ct.windll.user32
                _kernel32 = _ct.windll.kernel32
                if not _user32.IsWindow(target_hwnd):
                    ui_log("commit-focus: target invalid (window closed)")
                    target_hwnd = 0
                if target_hwnd:
                    fg_thread = _user32.GetWindowThreadProcessId(target_hwnd, None)
                    cur_thread = _kernel32.GetCurrentThreadId()
                    # 不再模拟 ALT 解锁（旧逻辑）——Alt 按下/抬起会激活 Chrome/Firefox/Electron
                    # 的自绘菜单栏（这些窗口 GetMenu 返回 NULL，上面 1.2 的 ESC 判定不触发），
                    # 菜单栏一直开着会吞掉 Ctrl+V 的 'V' → Gemini/ChatGPT/GitHub/百度/Google
                    # 等网页文本框上屏失败（Tauri 3.1.12 同款坑，用户实测 2.0 同样中招）。
                    # 改用 AttachThreadInput：让本线程临时共享前台线程的输入队列，
                    # Windows 即认为本进程有前台资格，SetForegroundWindow 不再被前台锁定拒绝，
                    # 全程不产生任何按键事件，菜单栏永不激活。
                    # 重试直到焦点真正落到目标窗口（最多 5 次），失败也不静默继续
                    for _attempt in range(5):
                        _attached = False
                        try:
                            _attached = bool(_user32.AttachThreadInput(cur_thread, fg_thread, True))
                        except Exception:
                            _attached = False
                        try:
                            _user32.BringWindowToTop(target_hwnd)
                            _user32.SetForegroundWindow(target_hwnd)
                        except Exception:
                            pass
                        if _attached:
                            try:
                                _user32.AttachThreadInput(cur_thread, fg_thread, False)
                            except Exception:
                                pass
                        time.sleep(0.08)
                        if _user32.GetForegroundWindow() == target_hwnd:
                            ui_log("commit-focus: ok attempt=%d" % _attempt)
                            _focus_dbg("commit-focus OK attempt=%d" % _attempt)
                            break
                    else:
                        # AttachThreadInput 不可用 / 5 次均被拒：fallback 旧逻辑（模拟 ALT 解锁），
                        # 仅极端情况走到，对自绘菜单应用可能仍失败，但总比焦点不恢复好
                        try:
                            _user32.keybd_event(0x12, 0, 0, 0)
                            _user32.keybd_event(0x12, 0, 0x0002, 0)
                            time.sleep(0.03)
                            _user32.BringWindowToTop(target_hwnd)
                            _user32.SetForegroundWindow(target_hwnd)
                            time.sleep(0.08)
                        except Exception:
                            pass
                        ui_log("commit-focus: FAILED all attempts, fg=%s" % _user32.GetForegroundWindow())
                        _focus_dbg("commit-focus FAILED fg=%s" % _user32.GetForegroundWindow())
        except Exception as e:
            ui_log("commit-focus: exception %s" % e)
            _focus_dbg("commit-focus EXC %s" % e)
        try:
            # 2. 模拟 Ctrl+V（此时无钩子，不会被吞）
            # 先 Ctrl down：系统知道 Ctrl 按下
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            # 再发 Win KEYUP：清理可能卡住的 Win 状态（用户先按 Win 时 Win down 没被吞但 up 被吞）。
            # 此时 Ctrl 按着，Windows 检测到 Win 按下期间有其他键（Ctrl），不会弹开始菜单。
            user32.keybd_event(VK_LWIN, 0, KEYUP, 0)
            user32.keybd_event(VK_RWIN, 0, KEYUP, 0)
            time.sleep(0.02)
            # 正常 Ctrl+V
            user32.keybd_event(VK_V, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(VK_V, 0, KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)
            time.sleep(0.03)  # 确保模拟事件处理完
            _focus_dbg("keybd_event ctrl+v sent")
        except Exception as e:
            ui_log(_pd("自动上屏失败，请手动 Ctrl+V", "Auto-commit failed, press Ctrl+V manually") + ": %s" % e)
            # 异常路径也强制 release，防止半发送序列留物理键卡住
            try:
                user32.keybd_event(VK_V, 0, KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)
                user32.keybd_event(VK_MENU, 0, KEYUP, 0)
                user32.keybd_event(VK_LWIN, 0, KEYUP, 0)
                user32.keybd_event(VK_RWIN, 0, KEYUP, 0)
            except Exception:
                pass
        finally:
            # 4. 重装钩子 + 短时间 _ignore_all 保险
            if hook_obj is not None:
                hook_obj.start()
                hook_obj._ignore_all = True
                hook_obj._swallow_keys.clear()
                root.after(80, lambda: setattr(hook_obj, '_ignore_all', False))

    def _stt_ask_confirm(raw, mode, tr, lang, custom_prompt):
        """确认原文再翻译：转写完成后弹出确认窗口（原文可编辑）。
        回车或「翻译上屏」→ LLM 翻译 → 上屏；取消 → 丢弃。
        - 窗口置顶 + 聚焦文本末尾（用户可直接回车确认）
        - 长文本 max_tokens 动态放大（512 对长句会截断/空响应，Tauri 版踩过坑）
        - 上屏复用 _stt_commit（含焦点恢复到录音前窗口）"""
        win = tk.Toplevel(root)
        win.title(_pd("确认原文", "Confirm text"))
        win.configure(bg=BG)
        win.overrideredirect(True)  # 无边框无任务栏（确认窗口不占任务栏条目）
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.update_idletasks()
        w = 520
        # 文本自适应高度：一行时只留一行高；长文本封顶 6 行（11pt 中文字符约 19px 宽）
        body_w = w - 14 * 2 - 2
        chars_per_line = max(10, body_w // 19)
        _n_lines = 0
        for _line in raw.split("\n"):
            _n_lines += max(1, (len(_line) + chars_per_line - 1) // chars_per_line)
        h_lines = max(1, min(_n_lines, 6))
        h = 92 + h_lines * 26 + 8  # 提示行+按钮行+边距 92，Text 每行 26px
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = (sw - w) // 2
        y = max(60, sh - 52 - h - 12)  # 贴任务栏上方
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.lift()

        card = tk.Frame(win, bg="#121A15", bd=0)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        tk.Label(card, text=_pd("确认原文：回车翻译上屏 · ESC 取消",
                                "Confirm: Enter translate & commit · ESC cancel"),
                 bg="#121A15", fg="#8A9A90",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(10, 6))
        txt = tk.Text(card, height=h_lines, wrap="word",
                      bg="#0B0F0C", fg="#E8EAED", insertbackground="#E8EAED",
                      relief=tk.FLAT, bd=0,
                      highlightthickness=1, highlightbackground="#1F3A2E",
                      highlightcolor=ACCENT, font=("Microsoft YaHei UI", 11))
        txt.pack(fill=tk.BOTH, expand=True, padx=14)
        txt.insert("1.0", raw)
        txt.mark_set("insert", "end")
        txt.see("end")  # 长文本滚动到末尾
        win.focus_force()  # 激活窗口（Windows 前台锁定下 lift 不够，必须强抢）
        txt.focus_force()  # 强制键盘焦点到原文编辑框
        # 延迟再强一次：等窗口真正映射完成（刚创建时 focus 可能被系统丢弃）
        win.after(80, lambda: (win.focus_force(), txt.focus_force()))

        btn_row = tk.Frame(card, bg="#121A15")
        btn_row.pack(fill=tk.X, padx=14, pady=12)
        cancel_btn = tk.Button(btn_row, text=_pd("取消", "Cancel"),
                               bg="#0B0F0C", fg="#9CA3AF", relief=tk.FLAT, bd=0,
                               font=("Microsoft YaHei UI", 9), cursor="hand2",
                               padx=18, pady=5, activebackground="#1F2937",
                               activeforeground="#E8EAED")
        cancel_btn.pack(side=tk.LEFT)
        ok_btn = tk.Button(btn_row, text=_pd("翻译上屏", "Translate & Commit"),
                           bg=ACCENT, fg=BG, relief=tk.FLAT, bd=0,
                           font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2",
                           padx=22, pady=5, activebackground=ACCENT2,
                           activeforeground=BG)
        ok_btn.pack(side=tk.RIGHT)

        def _on_cancel():
            win.destroy()
            _stt_busy[0] = False  # 释放 busy，恢复可录音
            stt_status.set(_pd("已取消", "Cancelled"))
            root.after(150, _stt_banner_hide)

        def _do_translate():
            edited = txt.get("1.0", "end-1c").strip()
            if not edited:
                return
            win.destroy()
            _stt_busy[0] = True  # 翻译期间继续占 busy，防并发
            _stt_anim_start(_pd("翻译中…", "Translating…"))

            def work2():
                try:
                    if _stt_cancel.is_set():
                        return
                    prov = _provider_from_cfg()
                    # 长文本放大 max_tokens（512 对长句会截断/空响应）
                    mt = max(2048, min(len(edited) * 3, 4096))
                    final = stt_engine.process_result(
                        prov, edited, mode, tr, lang,
                        custom_prompt=custom_prompt, timeout=45, max_tokens=mt,
                    )
                    if not final or not final.strip():
                        raise ProviderError("LLM 返回为空")
                except Exception as e:
                    ui_log("LLM 处理失败，上屏原始结果: %s" % e)
                    final = edited
                    root.after(0, lambda: _stt_banner_show(
                        _pd("LLM 处理失败，已上屏原文", "LLM failed, committed original"), "#ffb74d"))
                    root.after(0, lambda: root.after(2500, _stt_banner_hide))
                root.after(0, lambda final=final: _stt_commit(final))

            threading.Thread(target=work2, daemon=True).start()

        cancel_btn.configure(command=_on_cancel)
        ok_btn.configure(command=_do_translate)
        txt.bind("<Return>", lambda e: (_do_translate(), "break")[1])
        txt.bind("<Control-Return>", lambda e: (txt.insert("insert", "\n"), "break")[1])
        win.bind("<Escape>", lambda e: _on_cancel())
        win.protocol("WM_DELETE_WINDOW", _on_cancel)
        win.grab_set()  # 模态：确认窗口打开期间主 UI 不响应

    def _stt_commit(final: str):
        _stt_busy[0] = False
        _stt_anim_stop()
        _stt_wave_stop()
        _play_done_sound()
        try:
            root.clipboard_clear()
            root.clipboard_append(final)
        except Exception:
            pass
        if stt_auto_commit.get():
            # 模拟 Ctrl+V 粘贴（通用：Electron/Chrome/豆包等现代应用都响应；
            # PostMessage WM_PASTE 对这类应用无效，已弃用）
            try:
                if not _is_bridge_foreground():
                    _simulate_ctrl_v()
            except Exception as e:
                ui_log("auto-commit failed: %s" % e)
        _force_root_withdrawn()  # 托盘模式下强制 root 保持隐藏
        # 完成不向用户报告（"已复制到剪贴板"无需提示）：状态条直接淡出，状态行回"就绪"
        stt_status.set(_pd("就绪", "Ready"))
        root.after(150, _stt_banner_hide)

    def _stt_error(e: Exception):
        _stt_busy[0] = False
        _stt_anim_stop()
        _stt_wave_stop()
        _force_root_withdrawn()
        msg = _pd("转写失败", "Transcription failed") + "：" + _classify_error(e)
        stt_status.set(msg)
        ui_log(msg + " | " + str(e))
        _stt_banner_show(msg, "#ff6b6b")
        root.after(4000, _stt_banner_hide)

    def _start_hotkey():
        from hotkey_hook import WinHotkey, parse_combo
        combo = (cfg.get("stt_hotkey") or "ctrl+win").lower()
        MAX_REC_SEC = 45.0  # 最长录音时长，超时自动结束（防卡死）
        _hk = {"rec": False, "armed_at": 0.0, "pending": None}
        # 关键：钩子回调可能执行在 pystray 消息线程（非 Tk 主线程），回调里绝不能碰 Tk
        # （跨线程 root.after 轻则异常被吞=热键无效，重则 GIL 崩坏=程序退出）。
        # 回调只做纯 Python 赋值 + ctypes Win32 调用，主线程轮询标志再执行真实动作。

        def _begin():
            if _hk["rec"]:
                return
            _hk["rec"] = True
            _hk["armed_at"] = time.time()
            try:  # 记录说话前的前台窗口，松开后无感上屏到它（ctypes 调用无 GIL 风险）
                import ctypes
                _stt_target["hwnd"] = ctypes.windll.user32.GetForegroundWindow()
                ui_log("begin: record target hwnd=%s" % _stt_target["hwnd"])
                _focus_dbg("begin target hwnd=%s" % _stt_target["hwnd"])
            except Exception:
                _stt_target["hwnd"] = 0
            _hk["pending"] = "begin"

        def _end():
            if not _hk["rec"]:
                return
            _hk["rec"] = False
            _hk["pending"] = "end"

        def _poll_hotkey():
            p = _hk["pending"]
            if p == "begin":
                _hk["pending"] = None
                _stt_begin()
            elif p == "end":
                _hk["pending"] = None
                _stt_finish()
            root.after(50, _poll_hotkey)

        def _hotkey_timeout():
            if _hk["rec"] and time.time() - _hk["armed_at"] >= MAX_REC_SEC:
                _hk["rec"] = False
                ui_log("hotkey: %ds timeout finish" % MAX_REC_SEC)
                _stt_finish()
            else:
                root.after(5000, _hotkey_timeout)

        # 根据热键配置选择模式：combo（组合键）或 double_ctrl（双击 Ctrl）
        if combo == "double_ctrl":
            # 双击 Ctrl 模式：mods/trigger 传空，mode="double_ctrl"
            hook = WinHotkey([], None, _begin, _end, mode="double_ctrl")
        else:
            # 组合键模式：才需要 parse_combo 和回退逻辑
            mods, trigger = parse_combo(combo)
            if not mods:  # 配置异常时回退默认
                mods, trigger = ["ctrl", "win"], None
            hook = WinHotkey(mods, trigger, _begin, _end, mode="combo")
        if not hook.start():
            ui_log(_pd("热键钩子启动失败", "Hotkey hook failed to start"))
        root.after(50, _poll_hotkey)
        root.after(5000, _hotkey_timeout)
        # 日志提示根据模式不同
        if combo == "double_ctrl":
            ui_log(_pd("语音输入热键已启用：双击 Ctrl 并按住说话，松开自动上屏 [mode=double_ctrl]",
                        "Voice input hotkey enabled: double-tap Ctrl and hold to speak, release to commit [mode=double_ctrl]"))
        else:
            ui_log(_pd("语音输入热键已启用：按住 %s 说话 [mode=combo]",
                        "Voice input hotkey enabled: hold %s to speak [mode=combo]") % _hotkey_display(combo))
        return hook

    _hotkey_listener = [None]

    def _update_hotkey_hint():
        """热键配置变更后更新当前场景的按钮文字和说明 Label。
        两个场景各自定义 _update_hotkey_btn 并存到 root._fewtype_update_hotkey，
        这里统一调用，避免 NameError。"""
        fn = getattr(root, "_fewtype_update_hotkey", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _restart_hotkey():
        if _hotkey_listener[0] is not None:
            try:
                _hotkey_listener[0].stop()
            except Exception:
                pass
            _hotkey_listener[0] = None
        _hotkey_listener[0] = _start_hotkey()
        _update_hotkey_hint()

    _hotkey_listener[0] = _start_hotkey()

    tray_icon = None

    def hide_to_tray():
        root.withdraw()
        _tray_mode[0] = True
        ui_log(t("log_min_tray"))

    def show_from_tray(icon=None, item=None):
        _tray_mode[0] = False
        root.after(0, root.deiconify)
        root.after(0, root.lift)

    def quit_app(icon=None, item=None):
        def _q():
            service.stop()
            if tray_icon:
                try:
                    tray_icon.stop()
                except Exception:
                    pass
            root.destroy()
            os._exit(0)

        root.after(0, _q)

    def setup_tray():
        nonlocal tray_icon
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            ui_log(t("log_tray_missing"))
            return

        img = load_tray_image()
        if img is None:
            img = Image.new("RGB", (64, 64), "#1a1a2e")
            d = ImageDraw.Draw(img)
            d.ellipse((8, 8, 56, 56), fill="#4cc9f0")

        menu = pystray.Menu(
            pystray.MenuItem(t("tray_show"), show_from_tray, default=True),
            pystray.MenuItem(t("tray_start"), lambda: root.after(0, service.start)),
            pystray.MenuItem(t("tray_stop"), lambda: root.after(0, service.stop)),
            pystray.MenuItem(t("tray_exit"), quit_app),
        )
        tray_icon = pystray.Icon(
            "FewType_bridge", img, t("tray_tooltip"), menu
        )
        threading.Thread(target=tray_icon.run, daemon=True).start()
        ui_log(t("log_tray_ok"))

    def on_close():
        try:
            import pystray  # noqa: F401

            hide_to_tray()
        except ImportError:
            if messagebox.askokcancel(
                t("exit_confirm_title"), t("exit_confirm_body")
            ):
                quit_app()

    root.protocol("WM_DELETE_WINDOW", on_close)

    def bootstrap():
        ui_log(t("log_boot"))
        ui_log(t("log_workdir", d=str(APP_DIR)))
        ui_log("UI lang: %s" % _LANG)
        if not ensure_environment():
            ui_log(t("env_error_body"))
            return
        if not cfg.get("first_run_done"):
            ensure_desktop_shortcut()
            cfg["first_run_done"] = True
            save_config(cfg)
            ui_log(t("log_first"))
            messagebox.showinfo(t("welcome_title"), t("welcome_body"))
        if cfg.get("autostart"):
            set_autostart(True)
        service.start()
        setup_tray()

    root.after(200, bootstrap)
    root.mainloop()


_SINGLE_INSTANCE_MUTEX = None  # 进程存活期间持有，防 GC 导致互斥体释放


def acquire_single_instance_mutex() -> bool:
    """尝试获取单实例互斥体。

    Windows 命名 Mutex：第一个进程 CreateMutexW 成功；后续进程再创建同名
    互斥体时 GetLastError 返回 ERROR_ALREADY_EXISTS(183)，说明已有实例在运行。
    用 'Local\\' 前缀：仅当前登录会话内互斥，避免不同 Windows 用户互相干扰。
    返回 True = 本进程是唯一实例，可继续；False = 已有实例，应退出。
    """
    global _SINGLE_INSTANCE_MUTEX
    if sys.platform != "win32":
        return True  # 非 Windows 不做单实例限制
    try:
        import ctypes
        from ctypes import wintypes

        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.windll.kernel32
        CreateMutexW = kernel32.CreateMutexW
        CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        CreateMutexW.restype = wintypes.HANDLE
        GetLastError = kernel32.GetLastError
        GetLastError.restype = wintypes.DWORD

        handle = CreateMutexW(None, False, r"Local\FewType.Bridge.SingleInstance")
        if not handle:
            return True  # 创建失败不阻塞启动
        if GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _SINGLE_INSTANCE_MUTEX = handle  # 保持引用直到进程结束
        return True
    except Exception:
        return True  # 检测失败不阻塞启动


def main():
    if "--run-server" in sys.argv:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            sys.path.insert(0, str(getattr(sys, "_MEIPASS")))
        else:
            sys.path.insert(0, str(app_dir()))
        from server import main as server_main

        server_main()
        return
    if not acquire_single_instance_mutex():
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                t("already_running_title"), t("already_running_body")
            )
            root.destroy()
        except Exception:
            pass
        return
    run_gui()


if __name__ == "__main__":
    main()
