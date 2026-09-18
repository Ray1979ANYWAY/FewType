# -*- coding: utf-8 -*-
"""录音时压低其他播放音频音量（audio ducking）。

遍历默认渲染设备的所有音频会话，对「正在播放（Active）且非本进程」的会话，
把音量压到其当前值的 50%（相对减半，不是绝对值），录音结束时恢复原音量。

纯 ctypes 调 Windows Core Audio API（IMMDeviceEnumerator → IAudioSessionManager2
→ IAudioSessionEnumerator → ISimpleAudioVolume），无第三方依赖，与 hotkey_hook.py
同风格。调用方：hotkey_service._on_begin / _on_end（钩子线程内调用）。
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger("fewtype.audio_duck")

DUCK_RATIO = 0.5  # 压到会话当前音量的 50%

ole32 = ctypes.windll.ole32

# ---------------------------------------------------------------- COM GUID
class GUID(ctypes.Structure):
    """COM GUID：从 "{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}" 构造。"""
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, s: str) -> None:
        super().__init__()
        h = s.replace("{", "").replace("}", "").replace("-", "")
        self.Data1 = int(h[0:8], 16)
        self.Data2 = int(h[8:12], 16)
        self.Data3 = int(h[12:16], 16)
        # Data4 是 8 字节：h[16:24] 只有 4 字节（历史 bug：尾部被截成 00000000
        # → CoCreateInstance 报 REGDB_E_CLASSNOTREG），必须取 h[16:32]
        self.Data4 = (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:32]))


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioSessionManager2 = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
IID_IAudioSessionControl2 = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
IID_ISimpleAudioVolume = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")

CLSCTX_ALL = 0x17
ERender = 0
EConsole = 0
AudioSessionStateActive = 1
COINIT_APARTMENTTHREADED = 0x0

HRESULT = ctypes.c_long  # wintypes.HRESULT 需 Python 3.11+；HRESULT 本质是 32 位 LONG
LPCGUID = ctypes.POINTER(GUID)
LPVOID_P = ctypes.POINTER(ctypes.c_void_p)

# ---------------------------------------------------------------- vtable
# 只声明用到的接口方法；vtable 索引按 Windows SDK 头文件（IUnknown 占 0-2）
_QI = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, LPCGUID, LPVOID_P)
_ADDREF = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)
_RELEASE = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)
_GET_DEF_ENDPOINT = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, LPVOID_P)
_ACTIVATE = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, LPCGUID, wintypes.DWORD, ctypes.c_void_p, LPVOID_P)
_GET_ENUM = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, LPVOID_P)
_GET_COUNT = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
_GET_SESSION = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_int, LPVOID_P)
_GET_STATE = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
_GET_PID = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD))
_SET_MASTER = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_float, LPCGUID)
_GET_MASTER = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float))


class _IUnknownVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", _QI), ("AddRef", _ADDREF), ("Release", _RELEASE)]


class _IMMDeviceEnumeratorVtbl(_IUnknownVtbl):
    _fields_ = [("EnumAudioEndpoints", ctypes.c_void_p), ("GetDefaultAudioEndpoint", _GET_DEF_ENDPOINT)]


class _IMMDeviceVtbl(_IUnknownVtbl):
    _fields_ = [("Activate", _ACTIVATE)]


class _IAudioSessionManager2Vtbl(_IUnknownVtbl):
    _fields_ = [
        ("GetAudioSessionControl", ctypes.c_void_p),
        ("GetSimpleAudioVolume", ctypes.c_void_p),
        ("GetSessionEnumerator", _GET_ENUM),
    ]


class _IAudioSessionEnumeratorVtbl(_IUnknownVtbl):
    _fields_ = [("GetCount", _GET_COUNT), ("GetSession", _GET_SESSION)]


class _IAudioSessionControlVtbl(_IUnknownVtbl):
    """基接口 IAudioSessionControl（GetSession 返回的就是它，vtable 仅 12 项）。"""
    _fields_ = [
        ("GetState", _GET_STATE),
        ("GetDisplayName", ctypes.c_void_p),
        ("SetDisplayName", ctypes.c_void_p),
        ("GetIconPath", ctypes.c_void_p),
        ("SetIconPath", ctypes.c_void_p),
        ("GetGroupingParam", ctypes.c_void_p),
        ("SetGroupingParam", ctypes.c_void_p),
        ("RegisterAudioSessionNotification", ctypes.c_void_p),
        ("UnregisterAudioSessionNotification", ctypes.c_void_p),
    ]


class _IAudioSessionControl2Vtbl(_IAudioSessionControlVtbl):
    """IAudioSessionControl2 = Control + 4 个方法（GetProcessId 在索引 14）。"""
    _fields_ = [
        ("GetSessionIdentifier", ctypes.c_void_p),
        ("GetSessionInstanceIdentifier", ctypes.c_void_p),
        ("GetProcessId", _GET_PID),
        ("IsSystemSoundsSession", ctypes.c_void_p),
        ("SetDuckingPreference", ctypes.c_void_p),
    ]


class _ISimpleAudioVolumeVtbl(_IUnknownVtbl):
    _fields_ = [
        ("SetMasterVolume", _SET_MASTER),
        ("GetMasterVolume", _GET_MASTER),
        ("SetMute", ctypes.c_void_p),
        ("GetMute", ctypes.c_void_p),
    ]


# ---------------------------------------------------------------- 接口包装
class _IMMDeviceEnumerator(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IMMDeviceEnumeratorVtbl))]


class _IMMDevice(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IMMDeviceVtbl))]


class _IAudioSessionManager2(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IAudioSessionManager2Vtbl))]


class _IAudioSessionEnumerator(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IAudioSessionEnumeratorVtbl))]


class _IAudioSessionControl(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IAudioSessionControlVtbl))]


class _IAudioSessionControl2(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IAudioSessionControl2Vtbl))]


class _ISimpleAudioVolume(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_ISimpleAudioVolumeVtbl))]


# ---------------------------------------------------------------- ducking
class AudioDucker:
    """录音开始压低其他会话音量，结束恢复。

    线程模型：在钩子线程（消息循环线程）内调用 start()/stop()；
    COM 在此线程首次使用时 CoInitializeEx，进程生命周期内不复用清理。
    """

    def __init__(self, ratio: float = DUCK_RATIO) -> None:
        self._ratio = float(ratio)
        self._saved: list[tuple[_ISimpleAudioVolume, float]] = []
        self._active = False
        self._com_ready = False

    # ---------------------------------------------------------- 对外
    def start(self) -> None:
        """压低所有正在播放的其他会话音量（相对减半），记录原值。"""
        if self._active:
            return
        try:
            self._ensure_com()
            saved = self._collect_and_duck()
        except Exception as e:  # noqa: BLE001
            logger.warning("audio duck start failed: %s", e)
            return
        if saved:
            self._saved = saved
            self._active = True
            logger.info("audio duck: %d 个会话压低到 %d%%", len(saved), int(self._ratio * 100))

    def stop(self) -> None:
        """恢复被压低的会话音量。"""
        if not self._active:
            return
        ok = 0
        for vol, orig in self._saved:
            try:
                vol.contents.lpVtbl.contents.SetMasterVolume(vol, orig, None)
                ok += 1
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    vol.contents.lpVtbl.contents.Release(vol)
                except Exception:
                    pass
        logger.info("audio duck: 恢复 %d/%d 个会话", ok, len(self._saved))
        self._saved = []
        self._active = False

    # ---------------------------------------------------------- 内部
    def _ensure_com(self) -> None:
        if not self._com_ready:
            ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            self._com_ready = True

    def _collect_and_duck(self) -> list[tuple[_ISimpleAudioVolume, float]]:
        """枚举默认渲染设备的所有音频会话，压低 Active 且非本进程的会话。"""
        saved: list[tuple[_ISimpleAudioVolume, float]] = []

        ole32.CoCreateInstance.argtypes = [LPCGUID, ctypes.c_void_p, wintypes.DWORD, LPCGUID, LPVOID_P]
        ole32.CoCreateInstance.restype = HRESULT
        enum_p = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
            ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum_p))
        if hr < 0 or not enum_p:
            raise OSError(f"CoCreateInstance MMDeviceEnumerator failed: 0x{hr & 0xFFFFFFFF:08X}")
        enum_dev = ctypes.cast(enum_p, ctypes.POINTER(_IMMDeviceEnumerator))

        device_p = ctypes.c_void_p()
        try:
            hr = enum_dev.contents.lpVtbl.contents.GetDefaultAudioEndpoint(enum_dev, ERender, EConsole, ctypes.byref(device_p))
            if hr < 0 or not device_p:
                raise OSError(f"GetDefaultAudioEndpoint failed: 0x{hr & 0xFFFFFFFF:08X}")
            device = ctypes.cast(device_p, ctypes.POINTER(_IMMDevice))

            mgr_p = ctypes.c_void_p()
            try:
                hr = device.contents.lpVtbl.contents.Activate(
                    device, ctypes.byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, ctypes.byref(mgr_p))
                if hr < 0 or not mgr_p:
                    raise OSError(f"Activate IAudioSessionManager2 failed: 0x{hr & 0xFFFFFFFF:08X}")
                mgr = ctypes.cast(mgr_p, ctypes.POINTER(_IAudioSessionManager2))

                enum_p2 = ctypes.c_void_p()
                try:
                    hr = mgr.contents.lpVtbl.contents.GetSessionEnumerator(mgr, ctypes.byref(enum_p2))
                    if hr < 0 or not enum_p2:
                        raise OSError(f"GetSessionEnumerator failed: 0x{hr & 0xFFFFFFFF:08X}")
                    s_enum = ctypes.cast(enum_p2, ctypes.POINTER(_IAudioSessionEnumerator))

                    count = ctypes.c_int(0)
                    hr = s_enum.contents.lpVtbl.contents.GetCount(s_enum, ctypes.byref(count))
                    if hr < 0:
                        raise OSError(f"GetCount failed: 0x{hr & 0xFFFFFFFF:08X}")
                    my_pid = os.getpid()
                    for i in range(count.value):
                        try:
                            item = self._handle_session(s_enum, i, my_pid)
                            if item:
                                saved.append(item)
                        except Exception:  # noqa: BLE001
                            continue
                finally:
                    try:
                        s_enum.contents.lpVtbl.contents.Release(s_enum)
                    except Exception:
                        pass
            finally:
                try:
                    mgr.contents.lpVtbl.contents.Release(mgr)
                except Exception:
                    pass
        finally:
            try:
                enum_dev.contents.lpVtbl.contents.Release(enum_dev)
            except Exception:
                pass
        return saved

    def _handle_session(self, s_enum, index: int, my_pid: int):
        """处理单个会话：Active 且非本进程 → 压低 50% 并记录原值。

        注意 COM 层级：GetSession 返回 IAudioSessionControl（基接口，vtable 12 项），
        拿进程 ID 必须先 QueryInterface 到 IAudioSessionControl2，不能直接越界调用。
        """
        ctrl_p = ctypes.c_void_p()
        hr = s_enum.contents.lpVtbl.contents.GetSession(s_enum, index, ctypes.byref(ctrl_p))
        if hr < 0 or not ctrl_p:
            return None
        ctrl = ctypes.cast(ctrl_p, ctypes.POINTER(_IAudioSessionControl))
        ctrl2: _IAudioSessionControl2 | None = None
        try:
            # 基接口 → IAudioSessionControl2（拿 GetProcessId）
            ctrl2_p = ctypes.c_void_p()
            hr = ctrl.contents.lpVtbl.contents.QueryInterface(
                ctrl, ctypes.byref(IID_IAudioSessionControl2), ctypes.byref(ctrl2_p))
            if hr < 0 or not ctrl2_p:
                return None
            ctrl2 = ctypes.cast(ctrl2_p, ctypes.POINTER(_IAudioSessionControl2))

            # 排除自己进程（fewtype-backend 的 TTS/提示音会话）
            pid = wintypes.DWORD(0)
            pid_ok = ctrl2.contents.lpVtbl.contents.GetProcessId(ctrl2, ctypes.byref(pid)) >= 0
            if pid_ok and pid.value == my_pid:
                return None

            # 只压正在播放（Active）的会话
            state = ctypes.c_int(0)
            if ctrl2.contents.lpVtbl.contents.GetState(ctrl2, ctypes.byref(state)) < 0:
                return None
            if state.value != AudioSessionStateActive:
                return None

            # Control2 → ISimpleAudioVolume 并压低
            vol_p = ctypes.c_void_p()
            hr = ctrl2.contents.lpVtbl.contents.QueryInterface(
                ctrl2, ctypes.byref(IID_ISimpleAudioVolume), ctypes.byref(vol_p))
            if hr < 0 or not vol_p:
                return None
            vol = ctypes.cast(vol_p, ctypes.POINTER(_ISimpleAudioVolume))
            try:
                level = ctypes.c_float(0.0)
                if vol.contents.lpVtbl.contents.GetMasterVolume(vol, ctypes.byref(level)) < 0:
                    return None
                if level.value <= 0.0:  # 已静音/零音量，无需压
                    return None
                vol.contents.lpVtbl.contents.SetMasterVolume(vol, level.value * self._ratio, None)
                return (vol, float(level.value))  # vol 的 Release 交给 stop()
            except Exception:
                try:
                    vol.contents.lpVtbl.contents.Release(vol)
                except Exception:
                    pass
                return None
        finally:
            if ctrl2 is not None:
                try:
                    ctrl2.contents.lpVtbl.contents.Release(ctrl2)
                except Exception:
                    pass
            try:
                ctrl.contents.lpVtbl.contents.Release(ctrl)
            except Exception:
                pass


ducker = AudioDucker()
