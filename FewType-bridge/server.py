# -*- coding: utf-8 -*-
"""ebooks-tts 本地桥：127.0.0.1:5005"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import edge_tts
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("tts-server")

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
MAX_ATTEMPTS = 3
HOST = "127.0.0.1"
PORT = 5005

# ---- 音色清单缓存（edge_tts.list_voices 走微软接口，网络请求，缓存 24h）----
_VOICES_CACHE = None
_VOICES_CACHE_TIME = 0.0
VOICES_CACHE_TTL = 24 * 60 * 60  # 24 小时


async def fetch_voices() -> list[dict]:
    raw = await edge_tts.list_voices()
    out = []
    for v in raw or []:
        short_name = v.get("ShortName")
        if not short_name:
            continue
        out.append(
            {
                "shortName": short_name,
                "friendlyName": v.get("FriendlyName") or short_name,
                "gender": v.get("Gender"),
                "locale": v.get("Locale") or "",
                "status": v.get("Status"),
            }
        )
    return out


@app.route("/voices", methods=["GET"])
def voices():
    global _VOICES_CACHE, _VOICES_CACHE_TIME
    now = time.time()
    if _VOICES_CACHE is None or (now - _VOICES_CACHE_TIME) > VOICES_CACHE_TTL:
        try:
            fetched = asyncio.run(fetch_voices())
            if not fetched:
                raise RuntimeError("edge_tts.list_voices 返回空")
            _VOICES_CACHE = fetched
            _VOICES_CACHE_TIME = now
            logger.info(
                "刷新音色清单: %d voices / %d locales",
                len(fetched),
                len({v["locale"] for v in fetched}),
            )
        except Exception as e:
            logger.error("获取音色清单失败: %s: %s", type(e).__name__, e)
            if _VOICES_CACHE is None:
                return jsonify({"error": "%s: %s" % (type(e).__name__, e)}), 502
            # 拉取失败但有旧缓存，继续返回旧缓存
    return jsonify(
        {
            "voices": _VOICES_CACHE,
            "updatedAt": int(_VOICES_CACHE_TIME * 1000),
        }
    )


async def synthesize(text: str, voice: str, rate: Optional[str] = None) -> bytes:
    kwargs = {}
    if rate:
        kwargs["rate"] = rate
    communicate = edge_tts.Communicate(text, voice, **kwargs)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


async def synthesize_with_retry(text: str, voice: str, rate: Optional[str] = None) -> bytes:
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await synthesize(text, voice, rate)
        except Exception as e:
            last = e
            logger.warning(
                "第 %d/%d 次合成失败: %s: %s",
                attempt,
                MAX_ATTEMPTS,
                type(e).__name__,
                e,
            )
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(1.0)
    raise last  # type: ignore


@app.route("/speak", methods=["POST"])
def speak():
    payload = request.get_json(force=True) or {}
    text = (payload.get("text") or "").strip()
    voice = payload.get("voice") or DEFAULT_VOICE
    rate = payload.get("rate")
    output_dir = (payload.get("output_dir") or "").strip()
    if not text:
        return jsonify({"error": "text 不能为空"}), 400
    try:
        audio = asyncio.run(synthesize_with_retry(text, voice, rate))
    except Exception as e:
        logger.error("合成最终失败: %s: %s", type(e).__name__, e)
        return jsonify({"error": "%s: %s" % (type(e).__name__, e)}), 500
    logger.info("合成成功 voice=%s bytes=%d", voice, len(audio))
    return Response(audio, mimetype="audio/mpeg")


# ---- 长文本分段 TTS ----
TTS_MAX_SEG = 800  # 每段最大字符数（edge-tts 单次合成稳定性上限）


def tts_output_dir() -> Path:
    d = os.environ.get("FEWTYPE_OUTPUT_DIR")
    if d:
        return Path(d)
    return Path(__file__).resolve().parent / "tts_output"


def split_text(text: str, max_len: int = TTS_MAX_SEG) -> list[str]:
    """长文本切分：换行是硬分段（保留段落边界），句号是软边界（可合并），超长句硬切。"""
    chunks: list[str] = []
    cur = ""
    for para in text.splitlines():
        para = para.strip()
        if not para:
            if cur:
                chunks.append(cur)
                cur = ""
            continue
        parts = re.split(r"(?<=[。！？；.!?;])", para)
        for s in parts:
            s = s.strip()
            if not s:
                continue
            # 超长单句硬切
            while len(s) > max_len:
                if cur:
                    chunks.append(cur)
                    cur = ""
                chunks.append(s[:max_len])
                s = s[max_len:]
            if cur and len(cur) + len(s) > max_len:
                chunks.append(cur)
                cur = s
            else:
                cur = (cur + s) if cur else s
        # 换行是硬边界：段落结束即入 chunks
        if cur:
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks or []


@app.route("/tts_file", methods=["POST"])
def tts_file():
    payload = request.get_json(force=True) or {}
    text = (payload.get("text") or "").strip()
    voice = payload.get("voice") or DEFAULT_VOICE
    rate = payload.get("rate")
    output_dir = (payload.get("output_dir") or "").strip()
    if not text:
        return jsonify({"error": "text 不能为空"}), 400
    if len(text) > 20000:
        return jsonify({"error": "文本过长（上限 20000 字）"}), 400
    segments = split_text(text)
    if not segments:
        return jsonify({"error": "没有可合成的文本"}), 400
    audios = []
    for i, seg in enumerate(segments):
        try:
            audios.append(asyncio.run(synthesize_with_retry(seg, voice, rate)))
        except Exception as e:
            logger.error("第 %d 段合成失败: %s: %s", i + 1, type(e).__name__, e)
            return jsonify({"error": "第 %d 段合成失败: %s" % (i + 1, e)}), 500
    blob = b"".join(audios)
    out_dir = Path(output_dir) if output_dir else tts_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = "fewtype_%s.mp3" % time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / fname
    path.write_bytes(blob)
    logger.info("TTS 文件生成: %s (%d 段, %d bytes)", fname, len(audios), len(blob))
    return jsonify(
        {
            "path": str(path),
            "filename": fname,
            "bytes": len(blob),
            "segments": len(audios),
        }
    )


# ---- Chrome 扩展心跳检测 ----
# 扩展向 /extension_heartbeat 发 POST，本地服务记录最后活跃时间。
# UI 端通过 /extension_status 查询：90 秒内有心跳则视为在线，否则离线。
# 为什么用 90 秒超时：chrome.alarms 最小间隔 1 分钟，90 秒大于 1 分钟避免状态抖动。
_EXTENSION_LAST_HEARTBEAT = 0.0
_EXTENSION_HEARTBEAT_TIMEOUT = 90  # 秒


@app.route("/extension_heartbeat", methods=["POST"])
def extension_heartbeat():
    """Chrome 扩展心跳：扩展调用时更新最后活跃时间。"""
    global _EXTENSION_LAST_HEARTBEAT
    _EXTENSION_LAST_HEARTBEAT = time.time()
    logger.info("收到扩展心跳 (来自 %s)", request.remote_addr)
    return jsonify({"status": "ok"})


@app.route("/extension_status", methods=["GET"])
def extension_status():
    """查询扩展是否在线：90 秒内有心跳则在线。"""
    now = time.time()
    last = _EXTENSION_LAST_HEARTBEAT
    online = (last > 0) and (now - last < _EXTENSION_HEARTBEAT_TIMEOUT)
    return jsonify({
        "online": online,
        "last_heartbeat": last,
        "seconds_since_last": (now - last) if last > 0 else None,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ebooks-tts-bridge"})


def main():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
