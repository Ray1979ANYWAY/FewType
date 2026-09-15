@echo off
cd /d "%~dp0"
echo === FewType bridge build (onedir, full icon safe) ===
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
if exist "FewType.ico" set ICON_FILE=FewType.ico
if exist "icon\FewType.ico" set ICON_FILE=icon\FewType.ico

set ICON_ARGS=
set ADD_ICON=
if defined ICON_FILE (
  set ICON_ARGS=--icon %ICON_FILE%
  set ADD_ICON=--add-data %ICON_FILE%;.
)

echo [1/2] PyInstaller onedir...
set VERSION_ARGS=
if exist "version_info.txt" set VERSION_ARGS=--version-file version_info.txt
python -m PyInstaller --noconfirm --clean --onedir --windowed --name FewType-bridge %ICON_ARGS% %ADD_ICON% %VERSION_ARGS% --add-data "server.py;." --add-data "kofi_badge.png;." --hidden-import edge_tts --hidden-import flask --hidden-import flask_cors --hidden-import pystray --hidden-import PIL --collect-binaries lameenc --collect-binaries sounddevice launcher.py
if errorlevel 1 (
  echo ERROR: PyInstaller failed.
  pause
  exit /b 1
)

echo [2/2] Optional full ICO via rcedit (safe on onedir)...
if defined ICON_FILE (
  python tools\fix_exe_icon.py "dist\FewType-bridge\FewType-bridge.exe" "%ICON_FILE%"
  if errorlevel 1 (
    echo WARNING: rcedit not applied. Put rcedit-x64.exe in tools\ and re-run.
  )
  copy /Y "%ICON_FILE%" "dist\FewType-bridge\FewType.ico" >nul
)

echo.
echo OK: run dist\FewType-bridge\FewType-bridge.exe
echo Ship the whole dist\FewType-bridge\ folder to users.
pause
