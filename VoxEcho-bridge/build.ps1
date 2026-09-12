$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host "=== VoxEcho bridge build ==="
python -m pip install -U pip
python -m pip install -r requirements.txt
$icon = @()
if (Test-Path "VoxEcho.ico") { $icon = @("--icon", "VoxEcho.ico") }
elseif (Test-Path "icon\VoxEcho.ico") { $icon = @("--icon", "icon\VoxEcho.ico") }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name VoxEcho-bridge @icon --add-data "server.py;." --hidden-import edge_tts --hidden-import flask --hidden-import flask_cors --hidden-import pystray --hidden-import PIL launcher.py
Write-Host "OK: dist\VoxEcho-bridge.exe"
