# VoxEcho 图标用法

## 1. 准备一个 .ico（给 exe 用）

Windows 资源管理器里的 exe 图标必须是 **.ico**，且最好包含多尺寸：
16, 32, 48, 256（可再加 20, 24, 64）。

若你只有 png，可用在线工具或 ImageMagick 合成：

```bat
magick convert VoxEcho-16.png VoxEcho-32.png VoxEcho-48.png VoxEcho-256.png VoxEcho.ico
```

把最终文件放到本目录：

```
ebooks-tts-bridge/
  VoxEcho.ico          ← 推荐
  icon/
    VoxEcho-16.png
    VoxEcho-32.png
    ...
```

## 2. 打进 exe（PyInstaller）

build.bat 已使用：

```
--icon VoxEcho.ico
--name VoxEcho-bridge
```

打包后：`dist\VoxEcho-bridge.exe` 在资源管理器中即显示该图标。

若改图标后仍看到旧图：删掉 `build/`、`dist/` 再打包，或清理图标缓存。

## 3. 托盘图标

launcher 会按顺序尝试：

1. 与 exe 同目录的 `VoxEcho.ico` / `icon/VoxEcho-32.png` 等
2. 打包进 exe 的资源（_MEIPASS）
3. 都没有则用程序生成的默认圆点

托盘在 Windows 上用 16/32 即可，过大也会被缩小。

## 4. 桌面快捷方式

创建 .lnk 时会把 Target 指到 exe，快捷方式自动继承 **exe 的图标**，无需再设。


## 任务栏发糊：真实原因与现状

**已核实**：PyInstaller 的 `--icon` 会把 ico 的**完整多尺寸**（16/24/32/48/64/128/256）全部嵌入 exe，
不存在"只嵌入 16/32"的限制（已用资源枚举实测确认 7 档齐全）。

之前任务栏发糊的真正原因：launcher.py 的 `apply_window_icons()` 先提交 256px 高清图（iconphoto），
紧接着又调用 `iconbitmap` 用 ico 里的 16px 小帧**覆盖**了高清图，任务栏取到小图被放大所以发糊。
**该问题已修复**（iconbitmap 改为仅当 iconphoto 失败时才兜底）。

### 现在的图标保证

| 目标 | 来源 | 状态 |
|------|------|------|
| 资源管理器里的 exe 图标 | `--icon` 内嵌 7 尺寸 | ✅ 清晰 |
| 任务栏/窗口图标 | launcher 运行时 `iconphoto` 提交 256px 高清帧 | ✅ 清晰（已修复） |
| 托盘图标 | launcher 从 ico 取 64px 帧 | ✅ 清晰 |

运行时图标从 **exe 内打包的 VoxEcho.ico**（`--add-data`）读取，发布单 exe 即可，不强制 exe 旁再放 ico。
（exe 旁放一份 ico 也无害，可作运行时兜底。）

**不需要**在 build 后再跑任何图标修复脚本——build.bat 只做 PyInstaller 打包，不调用 rcedit。
若改了图标仍看到旧图，删掉 `build/`、`dist/` 重新打包，或清理 Windows 图标缓存。

> 附：`tools/fix_exe_icon_safe.py` 是**唯一**能安全替换 onefile 图标的工具（注入后原样拼回 PKG）。
> 仅当以后想给 exe 换新图标时才用它，日常构建不需要。


## 重要：不要对 onefile exe 使用 rcedit

对 PyInstaller **`--onefile`** 生成的 exe 运行 rcedit / Resource Hacker / UpdateResource 改图标，
会重写整个 exe 并丢弃末尾的 **PKG 归档**，启动时报：

```text
Could not load PyInstaller's embedded PKG archive from the executable
```

正确做法：

| 目标 | 做法 |
|------|------|
| 能跑且图标清晰的单文件 | `build.bat`（onefile，图标已内嵌 7 尺寸，**不要** rcedit） |
| 换新图标（安全） | 用 `tools/fix_exe_icon_safe.py`（注入 + 拼回 PKG，不破坏归档） |
| 想用 rcedit 改图标 | 改用 `build_onedir.bat` 的 onedir 模式（无 PKG 尾块，rcedit 安全） |

已损坏的 onefile 无法修复，请删掉 dist 后重新 `build.bat`（不要跑 rcedit 类图标修复）。
