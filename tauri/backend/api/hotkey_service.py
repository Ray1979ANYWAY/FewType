# -*- coding: utf-8 -*-
"""FewType 全局热键服务（FastAPI 版）。

复用 FewType-bridge/hotkey_hook.py 的低层键盘钩子（WH_KEYBOARD_LL，纯 ctypes 无依赖）：

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

logger = logging.getLogger("fewtype.hotkey")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_KEYDOWN = 0x0100
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_ESCAPE = 0x1B
WM_QUIT = 0x0012

# GetMenu：判断目标窗口是否有 Win32 菜单栏（决定是否补发 Esc 清理 Alt 激活的菜单）
user32.GetMenu.argtypes = [wintypes.HWND]
user32.GetMenu.restype = ctypes.c_void_p
# AttachThreadInput / GetClassNameW：Chromium（Chrome/Edge）解锁前台锁定时不产生 Alt 键事件
# （Alt 会激活 Chrome 自绘菜单栏；Chrome 无 Win32 菜单 → GetMenu=NULL 不会触发 ESC 清理，
#   Ctrl+V 的 V 被菜单栏吞掉 → Gemini/ChatGPT/GitHub/百度/Google 网页文本框上屏失败）
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
# 前台窗口相关（模块级声明：_restore_foreground 可能在 _find_app_window/_bring_app_to_front
# 之前被调用，若只在这两个函数里设 argtypes 则 64 位下 HWND 会按 c_int 截断）
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
# PeekMessageW：消息循环轮询（GetMessageW 会永久阻塞，导致 rebuild/stop 指令无法及时执行）
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
# MsgWaitForMultipleObjectsEx：事件驱动等待（钩子消息零延迟 + 超时兜底）
user32.MsgWaitForMultipleObjectsEx.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD
# 唤醒事件：restart()/stop() 置位 → 消息循环立即处理
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def _find_app_window() -> int | None:
    """按进程名定位 FewType 主窗口句柄。

    主窗口标题随系统语言变化（FewType 语音处理平台 / 語音處理平台 / Voice Platform），
    精确标题匹配不可靠，改为：枚举顶层窗口 → 取属于 fewtype.exe 进程的可见窗口。
    """
    import subprocess  # noqa: PLC0415

    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq fewtype.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:  # noqa: BLE001
        return None
    pids = set()
    for line in out.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    if not pids:
        return None

    found: list[int] = []
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):  # noqa: ANN001
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True  # 继续枚举

    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def _bring_app_to_front() -> None:
    """把 FewType 主窗口带到前台。

    模拟一次 Alt 按下/抬起绕过 Windows 前台锁定（与上屏前恢复焦点同一技巧，
    该路径用户已实测有效），再 SetForegroundWindow 主窗口。
    用于热键触发但 Provider 未配置时，保证设置窗在最前面，用户能看到。
    """
    try:
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
        hwnd = _find_app_window()
        if hwnd:
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        logger.debug("bring app to front failed", exc_info=True)


def _restore_foreground(hwnd: int) -> None:
    """把 hwnd 恢复为前台窗口（解锁 Windows 前台锁定）。

    全量 AttachThreadInput：不产生任何按键事件，任何应用都不会被 Alt 激活菜单栏
    ——Chrome/Firefox 自绘菜单、Electron 原生菜单、Win32 菜单应用全部免疫，
    从根上消灭「Alt 模拟吞掉 Ctrl+V」这一类问题（用户实测 Gemini/ChatGPT/GitHub/
    百度/Google 等网页文本框上屏失败，2026-09-15）。
    仅当 AttachThreadInput 不可用 / SetForegroundWindow 被拒时才 fallback 到
    Alt 模拟 + GetMenu 条件 ESC（3.1.11 已验证路径，作兜底）。
    """
    cur_tid = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
    ok = user32.SetForegroundWindow(hwnd)
    time.sleep(0.08)
    if attached:
        user32.AttachThreadInput(cur_tid, fg_tid, False)
    if ok:
        return
    # fallback：Alt 模拟解锁 + GetMenu 条件 ESC（原逻辑）
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.08)
    if user32.GetMenu(hwnd):
        user32.keybd_event(VK_ESCAPE, 0, 0, 0)
        user32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)


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
        self._wake_event = None  # Win32 事件句柄：唤醒消息循环（零延迟处理 rebuild/stop）
        self._active_combo: tuple = ()
        self._hotkey_source = "hotkey"  # 标记热键触发的会话，commit 时后端上屏
        self._prev_window = None        # 录音开始时的前台窗口（上屏前恢复焦点用）
        # 粘贴防抖：同文本短时间重复时只贴一次（双次上屏防御），并记录调用序号用于日志定位
        self._last_paste_text: str | None = None
        self._last_paste_time: float = 0.0
        self._paste_count = 0

    # ------------------------------------------------------------ 对外
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="fewtype-hotkey",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        # 唤醒消息循环立即退出（避免 join 卡满超时）
        if self._wake_event:
            kernel32.SetEvent(self._wake_event)
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def restart(self) -> None:
        """配置变更后重建钩子（指令投递到钩子线程执行）。

        若钩子线程已死（消息循环异常退出），直接重启线程，否则重启指令将永久无效。
        """
        if self._thread and self._thread.is_alive():
            self._cmd_q.put("rebuild")
            if self._wake_event:
                kernel32.SetEvent(self._wake_event)  # 立即唤醒，零延迟执行 rebuild
        else:
            logger.warning("hotkey 线程已退出，重新启动")
            self._thread = None
            self.start()

    # ------------------------------------------------------------ 钩子线程
    def _run(self) -> None:
        self._rebuild()
        # Windows 消息循环：LL 钩子回调由系统投递到此线程队列。
        # 等待策略：MsgWaitForMultipleObjectsEx（事件驱动 + 200ms 超时兜底）。
        #  - 钩子消息到达 → 立即返回处理（零延迟，按键响应与 GetMessageW 阻塞式一样快）
        #  - restart()/stop() 置位唤醒事件 → 立即处理 rebuild/退出
        #  - 200ms 超时 → 周期性兜底检查 stop_event / 指令队列
        # （不用 GetMessageW：无消息时永久阻塞，rebuild 指令无法及时执行；
        #   也不用 PeekMessage+sleep 轮询：50ms 空转让每个按键响应变肉）
        self._wake_event = kernel32.CreateEventW(None, False, False, None)
        msg = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                try:
                    cmd = self._cmd_q.get_nowait()
                    if cmd == "rebuild":
                        self._rebuild()
                except queue.Empty:
                    pass
                rc = user32.MsgWaitForMultipleObjectsEx(
                    1, ctypes.byref(wintypes.HANDLE(self._wake_event)),
                    200, 0xFF, 0)  # QS_ALLINPUT = 0xFF
                if rc == 0:  # WAIT_OBJECT_0：唤醒事件被置位
                    kernel32.ResetEvent(self._wake_event)
                    continue
                # 否则是窗口消息（WAIT_OBJECT_0 + 1）或超时（WAIT_TIMEOUT）
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                    if msg.message == WM_QUIT:
                        self._stop_event.set()
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._wake_event:
                kernel32.CloseHandle(self._wake_event)
                self._wake_event = None
        # 退出前卸载钩子
        self._teardown()

    def _rebuild(self) -> None:
        old = self._hook
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
            self._hook = None
        cfg = load_config()
        combo = (cfg.get("stt_hotkey") or "double_ctrl").strip().lower()
        mode = "double_ctrl" if combo == "double_ctrl" else "combo"
        mods, trigger = hotkey_hook.parse_combo(combo) if mode == "combo" else ([], None)
        logger.info(f"[debug] 重建热键钩子: combo={combo} mode={mode}")
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
        # Provider 未配置 API Key 时无法转写：不开始录音，通知前端弹出 Provider 设置窗口
        provider_cfg = cfg.get("provider") or {}
        if not (provider_cfg.get("api_key") or "").strip():
            logger.info(L("provider_key_missing"))
            # 用户此刻在其他应用里（热键触发），先把主窗口带到前台，
            # 否则 Provider 设置窗在后台弹出，用户看不到还在傻等
            _bring_app_to_front()
            self._speech.notify({
                "type": "open_settings",
                "target": "provider",
                "message": "provider_key_missing",
            })
            return
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
        now = time.time()
        self._paste_count += 1
        # 防抖：同一文本 1.5s 内重复的 commit 直接拦截（双次上屏防御）。
        # 正常的一次录音（几秒以上）不可能同文本短时间重复，不会误伤。
        if (text and self._last_paste_text == text
                and now - self._last_paste_time < 1.5):
            logger.info(
                f"[debug] 防抖拦截第 {self._paste_count} 次调用: "
                f"同文本 {len(text)} 字在 {now - self._last_paste_time:.2f}s 内重复")
            return
        cfg = load_config()
        if not cfg.get("stt_auto_commit", True):
            logger.info(L("autocommit_off"))
        t0 = time.time()
        try:
            pyperclip.copy(text)
        except Exception as e:  # noqa: BLE001
            logger.error(L("clipboard_fail", err=e))
            return
        clip_ms = (time.time() - t0) * 1000
        logger.info(
            f"[debug] 粘贴第 {self._paste_count} 次: 剪贴板写入 {clip_ms:.0f}ms, "
            f"{len(text)} 字")
        if not cfg.get("stt_auto_commit", True):
            return
        # 记录本次粘贴（防抖基准）：到这里才算真正要上屏
        self._last_paste_text = text
        self._last_paste_time = time.time()
        hook = self._hook
        if hook is not None:
            hook._ignore_all = True
        try:
            # 恢复录音前的焦点窗口，避免 Ctrl+V 落到 HUD 悬浮窗
            if self._prev_window:
                try:
                    # 解锁前台锁定 + 恢复焦点：
                    # - Chrome/Edge/Electron/Firefox 用 AttachThreadInput（不产生 Alt，避免激活自绘菜单栏吞掉 Ctrl+V）
                    # - 其他窗口 Alt 模拟 + GetMenu 条件 ESC（3.1.11 已验证）
                    _restore_foreground(self._prev_window)
                    time.sleep(0.02)
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
            time.sleep(0.05)  # 等剪贴板就绪
            _simulate_ctrl_v()
        finally:
            if hook is not None:
                hook._ignore_all = False
                hook._swallow_keys.clear()
