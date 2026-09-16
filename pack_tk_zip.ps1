# ============================================================================
# TK 2.0 发布 zip 打包脚本 —— 扁平结构
#
# 产物：D:\Documents\FewType-<版本>-tk-win64.zip
# zip 根目录结构（用户指定，2026-09-16）：
#   _internal\             PyInstaller onedir 依赖
#   FewType-extension\     浏览器扩展（电子书朗读用，不用的用户可跳过）
#   FewType.ico            程序图标
#   FewType-bridge.exe     主程序（托盘）
#
# 用法：先运行 build_onedir.bat（产出 dist\FewType-bridge\），再运行本脚本。
#   powershell -ExecutionPolicy Bypass -File pack_tk_zip.ps1 [-Version 2.0.5]
# ============================================================================
param([string]$Version = "2.0.5")
$ErrorActionPreference = "Stop"

$bridge = Join-Path $PSScriptRoot "FewType-bridge"
$dist   = Join-Path $bridge "dist\FewType-bridge"
$exe    = Join-Path $dist "FewType-bridge.exe"
if (-not (Test-Path $exe)) {
    Write-Error "未找到 $exe —— 请先运行 build_onedir.bat 完成构建。"
    exit 1
}

# 1) 临时目录：扁平复制 bridge 产物（exe / _internal / ico 直接放根）
$tmp = Join-Path $env:TEMP ("fewtype_tk_pack_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Copy-Item (Join-Path $dist "*") $tmp -Recurse -Force

    # 2) 扩展文件夹放 zip 根
    $ext = Join-Path $PSScriptRoot "FewType-extension"
    if (Test-Path $ext) {
        Copy-Item $ext (Join-Path $tmp "FewType-extension") -Recurse -Force
    } else {
        Write-Warning "未找到 FewType-extension，zip 将不含扩展。"
    }

    # 3) 压缩
    $zip = Join-Path "D:\Documents" ("FewType-" + $Version + "-tk-win64.zip")
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path (Join-Path $tmp "*") -DestinationPath $zip -CompressionLevel Optimal

    Write-Output ("OK: " + $zip + "  " + [math]::Round((Get-Item $zip).Length / 1MB, 1) + " MB")
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
