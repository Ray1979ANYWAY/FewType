# -*- coding: utf-8 -*-
"""火山引擎 豆包流式语音识别 2.0 客户端（新版控制台鉴权：X-Api-Key）。

协议参考：https://www.volcengine.com/docs/6561/1354869（完整协议）
- 端点：wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async（双向流式优化版）
- 请求头：X-Api-Key / X-Api-Resource-Id / X-Api-Request-Id / X-Api-Sequence: -1
- 帧格式（双向）：[4B Header][Payload size 4B 大端][Payload]
  Header byte0 = (Protocol version=1 << 4) | (Header size=1 << 0)  → 0x11
         byte1 = (Message type << 4) | (flags)
         byte2 = (Serialization << 4) | (Compression)
         byte3 = 0x00
  - full client request：type=0b0001, ser=0b0001(JSON), payload=配置 JSON
  - audio only request ：type=0b0010, flags=0b0010=最后一包, payload=裸 PCM
  - full server response：type=0b1001, 帧 = Header+Sequence(4B)+Size(4B)+JSON
  - error frame：type=0b1111, 帧 = Header+Code(4B)+Size(4B)+message
- interim：show_utterances + enable_nonstream → 分句 definite=true 为最终确定
"""
import json
import struct
import threading
import time
import uuid
from pathlib import Path

import websocket

WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
RESOURCE_ID_2_0 = "volc.seedasr.sauc.duration"   # 豆包流式语音识别模型 2.0（小时版）
RESOURCE_ID_1_0 = "volc.bigasr.sauc.duration"    # 豆包流式语音识别模型 1.0（小时版）

_HEADER0 = (0b0001 << 4) | 0b0001  # version=1, header_size=1


def _frame(msg_type: int, flags: int, serial: int, payload: bytes) -> bytes:
    hdr = bytes([_HEADER0, (msg_type << 4) | flags, (serial << 4) | 0b0000, 0x00])
    return hdr + struct.pack(">I", len(payload)) + payload


def _split_server_frame(msg: bytes):
    """把一条 WS 消息拆成若干服务端帧，返回 [(mtype, flags, payload), ...]。

    坑：服务端首帧（flags=0，建连确认/log_id）是 8 字节头（Header+Size，无 Sequence）；
    结果帧（flags=1/3）是 12 字节头（Header+Sequence+Size）。文档只画了 12 字节头。
    必须动态识别，否则首帧 size 读错会导致后续所有帧错位。"""
    frames = []
    off, n = 0, len(msg)
    while off + 4 <= n:
        mtype = msg[off + 1] >> 4
        flags = msg[off + 1] & 0x0F
        if mtype == 0b1001 and flags == 0:  # 首帧确认：8 字节头
            if off + 8 > n:
                break
            size = struct.unpack(">I", msg[off + 4:off + 8])[0]
            frames.append((mtype, flags, msg[off + 8:off + 8 + size]))
            off += 8 + size
        elif mtype in (0b1001, 0b1111):  # 结果帧/错误帧：12 字节头
            if off + 12 > n:
                break
            size = struct.unpack(">I", msg[off + 8:off + 12])[0]
            frames.append((mtype, flags, msg[off + 12:off + 12 + size]))
            off += 12 + size
        else:
            frames.append((mtype, flags, msg[off:]))
            break
    return frames


def _strip_wav_header(data: bytes) -> bytes:
    """wav 文件 → 裸 pcm_s16le；非 wav 原样返回。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        off = 12
        while off + 8 <= len(data):
            cid = data[off:off + 4]
            csize = struct.unpack("<I", data[off + 4:off + 8])[0]
            if cid == b"data":
                return data[off + 8: off + 8 + csize]
            off += 8 + csize + (csize & 1)
    return data


class VolcengineError(Exception):
    pass


class VolcengineStreamSession:
    """流式会话：start() 建连 → send_audio() 边录边传 → finish() 收尾取最终结果。
    适合"按住说话，边说边上字"：接收线程实时解析 interim/final，经 on_partial 回调。"""

    def __init__(self, api_key: str, on_partial=None, resource_id: str = RESOURCE_ID_2_0,
                 rate: int = 16000, seg_ms: int = 200, timeout: int = 60,
                 hotwords: list[str] | None = None):
        self._api_key = api_key
        self._on_partial = on_partial
        self._resource_id = resource_id
        self._rate = rate
        self._seg_ms = seg_ms
        self._timeout = timeout
        self._hotwords = hotwords
        self._ws = None
        self._definite_parts: list[str] = []
        self._last_full = ""
        self._errors: list[Exception] = []
        self._done = threading.Event()
        self._recv_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()

    # ------------------------------------------------------------ 建连/发送
    def start(self):
        if self._ws is not None:
            return
        payload = {
            "audio": {"format": "pcm", "codec": "raw",
                      "rate": self._rate, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": True,
                "result_type": "single",
            },
        }
        if self._hotwords:
            payload["request"]["corpus"] = {
                "hotwords": [{"word": w} for w in self._hotwords[:50]],
            }
        headers = [
            f"X-Api-Key: {self._api_key}",
            f"X-Api-Resource-Id: {self._resource_id}",
            f"X-Api-Request-Id: {str(uuid.uuid4())}",
            "X-Api-Sequence: -1",
        ]
        self._ws = websocket.create_connection(
            WS_URL, header=headers, timeout=self._timeout, enable_multithread=True,
        )
        self._ws.send_binary(_frame(
            0b0001, 0b0000, 0b0001,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ))
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    def send_audio(self, pcm: bytes):
        with self._send_lock:
            if self._ws is not None:
                self._ws.send_binary(_frame(0b0010, 0b0000, 0b0000, pcm))

    def finish(self, timeout: float = 30.0) -> str:
        """发最后包（flags=0b0010）并等待最终结果；返回完整文本。"""
        if self._ws is not None:
            with self._send_lock:
                try:
                    self._ws.send_binary(_frame(0b0010, 0b0010, 0b0000, b""))
                except Exception:
                    pass
        self._done.wait(timeout)
        if self._errors:
            raise self._errors[0]
        if self._definite_parts:
            return "".join(self._definite_parts)
        if self._last_full:
            return self._last_full
        raise VolcengineError("火山识别无返回结果（可能音频过短或静音）")

    def abort(self):
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self._done.set()

    # ------------------------------------------------------------ 接收
    def _recv_loop(self):
        ws = self._ws
        deadline = time.time() + self._timeout
        try:
            while time.time() < deadline:
                ws.settimeout(max(1.0, deadline - time.time()))
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except websocket.WebSocketConnectionClosedException:
                    break
                if not message or not isinstance(message, bytes):
                    continue
                done = False
                for mtype, flags, payload in _split_server_frame(message):
                    if self._handle_frame(mtype, flags, payload):
                        done = True
                if done:
                    break
        except Exception as e:  # noqa: BLE001
            if not isinstance(e, websocket.WebSocketConnectionClosedException):
                self._errors.append(e)
        finally:
            self._done.set()

    def _handle_frame(self, mtype: int, flags: int, payload: bytes) -> bool:
        if mtype == 0b1111:  # error frame
            code = struct.unpack(">I", payload[:4])[0] if len(payload) >= 4 else -1
            msg = payload[8:].decode("utf-8", "replace") if len(payload) >= 8 else payload.hex()
            self._errors.append(VolcengineError(f"火山错误 code={code}: {msg}"))
            return True
        if mtype == 0b1001:  # full server response
            self._handle_result(payload)
            return flags == 0b0011
        return False

    def _handle_result(self, payload: bytes):
        try:
            data = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            return
        result = data.get("result") or {}
        text = result.get("text", "") or ""
        if text:
            self._last_full = text
        for u in (result.get("utterances") or []):
            if u.get("definite"):
                t = u.get("text", "") or ""
                if t and (not self._definite_parts or self._definite_parts[-1] != t):
                    self._definite_parts.append(t)
        if self._on_partial:
            cur = "".join(self._definite_parts)
            try:
                self._on_partial(cur if cur else self._last_full)
            except Exception:
                pass


class VolcengineASR:
    """豆包流式 ASR 客户端。

    transcribe_file(wav_path, on_partial=None, enable_nonstream=True,
                    hotwords=None) -> str
      - on_partial(cur_text)：中间/最终结果回调（调用方保证线程安全）
      - 返回完整识别文本（definite 分句拼接；无 definite 时取最后全量文本）
    """

    def __init__(self, api_key: str, resource_id: str = RESOURCE_ID_2_0,
                 rate: int = 16000, seg_ms: int = 200, timeout: int = 60):
        if not api_key:
            raise VolcengineError("未填写火山引擎 API Key")
        self.api_key = api_key
        self.resource_id = resource_id
        self.rate = rate
        self.seg_ms = seg_ms
        self.timeout = timeout

    def transcribe_file(self, wav_path: str, on_partial=None,
                        enable_nonstream: bool = True,
                        hotwords: list[str] | None = None) -> str:
        raw = Path(wav_path).read_bytes()
        if not raw:
            raise VolcengineError("音频为空")
        pcm = _strip_wav_header(raw)
        if not pcm:
            raise VolcengineError("音频数据为空（wav 解析失败）")

        request_payload = {
            "audio": {"format": "pcm", "codec": "raw",
                      "rate": self.rate, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": enable_nonstream,
                "result_type": "single",
            },
        }
        if hotwords:
            request_payload["request"]["corpus"] = {
                "hotwords": [{"word": w} for w in hotwords[:50]],
            }

        headers = [
            f"X-Api-Key: {self.api_key}",
            f"X-Api-Resource-Id: {self.resource_id}",
            f"X-Api-Request-Id: {str(uuid.uuid4())}",
            "X-Api-Sequence: -1",
        ]

        definite_parts: list[str] = []
        last_full_text = ""
        errors: list[Exception] = []

        def handle_response_payload(payload: bytes):
            nonlocal last_full_text
            try:
                data = json.loads(payload.decode("utf-8", "replace"))
            except Exception:
                return
            result = (data.get("result") or {}) if isinstance(data, dict) else {}
            text = result.get("text", "") or ""
            if text:
                last_full_text = text
            for u in (result.get("utterances") or []):
                if u.get("definite"):
                    t = u.get("text", "") or ""
                    if t and (not definite_parts or definite_parts[-1] != t):
                        definite_parts.append(t)
            if on_partial:
                cur = "".join(definite_parts)
                on_partial(cur if cur else last_full_text)

        def handle_frame(mtype: int, flags: int, payload: bytes) -> bool:
            """处理一个服务端帧；返回 True 表示收到最后一包（可结束）。"""
            if mtype == 0b1111:  # error frame: Header+Code(4)+Size(4)+message
                code = struct.unpack(">I", payload[:4])[0] if len(payload) >= 4 else -1
                msg = payload[8:].decode("utf-8", "replace") if len(payload) >= 8 else payload.hex()
                errors.append(VolcengineError(f"火山错误 code={code}: {msg}"))
                return True
            if mtype == 0b1001:  # full server response
                handle_response_payload(payload)
                return flags == 0b0011  # 最后一包音频结果
            return False

        ws = None
        try:
            ws = websocket.create_connection(
                WS_URL, header=headers, timeout=self.timeout,
                enable_multithread=True,
            )
            # 1. full client request
            ws.send_binary(_frame(
                0b0001, 0b0000, 0b0001,
                json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            ))
            # 2. audio only request（200ms/块，最后一块 flags=0b0010）
            block = int(self.rate * self.seg_ms / 1000) * 2  # 16bit mono → bytes
            chunks = [pcm[i:i + block] for i in range(0, len(pcm), block)]
            if not chunks:
                chunks = [b""]
            for i, ch in enumerate(chunks):
                is_last = (i == len(chunks) - 1)
                ws.send_binary(_frame(0b0010, 0b0010 if is_last else 0b0000, 0b0000, ch))

            deadline = time.time() + self.timeout
            while time.time() < deadline:
                if errors:
                    raise errors[0]
                ws.settimeout(max(1.0, deadline - time.time()))
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except websocket.WebSocketConnectionClosedException:
                    break
                if not message or not isinstance(message, bytes):
                    continue
                done = False
                for mtype, flags, payload in _split_server_frame(message):
                    if handle_frame(mtype, flags, payload):
                        done = True
                if done:
                    break
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

        if errors:
            raise errors[0]
        if definite_parts:
            return "".join(definite_parts)
        if last_full_text:
            return last_full_text
        raise VolcengineError("火山识别无返回结果（可能音频过短或静音）")


if __name__ == "__main__":
    import sys
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    wav = sys.argv[2] if len(sys.argv) > 2 else ""
    if not key or not wav:
        print("用法: python volcengine_asr.py <api_key> <wav_path> [--nostream]")
        sys.exit(1)
    nostream = "--nostream" in sys.argv
    client = VolcengineASR(key)

    def _p(t):
        print("  [partial]", t, flush=True)

    print("开始识别...")
    final = client.transcribe_file(wav, on_partial=_p, enable_nonstream=not nostream)
    print("最终:", final)
