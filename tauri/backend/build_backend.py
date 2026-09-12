# VoxEcho 后端 sidecar 打包（PyInstaller）
# 产出：dist/voxecho-backend/voxecho-backend.exe
#     → 复制为 src-tauri/binaries/voxecho-backend-x86_64-pc-windows-msvc.exe
#       （Tauri externalBin 约定：二进制名 + 目标 triple 后缀）
#
# 用法：
#   pip install pyinstaller
#   python build_backend.py
#
# 注意：引擎模块（provider / stt_engine / volcengine_asr / normalize）位于
# VoxEcho-bridge/，通过 sys.path 动态定位（见 main.py 的 find_bridge_dir）。
# PyInstaller 需要把这些模块的源码目录加入 pathex，否则打包后找不到。

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_DIR = ROOT / "api"
ENGINE_DIR = ROOT / "engine"  # 引擎模块：provider / stt_engine / volcengine_asr / normalize / hotkey_hook

import PyInstaller.__main__  # noqa: E402

args = [
    str(API_DIR / "main.py"),
    "--name", "voxecho-backend",
    "--onefile",
    "--console",
    "--clean",
    "--noconfirm",
    # 引擎模块所在目录（provider.py / stt_engine.py / volcengine_asr.py / normalize.py）
    "--paths", str(ENGINE_DIR),
    "--paths", str(API_DIR),
    # edge-tts 的证书与数据
    "--collect-all", "edge_tts",
    "--collect-all", "provider",
    # 隐藏导入（动态 import 的模块）
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "websockets.asyncio",
    "--hidden-import", "websockets.legacy",
]

print("PyInstaller 参数:")
for a in args:
    print(" ", a)

PyInstaller.__main__.run(args)

# 复制产物到 src-tauri/binaries/（带 Tauri externalBin 的 triple 后缀）
dist_exe = ROOT / "dist" / "voxecho-backend.exe"
target = ROOT.parent / "src-tauri" / "binaries" / "voxecho-backend-x86_64-pc-windows-msvc.exe"
if dist_exe.exists():
    shutil.copy2(dist_exe, target)
    print(f"\n✅ sidecar 已复制到: {target} ({dist_exe.stat().st_size/1024/1024:.1f} MB)")
else:
    print(f"\n❌ 未找到产物: {dist_exe}")
    sys.exit(1)
