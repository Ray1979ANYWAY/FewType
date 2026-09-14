# -*- coding: utf-8 -*-
"""VoxEcho 配置存储层（FastAPI 版）。

从 launcher.py 剥离的 load_config / save_config 逻辑，独立成模块：
- 配置文件：%APPDATA%\\VoxEcho-tauri\\bridge_config.json（含 API Key 与风格 Prompt，
  绝不能放在程序目录——否则打包/复制/转移程序文件夹会带走密钥）
- 提供 GET 用安全视图（API Key 遮罩）与 POST 用白名单更新
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


def find_engine_dir() -> Path:
    """定位引擎目录（tauri/backend/engine：provider/stt_engine 等模块与配置文件的所在）。

    无论 api 包被放在仓库内哪个位置，逐级向上查找包含 engine 的祖先目录；
    找不到则回退到上级的上级。
    """
    p = Path(__file__).resolve()
    for parent in p.parents:
        cand = parent / "engine"
        if cand.is_dir():
            return cand
    return p.parent.parent


def config_dir() -> Path:
    """配置文件目录：%APPDATA%\\com.rayanyway.voxecho（与 Tauri identifier 一致，
    卸载时 NSIS deleteAppDataOnUninstall 才能一并删除，不留含 Key 的残留）。"""
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "com.rayanyway.voxecho"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _legacy_config_path() -> Path:
    """旧版配置路径（打包后位于 exe 旁，源码运行位于引擎目录 engine/ 下）。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = find_engine_dir()
    return base / "bridge_config.json"


def config_path() -> Path:
    """配置文件路径：%APPDATA%\\com.rayanyway.voxecho（含 API Key，不随程序目录被拖走）。"""
    return config_dir() / "bridge_config.json"


CONFIG_PATH = config_path()


def _migrate_legacy_config() -> None:
    """把旧位置的配置迁到新目录并删除源文件（含 API Key，不能留在任何程序目录）。
    迁移来源（按顺序）：
      1) %APPDATA%\\VoxEcho-tauri（上一版 APPDATA 目录名）
      2) 程序目录 / engine 目录（更早的旧版）
    迁移后打包、压缩、复制、移动整个程序文件夹都不会带走密钥。"""
    _base = os.environ.get("APPDATA") or str(Path.home())
    candidates = [
        Path(_base) / "VoxEcho-tauri" / "bridge_config.json",  # 上一版 APPDATA 目录
        _legacy_config_path(),                                   # 程序目录/engine 目录
    ]
    _dst = CONFIG_PATH
    for _src in candidates:
        try:
            if _src.exists():
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
    复制仍允许——Windows 没有“复制时弹警告”的机制；真正防泄露靠 config 不在程序目录。
    注意：因此 save_config 不能再用 tmp.replace（replace 需要 DELETE 权限），改直接 write_text。"""
    try:
        p = CONFIG_PATH
        if not p.exists():
            return
        subprocess.run(["icacls", str(p), "/deny", "*S-1-1-0:(DE)"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# 迁移旧版程序目录里的 config（须在 load_config 首次调用前完成）
_migrate_legacy_config()
_lock_config_file()

# 默认配置（与 launcher.load_config 保持一致，另补全 Tk 版实际使用的字段）
DEFAULTS: dict = {
    "autostart": False,
    "first_run_done": False,
    "ui_lang": "",
    "stt_hotkey": "ctrl+win",
    # 迁移标记默认置真：新安装/新保存的配置自带标记，load_config 的一次性迁移
    # （老配置残留 double_ctrl → 改回 ctrl+win）不会误伤用户手动保存的 double_ctrl。
    # 否则第一次保存 double_ctrl 会被迁移逻辑改回 ctrl+win，表现为"保存两次才生效"。
    "hotkey_migrated": True,
    "stt_auto_commit": True,
    "stt_mode": "verbatim",
    "stt_custom_style": "",
    "stt_translate": False,
    "stt_target_lang": "简体中文",
    "tts_output_dir": "",
    "provider": {
        "platform": "groq",
        "api_key": "",
        "llm_key": "",
        "asr_model": "",
        "llm_model": "",
        "base_url": "",
        "llm_base_url": "",
    },
    "stt_styles": [
        {
            "name": "Karwai Wong",
            "prompt": "Role: You are a scriptwriter specializing in Wong Kar-wai's signature cinematic monologue style.\n\nTask: Rewrite the user's input into a reflective, poetic, and atmospheric monologue reminiscent of classic Hong Kong cinema (e.g., Chungking Express, In the Mood for Love).\n\nStyle Guidelines:\n1. Temporal Anchors: Frequently frame thoughts around ultra-specific timestamps, precise distances, or shelf-life expiration dates (e.g., \"At 0.01mm apart,\" \"57 minutes past midnight,\" \"Canned pineapples expiring on May 1st\").\n2. Sensory & Visual Imagery: Evoke neon lights, rain-slicked streets, lingering smoke, retro songs, and quiet solitary moments.\n3. Tone: Melancholic, nostalgic, detached yet emotionally deeply yearning. Use short, rhythmic sentences with reflective pauses.\n4. Core Retention: Keep the essential meaning or main event from the original speech, but reframe it as a memory or interior monologue.\n\nOutput Constraint:\nOutput ONLY the final polished text in the requested target language. Do NOT add meta commentary, markdown formatting, or cinematic scene directions (like [Camera cuts])."
        }
    ],
}

# 顶层字段白名单：POST /api/config 只允许更新这些键
TOP_LEVEL_KEYS = {
    "autostart", "first_run_done", "ui_lang", "stt_hotkey",
    "stt_auto_commit", "stt_mode", "stt_custom_style",
    "stt_translate", "stt_target_lang",
    "tts_output_dir", "provider", "stt_styles",
}


def sanitize_api_key(raw: str) -> str:
    """清洗 API Key：去除空白/换行/常见前后缀包裹，返回紧凑 key。
    粘贴自网页/邮件时常带换行与说明文字，这里统一处理。"""
    s = (raw or "").strip()
    # 去掉首尾成对引号（' " `）与常见包裹
    for q in ("'", '"', "`"):
        if len(s) >= 2 and s[0] == q and s[-1] == q:
            s = s[1:-1].strip()
    # 折叠内部空白（换行/制表符/空格），只保留有效字符
    import re
    s = re.sub(r"\s+", "", s)
    return s

# provider 子字段白名单
PROVIDER_KEYS = {
    "platform", "api_key", "llm_key", "asr_model", "llm_model",
    "base_url", "llm_base_url",
}


def load_config() -> dict:
    """读取配置：默认值打底，文件存在则合并覆盖。失败静默返回默认值。"""
    cfg = deepcopy(DEFAULTS)
    # 配置文件里原始的迁移标记（不合并 DEFAULTS 的标记，用于判断是否老残留）
    raw_has_migrated = True
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
                raw_has_migrated = bool(data.get("hotkey_migrated"))
                # provider 子键也做默认值合并（旧配置文件可能缺 llm_key 等字段）
                prov = cfg.setdefault("provider", {})
                prov.update({k: v for k, v in DEFAULTS["provider"].items() if k not in prov})
                # stt_styles 缺失或为空时回退内置默认风格（保证王家卫等内置风格可用）
                if not cfg.get("stt_styles"):
                    cfg["stt_styles"] = deepcopy(DEFAULTS["stt_styles"])
        except Exception:
            pass
    # 一次性迁移：3.1.0 及更早的默认快捷键是 double_ctrl，3.1.4 起默认改为 ctrl+win。
    # 老配置文件里残留的 double_ctrl 会覆盖新默认值，首次启动自动迁移（置位标记后不再迁移，
    # 用户之后手动改回 double_ctrl 不受影响）。
    # 判断依据必须是配置文件原始标记：DEFAULTS 自带标记为 True（新配置不迁移），
    # 否则用户手动保存 double_ctrl 会被误判为老残留而改回 ctrl+win（"保存两次才生效"）。
    if cfg.get("stt_hotkey") == "double_ctrl" and not raw_has_migrated:
        cfg["stt_hotkey"] = "ctrl+win"
        cfg["hotkey_migrated"] = True
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> bool:
    """写入配置（直接写。不能用 tmp.replace——文件带“拒绝删除”ACL，replace 需要 DELETE 权限）。"""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _mask_key(key: str) -> str:
    """API Key 遮罩：保留前 4 位 + 后 4 位，中间打星。空返回空串。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def public_view(cfg: dict) -> dict:
    """GET /api/config 的安全视图：不泄露完整 API Key。
    provider.api_key / provider.llm_key 替换为 has_* + masked 字段。
    """
    out = deepcopy(cfg)
    prov = out.get("provider", {})
    for k in ("api_key", "llm_key"):
        raw = prov.get(k, "")
        prov[f"has_{k}"] = bool(raw)
        prov[k + "_masked"] = _mask_key(raw)
        prov.pop(k, None)
    return out


def apply_update(current: dict, incoming: dict) -> dict:
    """POST /api/config 白名单合并：返回新配置，不改动传入对象。"""
    cfg = deepcopy(current)
    for key, value in incoming.items():
        if key not in TOP_LEVEL_KEYS:
            continue
        if key == "provider" and isinstance(value, dict):
            prov = cfg.setdefault("provider", {})
            for pk, pv in value.items():
                if pk in PROVIDER_KEYS:
                    # 前端传空字符串表示"不修改"时保留原值
                    if pk in ("api_key", "llm_key") and pv in (None, ""):
                        continue
                    prov[pk] = pv
        elif key == "stt_styles" and isinstance(value, list):
            cfg[key] = value
        else:
            cfg[key] = value
    return cfg
