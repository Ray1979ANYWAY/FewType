# -*- coding: utf-8 -*-
"""FewType 语音输入引擎（FastAPI / WS /ws/speech-input 核心）。

从 launcher.py 的 STT 编排（4334-4970 行区域）剥离为独立服务：
- 按住说话：start() 开始录音 → stop() 结束并转写 → 润色/翻译 → 上屏事件
- 双路径：火山引擎流式（边录边传 + interim 实时上屏）／ 普通 Block（录完再转写）
- 所有状态经 on_event 回调广播：status / partial / final / commit / log / error

事件协议（服务端 → 客户端）：
    {"type": "status",   "state": "listening|transcribing|polishing|committing|idle|error", "message": str}
    {"type": "partial",  "text": str}            # 火山流式 interim
    {"type": "final",    "text": str}            # 最终结果（含润色/翻译）
    {"type": "commit",   "text": str}            # 上屏事件（由订阅方执行粘贴）
    {"type": "log",      "message": str}         # 上屏日志 / 流程日志
    {"type": "error",    "message": str}
"""
from __future__ import annotations
from log_i18n import L

import logging
import threading
import time
from typing import Callable

import stt_engine
from config_store import load_config

logger = logging.getLogger("fewtype.speech")

EventCallback = Callable[[dict], None]


class SpeechInputManager:
    """单例式语音输入管理器：同一时间仅一次录音。

    事件广播支持多个监听器（WS 连接 + 全局热键上屏并存）：
    - add_listener(cb) / remove_listener(cb)：线程安全地增删回调
    - 会话来源：start(config) 可传 _source="hotkey"（全局热键触发）或 "ws"（前端按钮）
      commit 事件会携带 source 字段，热键监听器据此决定是否后端自动上屏。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._cancel = threading.Event()
        self._recorder: stt_engine.Recorder | None = None
        self._session = None          # VolcengineStreamSession 或 None
        self._session_queue = None
        self._sender = None
        self._listeners: list[EventCallback] = []
        self._source = "ws"
        # 翻译确认等待：转写完成后暂停，等用户在前端编辑/确认原文
        self._confirm_evt = threading.Event()
        self._confirm_text: str | None = None
        self._confirm_cancel = False
        self._confirm_source: str | None = None

    # ------------------------------------------------------------ 对外接口
    def attach(self, on_event: EventCallback) -> None:
        """绑定事件回调（WS 连接建立时调用；兼容旧接口）。"""
        self.add_listener(on_event)

    def detach(self) -> None:
        """解绑所有监听器（旧接口语义：仅移除最后一次 attach 的？不，这里移除全部由调用方管理）。"""
        # 兼容旧调用：detach 移除所有（WS 断开时清理；热键监听器单独用 remove_listener 管理）
        with self._lock:
            self._listeners.clear()

    def add_listener(self, cb: EventCallback) -> None:
        with self._lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: EventCallback) -> None:
        with self._lock:
            if cb in self._listeners:
                self._listeners.remove(cb)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def start(self, config: dict) -> bool:
        """启动录音流程。config: {mode, custom_prompt, translate, target_lang, _source}。"""
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._source = config.pop("_source", "ws") if isinstance(config, dict) else "ws"
        self._cancel.clear()
        threading.Thread(target=self._run, args=(dict(config),), daemon=True).start()
        return True

    def stop(self) -> None:
        """松开按键：停止录音，进入转写收尾。"""
        rec = self._recorder
        if rec is not None:
            try:
                rec.stop_stream()
            except Exception:
                pass

    def abort(self) -> None:
        """中止当前任务：置取消位并释放资源（网络请求无法强杀，由 timeout 兜底）。"""
        with self._lock:
            if not self._active:
                return
        self._cancel.set()
        # 若正在等待翻译确认，唤醒等待线程（返回 None → 放弃）
        self._confirm_evt.set()
        rec, sess = self._recorder, self._session
        if rec is not None:
            try:
                rec.stop_stream()
            except Exception:
                pass
        if sess is not None:
            try:
                sess.abort()
            except Exception:
                pass
        self._emit({"type": "status", "state": "idle",
                    "message": "已中止当前任务"})
        self._emit({"type": "log", "message": "已中止当前任务"})
        with self._lock:
            self._active = False

    # ------------------------------------------------------------ 翻译确认
    def confirm(self, text: str, source: str, cancel: bool = False) -> bool:
        """用户在前端确认/修改原文后回调：唤醒等待中的转写线程继续翻译。

        - text: 用户确认后的原文（可能被修改）
        - source: 会话来源（hotkey / ws），必须与发起会话一致
        - cancel: True 表示放弃本次翻译上屏
        """
        with self._lock:
            if self._confirm_source != source or self._confirm_source is None:
                return False
            if cancel:
                self._confirm_cancel = True
            else:
                self._confirm_text = text
            self._confirm_evt.set()
            return True

    def _wait_confirm(self, timeout: float = 180.0) -> str | None:
        """等待用户确认原文；返回确认文本，取消/超时返回 None。"""
        with self._lock:
            self._confirm_evt.clear()
            self._confirm_text = None
            self._confirm_cancel = False
            self._confirm_source = self._source
        self._confirm_evt.wait(timeout)
        with self._lock:
            self._confirm_source = None
            if self._confirm_cancel:
                return None
            return self._confirm_text

    # ------------------------------------------------------------ 事件
    def _emit(self, ev: dict) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(ev)
            except Exception:
                pass

    def notify(self, ev: dict) -> None:
        """外部广播任意事件（如热键触发但 Provider 未配置时通知前端打开设置）。"""
        self._emit(ev)

    # ------------------------------------------------------------ 主流程
    def _run(self, cfg: dict) -> None:
        """后台线程主流程：录音 → 转写 → 润色 → 上屏事件。"""
        from provider import Provider

        mode = cfg.get("mode", "verbatim")
        translate = bool(cfg.get("translate", False))
        target_lang = cfg.get("target_lang", "简体中文")
        custom_prompt = cfg.get("custom_prompt")

        conf = load_config()
        pc = conf.get("provider", {}) or {}
        provider = Provider(
            pc.get("platform", "groq"),
            pc.get("api_key", ""),
            pc.get("asr_model", ""),
            pc.get("llm_model", ""),
            pc.get("base_url", ""),
            pc.get("llm_key", ""),
            pc.get("llm_base_url", ""),
        )
        platform = pc.get("platform", "groq")

        self._emit({"type": "status", "state": "listening",
                    "message": "聆听中…"})
        self._emit({"type": "log", "message": "开始聆听"})

        raw: str | None = None
        try:
            if platform == "volcengine":
                raw = self._run_streaming(provider, mode, custom_prompt)
            else:
                raw = self._run_block(provider, mode, custom_prompt)
        except Exception as e:  # noqa: BLE001
            logger.error(L("stt_fail", type=type(e).__name__, err=e))
            self._emit({"type": "status", "state": "error",
                        "message": f"{type(e).__name__}: {e}"})
            self._emit({"type": "error", "message": str(e)})
            self._emit({"type": "log", "message": f"失败: {e}"})
            raw = None

        if raw is None:
            with self._lock:
                self._active = False
                self._session = None
                self._session_queue = None
                self._sender = None
                self._recorder = None
            self._emit({"type": "status", "state": "idle", "message": "空闲"})
            return

        # 翻译模式：转写完成后先给用户编辑确认原文，确认后再翻译上屏
        if translate:
            self._emit({"type": "status", "state": "confirming",
                        "message": "请确认原文"})
            self._emit({"type": "confirm", "text": raw, "source": self._source,
                        "mode": mode, "target_lang": target_lang})
            confirmed = self._wait_confirm()
            if confirmed is None:
                # 用户取消 / 超时：放弃本次翻译上屏（录音状态已结束，不丢内容于输入框）
                self._emit({"type": "log",
                            "message": "已取消翻译上屏（未确认原文）"})
                with self._lock:
                    self._active = False
                    self._session = None
                    self._session_queue = None
                    self._sender = None
                    self._recorder = None
                self._emit({"type": "status", "state": "idle", "message": "空闲"})
                return
            raw = confirmed
            logger.info("[debug] 原文已确认")

        try:
            final = self._polish(provider, raw, mode, translate,
                                 target_lang, custom_prompt)
            logger.info("[debug] 翻译/润色完成")
        except Exception as e:  # noqa: BLE001
            logger.error(L("stt_fail", type=type(e).__name__, err=e))
            self._emit({"type": "status", "state": "error",
                        "message": f"{type(e).__name__}: {e}"})
            self._emit({"type": "error", "message": str(e)})
            self._emit({"type": "log", "message": f"失败: {e}"})
            final = None

        if final is not None:
            self._emit({"type": "final", "text": final})
            self._emit({"type": "commit", "text": final, "source": self._source})
            self._emit({"type": "log", "message": f"上屏 {len(final)} 字"})
            self._emit({"type": "status", "state": "committing",
                        "message": "已上屏"})
            logger.info("[debug] commit 事件已发出")
        with self._lock:
            self._active = False
            self._session = None
            self._session_queue = None
            self._sender = None
            self._recorder = None
        self._emit({"type": "status", "state": "idle", "message": "空闲"})

    # ------------------------------------------------------------ 火山流式
    def _run_streaming(self, provider, mode, custom_prompt) -> str:
        """按住边录边传：Recorder 块 → 火山 WS → interim 实时上屏。返回转写原文。"""
        import queue
        import volcengine_asr

        q: queue.Queue = queue.Queue()
        sess = volcengine_asr.VolcengineStreamSession(
            provider.api_key,
            on_partial=lambda t: self._emit(
                {"type": "partial", "text": t}),
            hotwords=stt_engine.load_hotwords(),
        )
        self._session = sess
        self._session_queue = q

        def _send_loop():
            while True:
                chunk = q.get()
                if chunk is None:
                    break
                try:
                    sess.send_audio(chunk)
                except Exception:
                    break

        sender = threading.Thread(target=_send_loop, daemon=True)
        self._sender = sender
        sender.start()

        rec = stt_engine.Recorder()
        self._recorder = rec
        rec.set_on_block(lambda d: q.put(d.tobytes()))
        try:
            sess.start()
            rec.start()
            # 等待停止（用户松开按键 / 中止）
            while rec.active and not self._cancel.is_set():
                time.sleep(0.05)
        finally:
            rec.stop_stream()
        q.put(None)
        sender.join(timeout=4)

        if self._cancel.is_set():
            return None  # type: ignore[return-value]
        self._emit({"type": "status", "state": "transcribing",
                    "message": "转写中…"})
        raw_asr = sess.finish(timeout=60)
        if not raw_asr:
            raise RuntimeError("火山识别无返回结果（可能音频过短或静音）")
        self._emit({"type": "log", "message": f"ASR 结果: {raw_asr[:60]}"})
        return raw_asr

    # ------------------------------------------------------------ Block 模式
    def _run_block(self, provider, mode, custom_prompt) -> str:
        """普通模式：录完整段 → 压缩 → Whisper 转写。返回转写原文。"""
        rec = stt_engine.Recorder()
        self._recorder = rec
        rec.start()
        while rec.active and not self._cancel.is_set():
            time.sleep(0.05)
        raw = rec.stop()
        if self._cancel.is_set():
            return None  # type: ignore[return-value]
        if raw is None:
            raise RuntimeError("未检测到语音（录音过短或静音）")
        self._emit({"type": "status", "state": "transcribing",
                    "message": "转写中…"})

        mp3 = stt_engine.compress_audio(raw, rec.sr)
        if mp3:
            audio_data = mp3
            suffix = ".mp3"
        else:
            audio_data = stt_engine.wav_bytes_from_int16(raw, rec.sr)
            suffix = ".wav"

        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.gettempdir()) / f"fewtype_stt_{int(time.time()*1000)}{suffix}"
        tmp.write_bytes(audio_data)
        try:
            raw_asr = provider.transcribe(
                str(tmp),
                prompt=stt_engine.build_whisper_prompt("zh"),
                timeout=60,
            )
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
        if not raw_asr:
            raise RuntimeError("转写无结果（可能音频过短或静音）")
        self._emit({"type": "log", "message": f"ASR 结果: {raw_asr[:60]}"})
        return raw_asr

    # ------------------------------------------------------------ 润色/翻译
    def _polish(self, provider, raw_asr: str, mode: str, translate: bool,
                target_lang: str, custom_prompt: str | None) -> str:
        if mode == "verbatim" and not translate:
            return stt_engine.process_result(provider, raw_asr, mode,
                                             translate, target_lang,
                                             custom_prompt)
        self._emit({"type": "status", "state": "polishing",
                    "message": "润色中…"})
        out = stt_engine.process_result(provider, raw_asr, mode, translate,
                                        target_lang, custom_prompt)
        return out
