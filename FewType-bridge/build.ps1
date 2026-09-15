$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host "=== FewType bridge build ==="
python -m pip install -U pip
python -m pip install -r requirements.txt
$icon = @()
if (Test-Path "FewType.ico") { $icon = @("--icon", "FewType.ico") }
elseif (Test-Path "icon\FewType.ico") { $icon = @("--icon", "icon\FewType.ico") }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name FewType-bridge @icon --add-data "server.py;." --hidden-import edge_tts --hidden-import flask --hidden-import flask_cors --hidden-import pystray --hidden-import PIL launcher.py
Write-Host "OK: dist\FewType-bridge.exe"
