# -*- coding: utf-8 -*-
"""开机自启（迁移自 Tk 版 set_autostart：写入启动文件夹快捷方式）。
纯标准库 + PowerShell COM，无第三方依赖。
注意：自启目标是主壳 voxecho.exe（与 sidecar 同目录）；dev 模式下不写。
"""
from __future__ import annotations
from log_i18n import L

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("voxecho.autostart")

LINK_NAME = "VoxEcho.lnk"


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def _create_shortcut(link_path: Path, target: Path) -> None:
    target_s = str(target).replace("'", "''")
    link_s = str(link_path).replace("'", "''")
    work_s = str(target.parent).replace("'", "''")
    icon_s = str(target).replace("'", "''")
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "$s.IconLocation = '%s,0'; "
        "$s.Save()"
    ) % (link_s, target_s, work_s, icon_s)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=True,
        capture_output=True,
        timeout=30,
    )


def shell_exe() -> Path | None:
    """定位主壳 exe（自启目标）。sidecar 与主壳同目录；dev 返回 None。"""
    me = Path(sys.executable)
    name = me.name.lower()
    if name.startswith("voxecho-backend"):
        cand = me.parent / "voxecho.exe"
        if cand.exists():
            return cand
        return None
    if name == "python.exe" or name == "pythonw.exe":
        return None  # dev 模式：不写自启
    return me


def set_autostart(enabled: bool) -> bool:
    """启用/禁用开机自启。返回是否成功。"""
    if sys.platform != "win32":
        return False
    link = startup_folder() / LINK_NAME
    try:
        if enabled:
            target = shell_exe()
            if target is None:
                logger.info(L("autostart_skip"))
                return False
            startup_folder().mkdir(parents=True, exist_ok=True)
            _create_shortcut(link, target)
            logger.info(L("autostart_set", target=target))
        else:
            if link.exists():
                link.unlink()
            logger.info(L("autostart_clear"))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(L("autostart_fail", err=e))
        return False
