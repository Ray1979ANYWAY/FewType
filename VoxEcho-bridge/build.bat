@echo off
cd /d "%~dp0"
echo === VoxEcho bridge build ===
where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: python not found.
  pause
  exit /b 1
)
python -m pip install -U pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: pip failed.
  pause
  exit /b 1
)

set ICON_FILE=
if exist "VoxEcho.ico" set ICON_FILE=VoxEcho.ico
if exist "icon\VoxEcho.ico" set ICON_FILE=icon\VoxEcho.ico

set ICON_ARGS=
set ADD_ICON=
if defined ICON_FILE (
  set ICON_ARGS=--icon %ICON_FILE%
  set ADD_ICON=--add-data %ICON_FILE%;.
)

echo.
echo Mode: onefile (do NOT run rcedit on onefile - it breaks PKG archive)
echo.

set VERSION_ARGS=
if exist "version_info.txt" set VERSION_ARGS=--version-file version_info.txt

python -m PyInstaller --noconfirm --clean --onefile --windowed --name VoxEcho-bridge %ICON_ARGS% %ADD_ICON% %VERSION_ARGS% --add-data "server.py;." --add-data "kofi_badge.png;." --hidden-import edge_tts --hidden-import flask --hidden-import flask_cors --hidden-import pystray --hidden-import PIL --collect-binaries lameenc --collect-binaries sounddevice launcher.py
if errorlevel 1 (
  echo ERROR: PyInstaller failed.
  pause
  exit /b 1
)

if defined ICON_FILE copy /Y "%ICON_FILE%" "dist\VoxEcho.ico" >nul

echo.
echo OK: dist\VoxEcho-bridge.exe
echo.
echo NOTE:
echo - Window/tray icons load VoxEcho.ico at runtime (keep ico next to exe for best quality).
echo - Explorer exe icon embeds full multi-size set from the ico (16-256, all present). Do NOT use rcedit on onefile.
echo.
pause
