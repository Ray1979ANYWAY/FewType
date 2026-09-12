# -*- coding: utf-8 -*-
"""原生系统对话框（文件夹选择 / 打开文件夹）。
纯标准库实现：tkinter（Python 自带）+ os.startfile，无第三方依赖。
- pick_directory：弹出系统文件夹选择对话框（Tk 版原有交互，迁移到后端）
- open_directory：用资源管理器打开目录（或定位文件所在目录）
"""
from __future__ import annotations
from log_i18n import L

import logging
import os
import threading

logger = logging.getLogger("voxecho.dialog")


def pick_directory(initial: str = "") -> str:
    """弹出系统文件夹选择对话框。返回所选路径；取消/失败返回空串。
    在独立线程创建 Tk（uvicorn 工作线程里直接建 Tk 不稳定）。"""
    result: dict = {"path": ""}

    def _run() -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception as e:  # noqa: BLE001
            logger.warning(L("dialog_tk_unavailable", err=e))
            return
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            d = filedialog.askdirectory(
                parent=root,
                initialdir=initial or None,
                title="指定输出文件夹",
            )
            root.destroy()
            result["path"] = d or ""
        except Exception as e:  # noqa: BLE001
            logger.warning(L("dialog_folder_fail", err=e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=600)  # 用户可能长时间停留，只做超时保护
    return result.get("path", "")


def open_directory(path: str) -> bool:
    """用系统资源管理器打开目录；path 为文件时打开其所在目录。"""
    try:
        if os.path.isdir(path):
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        parent = os.path.dirname(path)
        if parent and os.path.isdir(parent):
            os.startfile(parent)  # type: ignore[attr-defined]
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning(L("dialog_open_fail", path=path, err=e))
    return False
