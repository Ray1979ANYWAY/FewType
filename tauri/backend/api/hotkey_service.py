# -*- coding: utf-8 -*-
"""VoxEcho 全局热键服务（FastAPI 版）。

复用 VoxEcho-bridge/hotkey_hook.py 的低层键盘钩子（WH_KEYBOARD_LL，纯 ctypes 无依赖）：

- 组合键模式（alt+win / ctrl+win / alt+x…）：全部按下 → 开始录音；任一成员松开 → 停止
- 双击 Ctrl 模式（double_ctrl）：第二下长按讲话，松开发送
- 录音完成（commit）后自动上屏：pyperclip 写剪贴板 + keybd_event 模拟 Ctrl+V
  （热键场景用户在其他应用里，必须由后端完成粘贴；上屏期间用 _ignore_all 避免钩子吞模拟键）

关键线程模型：
- LL 钩子的回调由系统投递到【安装钩子的线程】的消息队列，
  因此钩子必须在专用线程内安装，且该线程要跑 Windows 消息循环（GetMessageW 泵）。
- 配置变更需要重建钩子时，通过指令队列把"stop/start"发到钩子线程执行，避免跨线程重装导致钩子失效。
"""
from __future__ import annotations
from log_i18n import L

import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes

import pyperclip

import hotkey_hook
from config_store import load_config

logger = logging.getLogger("voxecho.hotkey")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_KEYDOWN = 0x0100
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C


def _simulate_ctrl_v() -> None:
    """模拟 Ctrl+V 自动上屏（keybd_event）。

    不卸载钩子：上屏前把钩子 _ignore_all 置位（回调放行所有键、不吞任何键），
    模拟按键结束后复位，避免钩子与模拟互相干扰导致修饰键卡住。
    """
    try:
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)   # 清理可能卡住的 ALT
        user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)   # 清理可能卡住的 Win
        user32.keybd_event(VK_RWIN, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)
    except Exception as e:  # noqa: BLE001
        logger.error(L("paste_fail", err=e))
        # 异常路径也强制释放全部修饰键，防止按键卡住
        for vk in (VK_V, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN):
            try:
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            except Exception:
                pass


class GlobalHotkeyService:
    """全局热键服务：钩子线程 + 消息循环 + 录音/上屏回调。"""

    def __init__(self, speech_manager) -> None:
        self._speech = speech_manager
        self._hook: hotkey_hook.WinHotkey | None = None
        self._thread: threading.Thread | None = None
        self._cmd_q: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._active_combo: tuple = ()
        self._hotkey_source = "hotkey"  # 标记热键触发的会话，commit 时后端上屏
        self._prev_window = None        # 录音开始时的前台窗口（上屏前恢复焦点用）

    # ------------------------------------------------------------ 对外
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="voxecho-hotkey",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def restart(self) -> None:
        """配置变更后重建钩子（指令投递到钩子线程执行）。"""
        if self._thread and self._thread.is_alive():
            self._cmd_q.put("rebuild")

    # ------------------------------------------------------------ 钩子线程
    def _run(self) -> None:
        self._rebuild()
        # Windows 消息循环：LL 钩子回调由系统投递到此线程队列
        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            # 处理指令队列（rebuild）
            try:
                cmd = self._cmd_q.get_nowait()
                if cmd == "rebuild":
                    self._rebuild()
            except queue.Empty:
                pass
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0:
                break
            if r == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # 退出前卸载钩子
        self._teardown()

    def _rebuild(self) -> None:
        old = self._hook
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        cfg = load_config()
        combo = (cfg.get("stt_hotkey") or "double_ctrl").strip().lower()
        mode = "double_ctrl" if combo == "double_ctrl" else "combo"
        mods, trigger = hotkey_hook.parse_combo(combo) if mode == "combo" else ([], None)
        try:
            hook = hotkey_hook.WinHotkey(
                mods, trigger, self._on_begin, self._on_end, mode=mode
            )
            if not hook.start():
                logger.error(L("hook_install_fail", combo=combo))
                return
        except Exception as e:  # noqa: BLE001
            logger.error(L("hook_init_fail", combo=combo, err=e))
            return
        self._hook = hook
        self._active_combo = (mode, mods, trigger)
        logger.info(L("hotkey_enabled", combo=combo, mode=mode))

    def _teardown(self) -> None:
        if self._hook is not None:
            try:
                self._hook.stop()
            except Exception:
                pass
            self._hook = None

    # ------------------------------------------------------------ 回调
    def _on_begin(self) -> None:
        """组合键全部按下 / 双击第二下按下 → 开始录音。

        模式 / 风格 / 翻译配置取自配置文件（前端选择时已同步写入）：
        - stt_mode: verbatim / fluent / formal / custom
        - stt_custom_style: 自定义风格名（mode=custom 时从 stt_styles 匹配 prompt）
        """
        if self._speech.active:
            logger.info(L("hotkey_busy"))
            return
        # 记录录音开始时的前台窗口，上屏前把焦点还给它（避免 Ctrl+V 落到 HUD）
        try:
            self._prev_window = user32.GetForegroundWindow()
        except Exception:
            self._prev_window = None
        cfg = load_config()
        mode = cfg.get("stt_mode", "verbatim")
        custom_prompt: str | None = None
        if mode == "custom":
            style_name = cfg.get("stt_custom_style") or ""
            for s in cfg.get("stt_styles") or []:
                if s.get("name") == style_name:
                    custom_prompt = (s.get("prompt") or "").strip() or None
                    break
        ok = self._speech.start({
            "mode": mode,
            "custom_prompt": custom_prompt,
            "translate": bool(cfg.get("stt_translate", False)),
            "target_lang": cfg.get("stt_target_lang", "简体中文"),
            "_source": "hotkey",
        })
        logger.info(L("hotkey_recording", mode=mode, style=cfg.get("stt_custom_style"), ok=ok))

    def _on_end(self) -> None:
        """成员键松开 → 停止录音，进入转写收尾。"""
        self._speech.stop()

    # ------------------------------------------------------------ 上屏
    def on_commit(self, text: str) -> None:
        """SpeechInputManager commit 事件（仅热键触发的会话才走到后端上屏）。

        由 speech 线程调用：写剪贴板 + 模拟 Ctrl+V（钩子 _ignore_all 保险）。
        """
        logger.info("[debug] on_commit 已收到文本")
        cfg = load_config()
        if not cfg.get("stt_auto_commit", True):
            logger.info(L("autocommit_off"))
        try:
            pyperclip.copy(text)
        except Exception as e:  # noqa: BLE001
            logger.error(L("clipboard_fail", err=e))
            return
        if not cfg.get("stt_auto_commit", True):
            return
        # 恢复录音前的焦点窗口，避免 Ctrl+V 落到 HUD 悬浮窗
        if self._prev_window:
            try:
                # ALT 按下再抬起：绕过 Windows 前台锁定限制
                user32.keybd_event(VK_MENU, 0, 0, 0)
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
                user32.SetForegroundWindow(self._prev_window)
                time.sleep(0.08)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[debug] 恢复前台窗口失败: {e}")
        # 打印粘贴前焦点窗口标题，用于验证焦点是否已回到目标窗口
        try:
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            logger.info(f"[debug] 粘贴前前台窗口: {buf.value}")
        except Exception:  # noqa: BLE001
            pass
        hook = self._hook
        if hook is not None:
            hook._ignore_all = True
        try:
            time.sleep(0.05)  # 等剪贴板就绪
            _simulate_ctrl_v()
        finally:
            if hook is not None:
                hook._ignore_all = False
                hook._swallow_keys.clear()
