# sidecar 产物目录

生产模式下，Tauri 壳（`src/lib.rs`）会在**可执行文件同目录**查找并拉起
`fewtype-backend.exe`（PyInstaller onefile 打包的 FastAPI 后端）。

本目录用于暂存 PyInstaller 构建产物（构建命令见仓库根文档），
最终随安装包一起分发到安装目录。

开发模式（`tauri dev`）不需要本目录：壳直接 spawn 系统 Python
运行 `../backend/api/main.py`。
