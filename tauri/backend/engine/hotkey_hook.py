# -*- coding: utf-8 -*-
"""Windows 低层键盘钩子热键（WH_KEYBOARD_LL）

替代 pynput Listener 的原因：pynput 回调返回 False 会【停止整个监听器】
（之前吞键 return False → Listener 死后 on_release 永不触发 → rec 卡 45s 超时才收尾）。
LL 钩子 return 1 只吞掉该按键本身，监听持续有效。

行为：
- 组合键全部按下 → on_begin()（一次）
- 任一组合键成员松开 → on_end()（一次）
- 组合激活/录音期间，成员键的按下+释放都被吞掉（系统不知道组合键被按过，防 Win 键弹系统 UI）
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# 左右键 VK → 通用键 VK（LL 钩子对左右修饰键分别报 0xA0~0xA5）
_NORM_VK = {
    0xA0: 0x10, 0xA1: 0x10,  # shift 左右
    0xA2: 0x11, 0xA3: 0x11,  # ctrl 左右
    0xA4: 0x12, 0xA5: 0x12,  # alt 左右
    0x5B: 0x5B, 0x5C: 0x5B,  # win 左右
}
_VK_NAME = {"ctrl": 0x11, "alt": 0x12, "win": 0x5B, "shift": 0x10}

# ---- 双击 Ctrl 模式常量 ----
DOUBLE_CTRL_TIMEOUT = 0.3  # 300ms 双击时间窗口
KEYEVENTF_KEYUP = 0x0002   # keybd_event 释放标志
VK_CONTROL = 0x11           # Ctrl 通用键码（左右 Ctrl 归一化后都是 0x11）

# keybd_event 用于结束双击录音后补发 Ctrl 松开事件，
# 避免系统因第一次 Ctrl 按下已传递、第二次松开被吞而认为 Ctrl 一直按着
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
user32.keybd_event.restype = None


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


_HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# ctypes 必须显式声明参数类型：否则 64 位句柄按 32 位 int 传递会被截断 → SetWindowsHookExW 返回 NULL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HMODULE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM


def parse_combo(combo: str):
    """'alt+win' / 'ctrl+win' / 'alt+x' → (mods:list[str], trigger:str|None)"""
    parts = [p.strip().lower() for p in str(combo).split("+") if p.strip()]
    if not parts:
        return ["ctrl", "win"], None
    mod_keys = {"ctrl", "alt", "win", "shift"}
    mods = [p for p in parts if p in mod_keys]
    rest = [p for p in parts if p not in mod_keys]
    return mods, (rest[0] if rest else None)


class WinHotkey:
    """低层键盘钩子热键。回调在安装钩子的线程（Tk 主线程）消息循环中执行。"""

    def __init__(self, mods, trigger, on_begin, on_end, mode="combo"):
        """
        mode:
          - "combo": 组合键模式（默认），如 alt+win、ctrl+win
          - "double_ctrl": 双击 Ctrl 模式，第二下长按讲话，松开发送
        """
        self.mode = mode
        self._member_vks = set(_VK_NAME[m] for m in mods)
        if trigger:
            t = trigger.lower()
            vk = _VK_NAME.get(t)
            if vk is None and len(t) == 1 and t.isalnum():
                vk = ord(t.upper())
            if vk:
                self._member_vks.add(vk)
        self.on_begin = on_begin
        self.on_end = on_end
        self._pressed = {}
        self.rec = False
        self._swallow_keys = set()  # 本次录音需吞掉 release 的成员键（防止两键同松时第二个 release 漏吞→弹开始菜单）
        self._ignore_all = False    # 模拟 Ctrl+V 自动上屏期间临时忽略所有按键事件，避免钩子与模拟互相干扰
        self._pending = []          # 组合键模式：被吞住等待组合判定的按键序列 [(vk, down), ...]
        self._pending_start = 0     # 起始被吞的成员键 vk（其松开 → 触发按序补发）
        self._hook = None
        self._proc = None

        # ---- 双击 Ctrl 状态机变量 ----
        # 状态：idle → first_held（第一次按下，未松开）→ first_released（第一次已松开，等第二次）→ recording
        # 关键：第一次必须先松开，才能接受第二次按下。否则按住 Ctrl 不放的键盘重复事件会被误判为双击。
        self._dc_first_pressed = False    # 第一次 Ctrl 已按下
        self._dc_first_released = False   # 第一次 Ctrl 是否已松开（松开后才能接受第二次按下）
        self._dc_first_press_time = 0.0   # 第一次按下的时间戳
        self._dc_release_time = 0.0       # 第一次松开的时间戳（用于 50ms 防抖校验）
        self._dc_other_key_intervened = False  # 第一次和第二次之间按了其他键 → 取消双击
        self._dc_recording = False         # 第二次按下后，录音中

    def _combo_down(self):
        return all(self._pressed.get(vk, False) for vk in self._member_vks)

    def _on_event(self, vk_raw, down):
        """状态机：返回 True 表示该键应被吞掉。测试可直接调用。
        根据 self.mode 分发到组合键逻辑或双击 Ctrl 逻辑。"""
        if self.mode == "double_ctrl":
            return self._on_event_double_ctrl(vk_raw, down)
        return self._on_event_combo(vk_raw, down)

    def _on_event_combo(self, vk_raw, down):
        """组合键模式状态机（防 Win 键先按弹开始菜单版）。

        问题：旧逻辑只吞"组合成立之后"的成员键。若用户先按 Win（或 Win 事件先到），
        Win 的按下已传给系统 → 开始菜单弹出 → 焦点落在搜索栏，录音/上屏全跑偏。

        新逻辑：成员键按下时【一律先吞住】并进入 pending；组合成立 → 触发录音，
        全部吞掉（系统对组合键无感知）；组合未成立（单独按 Win / Win+E 等原生组合）
        → 起始键松开时【按序补发】被吞的按键，系统行为与原生一致。
        """
        vk = _NORM_VK.get(vk_raw, vk_raw)
        is_member = vk in self._member_vks

        # ---- 录音中（rec=True）：成员键一律直接吞掉，不污染 pending / 状态机 ----
        # 关键：按住热键说话超过键盘 auto-repeat 延迟（约 500ms）时，Windows 会重复投递
        # 成员键的 WM_KEYDOWN。旧逻辑让 repeat 进入 pending，松开时 pending 因 rec=True
        # 永不补发/清理 → 之后所有按键被钩子吞掉（"键盘粘滞"，只能杀进程+重启恢复）。
        if self.rec:
            if not is_member:
                return False  # 非成员键照常放行
            if down:
                return True   # auto-repeat / 重按：吞掉，保持录音
            self._pressed[vk] = False
            if not self._combo_down():
                # 组合不再完整 → 结束录音（任一成员键松开即停止）
                self.rec = False
                self._swallow_keys.clear()
                try:
                    self.on_end()
                except Exception:
                    pass
            return True

        # ---- pending：成员键已被吞住，等待组合判定 ----
        if self._pending:
            if down:
                self._pressed[vk] = True
                if self._combo_down() and not self.rec:
                    # 组合成立 → 触发录音，pending 全部吞掉
                    self.rec = True
                    self._swallow_keys = set(self._member_vks)
                    self._pending = []
                    try:
                        self.on_begin()
                    except Exception:
                        pass
                else:
                    self._pending.append((vk, down))
            else:
                self._pressed[vk] = False
                self._pending.append((vk, down))
                if vk == self._pending_start and not self.rec:
                    # 起始成员键已松开且组合从未成立 → 按序补发被吞的按键
                    self._replay_pending()
                    self._pending = []
            return True  # pending 期间一律吞

        if down:
            self._pressed[vk] = True
            if is_member:
                # 成员键按下：吞住并进入 pending
                self._pending = [(vk, down)]
                self._pending_start = vk
                if self._combo_down() and not self.rec:
                    # 其他成员键已按住（如 Ctrl 已按），组合直接成立
                    self.rec = True
                    self._swallow_keys = set(self._member_vks)
                    self._pending = []
                    try:
                        self.on_begin()
                    except Exception:
                        pass
                return True
            return False  # 非成员键正常放行

        # ---- up（无 pending）----
        self._pressed[vk] = False
        was_rec = self.rec
        if is_member and not self._combo_down() and was_rec:
            # 最后一个成员键松开 → 结束录音
            self.rec = False
            self._swallow_keys.clear()
            try:
                self.on_end()
            except Exception:
                pass
            return True  # 吞掉结束松开的 up
        return False

    def _replay_pending(self):
        """按序补发 pending 中被吞的按键（keybd_event 注入，钩子对注入事件放行不递归）。

        例：单独按 Win → 补发 [Win down, Win up]，开始菜单正常弹出。
        Win+E → 补发 [Win down, E down, E up, Win up]，Win+E 原生行为保持。
        """
        try:
            for vk, is_down in self._pending:
                user32.keybd_event(vk, 0, 0 if is_down else KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

    def _on_event_double_ctrl(self, vk_raw, down):
        """双击 Ctrl 模式状态机（严格校验版）：第二下长按讲话，松开自动上屏。

        【严格校验规则】
        第二次按下必须同时满足：
        1. 距离第一次松开的时间 > 50ms（排除键盘硬件按键抖动/重复码）
        2. 距离第一次按下的总时间 < 350ms（双击窗口）
        3. 中间没有其他非修饰键干预

        【状态机】
        idle → first_held（第一次按下，未松开）
             → first_released（第一次已松开，等第二次）
             → recording（第二次按下，录音中）
             → idle（第二次松开，结束）

        【吞键策略】
        - 第一次按下/松开：不吞（保证 Ctrl+C/V 等正常组合键完全不受影响）
        - 第二次按下/松开：吞（系统不知道第二次按下，避免触发 Ctrl 相关行为）
        - 其他键：不吞
        - 结束时补发 Ctrl 松开事件（第一次按下已传递给系统，第二次松开被吞，需清理状态）
        """
        vk = _NORM_VK.get(vk_raw, vk_raw)
        is_ctrl = (vk == VK_CONTROL)
        now = time.time()

        if is_ctrl and down:
            # ---- Ctrl 按下 ----
            if not self._dc_first_pressed:
                # 【第一次真实按下】
                self._dc_first_pressed = True
                self._dc_first_released = False
                self._dc_first_press_time = now
                self._dc_release_time = 0.0
                self._dc_other_key_intervened = False
                return False

            elif not self._dc_first_released:
                # 【按住不放的重复事件】第一次按下后还没松开，又收到 Ctrl 按下
                # 这是 Windows 键盘自动重复，直接忽略，不更新时间戳
                return False

            else:
                # 【第二次按下候选】必须满足严格校验：
                # 1. 距离第一次松开 > 50ms（排除硬件抖动）
                # 2. 距离第一次按下 < 350ms（双击窗口）
                # 3. 中间没有其他键干预
                time_since_first_press = now - self._dc_first_press_time
                time_since_release = now - self._dc_release_time

                if (0.05 < time_since_release) and (time_since_first_press < 0.35) and not self._dc_other_key_intervened:
                    # 确认是真正的第二次双击长按！
                    self._dc_recording = True
                    self.rec = True
                    try:
                        self.on_begin()
                    except Exception:
                        pass
                    return True  # 吞掉第二次按下
                else:
                    # 不满足双击条件（太快/太慢/中间有其他键），重置为新的"第一次按下"
                    self._dc_first_pressed = True
                    self._dc_first_released = False
                    self._dc_first_press_time = now
                    self._dc_release_time = 0.0
                    self._dc_other_key_intervened = False
                    return False

        elif is_ctrl and not down:
            # ---- Ctrl 松开 ----
            if self._dc_recording:
                # 【第二次松开】结束录音
                self._dc_recording = False
                self.rec = False
                self._dc_first_pressed = False
                self._dc_first_released = False
                try:
                    self.on_end()
                except Exception:
                    pass
                # 补发 Ctrl 松开事件：第一次按下已传递给系统，第二次松开被吞，
                # 系统会认为 Ctrl 一直按着，补发一次松开清理状态
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                return True  # 吞掉第二次松开

            elif self._dc_first_pressed and not self._dc_first_released:
                # 【第一次真实松开】记录松开的时间戳
                self._dc_first_released = True
                self._dc_release_time = now  # 关键：记录第一次松开的具体时间
                return False  # 不吞

            else:
                self._dc_first_pressed = False
                self._dc_first_released = False
                return False

        else:
            # ---- 其他键 ----
            # 录音期间（含双击候选期）按 Win 键：吞掉，防止开始菜单弹出抢焦点。
            # 焦点一丢，上屏就贴到搜索栏去了（用户实测 Cortana 窗口）。
            # 非录音期正常放行，Win+E / Win+D 等原生快捷键完全不受影响。
            if vk in (0x5B, 0x5C):  # Win 左右
                if self._dc_first_pressed or self._dc_recording:
                    return True  # 吞掉 Win 按下/松开，系统感知不到 Win 被按过
                return False
            # 忽略修饰键本身的切换码（shift/ctrl/alt），防止修饰键干扰双击检测
            # 只有真正的字符键/功能键才取消双击资格
            if vk not in (0x10, 0x11, 0x12):
                if self._dc_first_pressed and not self._dc_recording:
                    self._dc_other_key_intervened = True
            return False  # 其他键一律不吞


    def _callback(self, nCode, wParam, lParam):
        if nCode >= 0:
            try:
                # 模拟 Ctrl+V 自动上屏期间临时忽略所有事件，避免钩子与模拟互相干扰导致修饰键卡住
                if self._ignore_all:
                    return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)
                kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                # 忽略注入的按键（我们自己模拟的 Ctrl+V 自动上屏），避免钩子与模拟互相干扰
                if kb.flags & 0x10:  # LLKHF_INJECTED
                    return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)
                down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                up = wParam in (WM_KEYUP, WM_SYSKEYUP)
                if down or up:
                    if self._on_event(kb.vkCode, down):
                        return 1
            except Exception:
                pass
        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def start(self) -> bool:
        if self._hook:
            return True
        self._proc = _HOOKPROC(self._callback)  # 必须持有引用防 GC
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        return bool(self._hook)

    def stop(self):
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        # 卸载钩子前补发所有被吞未放的按键，避免系统残留修饰键按下状态（键盘卡死）
        try:
            # 1) pending 中被吞的按键按序补发（保持原生语义）
            if self._pending:
                self._replay_pending()
            # 2) 仍处于按下状态的成员键补发 keyup（孤儿 keyup 无害，可清理任何残留）
            for vk, held in list(self._pressed.items()):
                if held and vk in self._member_vks:
                    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            # 3) 双击 Ctrl 模式：第一次 Ctrl 按下已传给系统，退出时补发松开清理状态
            if self._dc_first_pressed or self._dc_recording:
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass
        self._proc = None
        self._pressed.clear()
        self._swallow_keys.clear()
        self._pending = []
        self._pending_start = 0
        self.rec = False
        # 重置双击 Ctrl 状态机
        self._dc_first_pressed = False
        self._dc_first_released = False
        self._dc_first_press_time = 0.0
        self._dc_release_time = 0.0
        self._dc_other_key_intervened = False
        self._dc_recording = False
