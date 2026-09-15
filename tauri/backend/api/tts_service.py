# -*- coding: utf-8 -*-
"""FewType TTS 引擎（FastAPI 版，从 Flask server.py 迁移）。

edge-tts 合成能力原样保留，改造点：
- Flask 同步 → FastAPI async（edge_tts.Communicate 本身就是 async，无需 asyncio.run 桥接）
- 音色清单缓存 24h 逻辑不变
- 长文本分段 /tts_file 迁移为 POST /api/tts（返回文件路径）
- 短文本试听 /speak 迁移为 POST /api/tts/speak（返回音频流）
- 新增 POST /api/tts/translate（LLM 翻译，长文本转语音前置）
"""
from __future__ import annotations
from log_i18n import L

import logging
import os
import re
import time
from pathlib import Path

import edge_tts

logger = logging.getLogger("fewtype.tts")

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
MAX_ATTEMPTS = 3
TTS_MAX_SEG = 800          # 每段最大字符数（edge-tts 单次合成稳定性上限）
MAX_TEXT_CHARS = 20000     # 单次合成文本上限

# ---- 音色清单缓存（edge_tts.list_voices 走微软接口，网络请求，缓存 24h）----
_VOICES_CACHE: list[dict] | None = None
_VOICES_CACHE_TIME = 0.0
VOICES_CACHE_TTL = 24 * 60 * 60


def _user_documents_dir() -> Path:
    """Windows 用户文档目录（SHGetKnownFolderPath FOLDERID_Documents），兼容 OneDrive 重定向。"""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", wintypes.BYTE * 8),
            ]

        # FOLDERID_Documents = {A8C1F832-2D62-4B68-A0FB-B36D1F2A1D6B}
        guid = GUID(0xA8C1F832, 0x2D62, 0x4B68,
                    (0xA0, 0xFB, 0xB3, 0x6D, 0x1F, 0x2A, 0x1D, 0x6B))
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        buf = ctypes.c_wchar_p()
        hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buf))
        if hr == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Documents"


def tts_output_dir() -> Path:
    """输出目录：环境变量 FEWTYPE_OUTPUT_DIR 优先，否则默认"用户→文档→FewType_tts_out"。"""
    d = os.environ.get("FEWTYPE_OUTPUT_DIR")
    if d:
        return Path(d)
    return _user_documents_dir() / "FewType_tts_out"


# ---------------------------------------------------------------- 音色清单
async def fetch_voices() -> list[dict]:
    """拉取 edge-tts 音色清单（原始字段精简后返回）。"""
    raw = await edge_tts.list_voices()
    out = []
    for v in raw or []:
        short_name = v.get("ShortName")
        if not short_name:
            continue
        out.append({
            "shortName": short_name,
            "friendlyName": v.get("FriendlyName") or short_name,
            "gender": v.get("Gender"),
            "locale": v.get("Locale") or "",
            "status": v.get("Status"),
        })
    return out


async def get_voices(force_refresh: bool = False) -> tuple[list[dict], float]:
    """带 24h 缓存的音色清单。返回 (voices, updated_at_ts)。
    首次失败抛异常（由路由转 502）；有旧缓存时失败回退旧缓存。"""
    global _VOICES_CACHE, _VOICES_CACHE_TIME
    now = time.time()
    if force_refresh or _VOICES_CACHE is None or (now - _VOICES_CACHE_TIME) > VOICES_CACHE_TTL:
        fetched = await fetch_voices()
        if not fetched:
            raise RuntimeError("edge_tts.list_voices 返回空")
        _VOICES_CACHE = fetched
        _VOICES_CACHE_TIME = now
        logger.info(L("voices_refresh", n=len(fetched), locales=len({v["locale"] for v in fetched})))
    return _VOICES_CACHE, _VOICES_CACHE_TIME


# ---------------------------------------------------------------- 合成核心
# 默认音量提升：edge-tts 原始响度偏小（+0%），统一抬到 +20% 改善听感
TTS_VOLUME_BOOST = "+20%"


async def synthesize(text: str, voice: str, rate: str | None = None,
                     volume: str | None = None) -> bytes:
    """单段合成，返回 MP3 bytes。volume 未传时用默认提升（+20%）。"""
    kwargs = {"volume": volume or TTS_VOLUME_BOOST}
    if rate:
        kwargs["rate"] = rate
    communicate = edge_tts.Communicate(text, voice, **kwargs)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


async def synthesize_with_retry(text: str, voice: str, rate: str | None = None,
                                volume: str | None = None) -> bytes:
    """带退避重试的合成（最多 MAX_ATTEMPTS 次，间隔 1s）。"""
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await synthesize(text, voice, rate, volume)
        except Exception as e:  # noqa: BLE001
            last = e
            logger.warning(L("synth_retry", attempt=attempt, max=MAX_ATTEMPTS, type=type(e).__name__, err=e))
            if attempt < MAX_ATTEMPTS:
                import asyncio
                await asyncio.sleep(1.0)
    raise last  # type: ignore[union-attr]


# ---------------------------------------------------------------- 长文本分段
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


async def tts_file(text: str, voice: str, rate: str | None = None,
                   volume: str | None = None,
                   output_dir: str | Path | None = None) -> dict:
    """长文本分段合成 → 写入文件。返回 {path, filename, bytes, segments}。"""
    segments = split_text(text)
    if not segments:
        raise ValueError("没有可合成的文本")
    audios: list[bytes] = []
    for i, seg in enumerate(segments):
        try:
            audios.append(await synthesize_with_retry(seg, voice, rate, volume))
        except Exception as e:  # noqa: BLE001
            logger.error(L("seg_fail", n=i + 1, type=type(e).__name__, err=e))
            raise RuntimeError(f"第 {i + 1} 段合成失败: {e}") from e
    blob = b"".join(audios)
    out_dir = Path(output_dir) if output_dir else tts_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = "fewtype_%s.mp3" % time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / fname
    path.write_bytes(blob)
    logger.info(L("tts_file", fname=fname, segments=len(audios), bytes=len(blob)))
    return {
        "path": str(path),
        "filename": fname,
        "bytes": len(blob),
        "segments": len(audios),
        "voice": voice,
    }


async def tts_speak(text: str, voice: str, rate: str | None = None,
                    volume: str | None = None) -> bytes:
    """短文本试听：直接返回 MP3 bytes（不写文件）。"""
    return await synthesize_with_retry(text, voice, rate, volume)
