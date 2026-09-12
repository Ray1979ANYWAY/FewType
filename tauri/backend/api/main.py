# -*- coding: utf-8 -*-
"""VoxEcho FastAPI 后端服务（阶段一：Tauri 架构后端引擎）。

替代原 Flask server.py + launcher.py 中的服务编排：
    POST /api/tts            长文本 TTS → 返回音频文件路径
    POST /api/tts/speak      短文本试听 → 返回 MP3 音频流
    POST /api/tts/translate  LLM 翻译（长文本转语音前置）
    GET  /api/voices         音色清单（24h 缓存）
    GET  /api/config         读取配置（API Key 遮罩）
    POST /api/config         更新配置（白名单）
    WS   /ws/speech-input    语音输入：录音状态 / 识别结果 / 上屏日志
    GET  /health             健康检查

兼容端点（旧 Tk launcher 迁移期依赖，与 Flask 版路径一致）：
    GET  /voices  POST /speak  POST /tts_file
    POST /extension_heartbeat  GET /extension_status

静默运行：`python main.py`（无参启动）或 `python main.py --silent`（隐藏控制台窗口）。
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# 引擎模块（provider / stt_engine / volcengine_asr / normalize / hotkey_hook）位于
# tauri/backend/engine，无论本包放哪里都先定位并加入 sys.path；本包目录同时加入。
_ENGINE_DIR = Path(__file__).resolve()
while not (_ENGINE_DIR / "engine").is_dir() and _ENGINE_DIR.parent != _ENGINE_DIR:
    _ENGINE_DIR = _ENGINE_DIR.parent
if (_ENGINE_DIR / "engine").is_dir():
    _ENGINE_DIR = _ENGINE_DIR / "engine"
for _p in (str(_ENGINE_DIR), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from config_store import apply_update, load_config, public_view, save_config, sanitize_api_key
import tts_service
from speech_service import SpeechInputManager
from hotkey_service import GlobalHotkeyService
from log_i18n import L, set_lang

# ---------------------------------------------------------------- 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("voxecho")
set_lang(str((load_config().get("ui_lang") or "zh-CN")))

app = FastAPI(title="VoxEcho API", version="0.10.0", docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 本地服务，Tauri 前端 / 旧 UI 均需跨域
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------- Pydantic 模型
class TTSRequest(BaseModel):
    text: str
    voice: str = tts_service.DEFAULT_VOICE
    rate: Optional[str] = None
    volume: Optional[str] = None
    output_dir: Optional[str] = None


class SpeakRequest(BaseModel):
    text: str
    voice: str = tts_service.DEFAULT_VOICE
    rate: Optional[str] = None
    volume: Optional[str] = None


class TranslateRequest(BaseModel):
    text: str
    target_lang: str = "简体中文"


class STTStartConfig(BaseModel):
    mode: str = "verbatim"          # verbatim | fluent | formal | custom
    custom_prompt: Optional[str] = None
    translate: bool = False
    target_lang: str = "简体中文"


class ProviderRequest(BaseModel):
    """Provider 测试/抓取请求：可携带临时输入（未保存）或读取配置。"""
    platform: str = "groq"
    api_key: str = ""
    llm_key: str = ""
    asr_model: str = ""
    llm_model: str = ""
    base_url: str = ""
    llm_base_url: str = ""


# ---------------------------------------------------------------- 单例
_speech = SpeechInputManager()
_hotkey = GlobalHotkeyService(_speech)


# ---------------------------------------------------------------- 运行日志环形缓冲（抽屉日志面板数据源）
class RingBufferHandler(logging.Handler):
    """把 root logger 的日志写入内存环形缓冲，供 GET /api/logs 读取。"""

    def __init__(self, maxlen: int = 500) -> None:
        super().__init__()
        self._buf: collections.deque = collections.deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name == "uvicorn.access":
                return  # 访问日志刷屏，不进抽屉
            self._buf.append(self.format(record))
        except Exception:
            pass

    def snapshot(self) -> list[str]:
        return list(self._buf)


_log_handler = RingBufferHandler()
_log_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
)
logging.getLogger().addHandler(_log_handler)


def _on_speech_event(ev: dict) -> None:
    """全局事件订阅：热键触发的会话 → 后端自动上屏（其他事件忽略）。"""
    if ev.get("type") == "commit" and ev.get("source") == "hotkey":
        logger.info("[debug] commit 事件已路由到 on_commit")
        _hotkey.on_commit(ev.get("text", ""))


_speech.add_listener(_on_speech_event)

# Chrome 扩展心跳（迁移自 server.py）
_EXTENSION_LAST_HEARTBEAT = 0.0
_EXTENSION_HEARTBEAT_TIMEOUT = 90


def _provider_from_config():
    from provider import Provider
    pc = (load_config().get("provider") or {})
    return Provider(
        pc.get("platform", "groq"),
        pc.get("api_key", ""),
        pc.get("asr_model", ""),
        pc.get("llm_model", ""),
        pc.get("base_url", ""),
        pc.get("llm_key", ""),
        pc.get("llm_base_url", ""),
    )


# ---------------------------------------------------------------- 健康 & 扩展
@app.get("/health")
async def health():
    return {"status": "ok", "service": "voxecho-api"}


@app.post("/extension_heartbeat")
async def extension_heartbeat():
    global _EXTENSION_LAST_HEARTBEAT
    _EXTENSION_LAST_HEARTBEAT = time.time()
    return {"status": "ok"}


@app.get("/extension_status")
async def extension_status():
    now = time.time()
    last = _EXTENSION_LAST_HEARTBEAT
    online = (last > 0) and (now - last < _EXTENSION_HEARTBEAT_TIMEOUT)
    return {
        "online": online,
        "last_heartbeat": last,
        "seconds_since_last": (now - last) if last > 0 else None,
    }


# ---------------------------------------------------------------- 音色清单
@app.get("/api/voices")
@app.get("/voices")
async def voices():
    try:
        vlist, updated = await tts_service.get_voices()
    except Exception as e:  # noqa: BLE001
        logger.error(L("voices_fail", type=type(e).__name__, err=e))
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    return {"voices": vlist, "updatedAt": int(updated * 1000)}


# ---------------------------------------------------------------- TTS
@app.post("/api/tts")
async def api_tts(req: TTSRequest):
    """长文本 TTS：自动分段合成，返回音频文件路径。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")
    if len(text) > tts_service.MAX_TEXT_CHARS:
        raise HTTPException(status_code=400,
                            detail=f"文本过长（上限 {tts_service.MAX_TEXT_CHARS} 字）")
    try:
        result = await tts_service.tts_file(text, req.voice, req.rate, req.volume, req.output_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.error(L("tts_fail", type=type(e).__name__, err=e))
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
    return result


@app.post("/api/tts/speak")
async def api_tts_speak(req: SpeakRequest):
    """短文本试听：返回 MP3 音频流。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")
    if len(text) > tts_service.TTS_MAX_SEG:
        raise HTTPException(status_code=400,
                            detail=f"试听仅支持 {tts_service.TTS_MAX_SEG} 字以内，长文本请用 /api/tts")
    try:
        audio = await tts_service.tts_speak(text, req.voice, req.rate, req.volume)
    except Exception as e:  # noqa: BLE001
        logger.error(L("speak_fail", type=type(e).__name__, err=e))
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
    return Response(audio, media_type="audio/mpeg")


@app.post("/api/tts/translate")
async def api_tts_translate(req: TranslateRequest):
    """LLM 翻译（长文本转语音前置）：调 provider.chat，线程池避免阻塞事件循环。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")
    provider = _provider_from_config()
    if not (load_config().get("provider") or {}).get("api_key"):
        raise HTTPException(status_code=400, detail="未配置 API Key")
    from provider import build_messages
    sys_p = (
        "You are a professional translator. Translate the user's text into %s "
        "in natural, native, idiomatic style. Preserve meaning, tone and structure. "
        "Output ONLY the translated text, no explanations, no quotes." % req.target_lang
    )
    try:
        out = await asyncio.to_thread(
            provider.chat, build_messages(sys_p, text), 0.3, 60, None
        )
    except Exception as e:  # noqa: BLE001
        logger.error(L("translate_fail", type=type(e).__name__, err=e))
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"translated": out.strip(), "target_lang": req.target_lang}


# 兼容端点（旧 launcher 依赖）
@app.post("/tts_file")
async def compat_tts_file(req: TTSRequest):
    return await api_tts(req)


@app.post("/speak")
async def compat_speak(req: SpeakRequest):
    return await api_tts_speak(req)


# ---------------------------------------------------------------- Provider 测试/抓取
def _provider_from_req(req: ProviderRequest):
    """从请求体构建 Provider（优先请求体临时值，空字段回退配置文件）。"""
    from provider import Provider
    pc = (load_config().get("provider") or {})
    return Provider(
        req.platform or pc.get("platform", "groq"),
        req.api_key or pc.get("api_key", ""),
        req.asr_model or pc.get("asr_model", ""),
        req.llm_model or pc.get("llm_model", ""),
        req.base_url or pc.get("base_url", ""),
        req.llm_key or pc.get("llm_key", ""),
        req.llm_base_url or pc.get("llm_base_url", ""),
    )


@app.post("/api/provider/test")
async def provider_test(req: ProviderRequest):
    """测试连接：调 provider.test_connection（抓取 /models 验 Key 与网络）。"""
    try:
        prov = _provider_from_req(req)
        if not (req.api_key or (load_config().get("provider") or {}).get("api_key")):
            return {"ok": False, "message": "未填写 API Key"}
        msg = await asyncio.to_thread(prov.test_connection, 12)
        return {"ok": True, "message": msg}
    except Exception as e:  # noqa: BLE001
        logger.info(L("test_fail", type=type(e).__name__, err=e))
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


@app.post("/api/provider/models")
async def provider_models(req: ProviderRequest):
    """抓取并分类模型列表（ASR / LLM）。火山 ASR 无列表接口，返回内置资源 ID。"""
    try:
        from provider import VOLC_ASR_IDS
        prov = _provider_from_req(req)
        platform = (req.platform or (load_config().get("provider") or {}).get("platform", "groq"))
        if platform == "volcengine":
            return {"ok": True, "asr": list(VOLC_ASR_IDS), "llm": []}
        asr_list, llm_list = await asyncio.to_thread(prov.fetch_models_split)
        return {"ok": True, "asr": asr_list, "llm": llm_list}
    except Exception as e:  # noqa: BLE001
        logger.info(L("fetch_fail", type=type(e).__name__, err=e))
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- 配置
@app.get("/api/config")
async def get_config():
    return public_view(load_config())


@app.post("/api/config")
async def post_config(req: dict):
    current = load_config()
    updated = apply_update(current, req)
    # 保存 Provider 时清洗 Key（粘贴自动清洗的兜底，防换行/空格混入）
    pc = updated.get("provider") or {}
    if isinstance(pc, dict):
        dirty = False
        for k in ("api_key", "llm_key"):
            v = pc.get(k)
            if isinstance(v, str) and v:
                cleaned = sanitize_api_key(v)
                if cleaned != v:
                    pc[k] = cleaned
                    dirty = True
        # 平台切换治理：非火山平台不使用独立 LLM Key。
        # 前端切换平台时 payload 不含 llm_key，apply_update 会保留旧值
        # （如残留的火山 ark Key），导致翻译用错 key 报「key 无效」。此处清空回退主 Key。
        req_pc = req.get("provider") or {}
        plat = str(req_pc.get("platform") or pc.get("platform") or "")
        if "provider" in req and plat and plat != "volcengine" and "llm_key" not in req_pc:
            if pc.get("llm_key"):
                pc["llm_key"] = ""
                dirty = True
        if dirty:
            updated["provider"] = pc
    if not save_config(updated):
        raise HTTPException(status_code=500, detail="配置写入失败")
    logger.info(L("config_updated", keys=sorted(req.keys())))
    # ui_lang 变化 → 日志语言跟随
    if "ui_lang" in req:
        set_lang(str(updated.get("ui_lang") or "zh-CN"))
    # 快捷键配置变化 → 重建全局热键钩子
    if "stt_hotkey" in req or "stt_auto_commit" in req:
        _hotkey.restart()
    # 开机自启变化 → 写/删启动文件夹快捷方式（Tk 版同款逻辑）
    if "autostart" in req:
        import autostart_util
        autostart_util.set_autostart(bool(updated.get("autostart")))
    return public_view(updated)


# ---------------------------------------------------------------- 系统对话框
@app.post("/api/dialog/pick-dir")
def api_pick_dir(req: dict | None = None):
    """弹出系统文件夹选择对话框（tkinter filedialog）。"""
    req = req or {}
    initial = str(req.get("initial") or "")
    import dialog_util
    path = dialog_util.pick_directory(initial)
    return {"path": path}


@app.post("/api/dialog/open-dir")
def api_open_dir(req: dict):
    """用资源管理器打开目录（或定位文件所在目录）。"""
    req = req or {}
    path = str(req.get("path") or "")
    import dialog_util
    ok = dialog_util.open_directory(path)
    return {"ok": ok, "path": path}


# ---------------------------------------------------------------- 运行日志
@app.get("/api/logs")
async def api_logs():
    """返回最近运行日志（抽屉日志面板轮询源）。"""
    return {"lines": _log_handler.snapshot()}


# ---------------------------------------------------------------- 语音服务管理
@app.post("/api/speech/restart")
async def api_speech_restart():
    """重启语音服务：中止当前会话并广播 idle（HUD 状态条假死时用）。"""
    _speech.abort()
    logger.info(L("speech_restarted"))
    return {"ok": True}


@app.post("/api/speech/confirm")
async def api_speech_confirm(payload: dict):
    """翻译确认：转写完成后用户在 HUD 编辑框确认/修改原文，据此继续翻译上屏。

    body: {"text": str, "source": "hotkey"|"ws", "cancel": bool}
    """
    text = str(payload.get("text", "")).strip()
    source = str(payload.get("source", "ws"))
    cancel = bool(payload.get("cancel", False))
    if not cancel and not text:
        return {"ok": False, "error": "text 为空"}
    ok = _speech.confirm(text, source, cancel=cancel)
    if ok:
        logger.info(L("speech_confirmed"))
    return {"ok": ok, "cancel": cancel}


# ---------------------------------------------------------------- 显示器工作区
@app.get("/api/display/workarea")
def api_workarea():
    """主显示器工作区矩形（排除任务栏）：HUD 悬浮条定位用。"""
    import ctypes
    from ctypes import wintypes
    try:
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            logger.info(
                f"[debug] workarea: left={rect.left} top={rect.top} "
                f"right={rect.right} bottom={rect.bottom}"
            )
            return {
                "left": int(rect.left), "top": int(rect.top),
                "right": int(rect.right), "bottom": int(rect.bottom),
            }
    except Exception as e:  # noqa: BLE001
        logger.warning(L("workarea_fail", err=e))
    return {"left": 0, "top": 0, "right": 0, "bottom": 0}


class FrontendLogRequest(BaseModel):
    msg: str = ""


@app.post("/api/frontend-log")
async def api_frontend_log(req: FrontendLogRequest):
    """HUD 前端调试日志桥：前端把定位/权限失败信息打到后端日志，便于远程排查。"""
    logger.info(f"[hud-front] {req.msg}")
    return {"ok": True}


# ---------------------------------------------------------------- WS 语音输入
async def _ws_send_loop(ws: WebSocket, queue: asyncio.Queue):
    while True:
        ev = await queue.get()
        try:
            await ws.send_json(ev)
        except Exception:
            return


async def _ws_recv_loop(ws: WebSocket, queue: asyncio.Queue):
    """接收客户端指令：start / stop / abort / ping。"""
    while True:
        msg = await ws.receive_json()
        mtype = msg.get("type")
        if mtype == "start":
            cfg = msg.get("config") or {}
            try:
                start_cfg = STTStartConfig(**cfg)
            except Exception:
                await ws.send_json({"type": "error", "message": "start 参数不合法"})
                continue
            ok = _speech.start(start_cfg.model_dump())
            await ws.send_json({"type": "log",
                                "message": "已在录制（另一个会话）" if not ok else "开始录音"})
        elif mtype == "stop":
            _speech.stop()
        elif mtype == "abort":
            _speech.abort()
        elif mtype == "ping":
            await ws.send_json({"type": "pong"})


@app.websocket("/ws/speech-input")
async def ws_speech_input(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    # 线程安全地把引擎事件转投到本连接的 asyncio 队列（按连接注册/注销监听器）
    def _on_event(ev: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ev)
    _speech.add_listener(_on_event)
    await ws.send_json({"type": "ready", "version": "0.10.0"})
    send_task = asyncio.create_task(_ws_send_loop(ws, queue))
    recv_task = asyncio.create_task(_ws_recv_loop(ws, queue))
    try:
        await send_task
    except WebSocketDisconnect:
        pass
    finally:
        recv_task.cancel()
        _speech.remove_listener(_on_event)
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 启动
def _hide_console() -> None:
    """Windows 静默模式：隐藏当前控制台窗口（pythonw 启动时无窗口则无效但无害）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="VoxEcho FastAPI 后端服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5010)
    parser.add_argument("--silent", action="store_true",
                        help="Windows 上隐藏控制台窗口（静默无窗口运行）")
    args = parser.parse_args(argv)

    if args.silent:
        _hide_console()

    _hotkey.start()
    logger.info(L("api_start", host=args.host, port=args.port))
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
