@echo off
chcp 65001 >nul
title VoxEcho 卸载
echo.
echo ============================================
echo   VoxEcho (tkinter 版) 卸载
echo ============================================
echo.
taskkill /f /im VoxEcho-bridge.exe >nul 2>&1
echo [1/2] 正在删除本地配置（含 API Key 与风格 Prompt）...
rd /s /q "%APPDATA%\VoxEcho" 2>nul
echo [2/2] 正在删除程序目录...
start "" cmd /c "timeout /t 2 /nobreak >nul & rd /s /q ""%~dp0"" >nul 2>&1 & exit"
echo.
echo 卸载完成，本窗口即将关闭。
timeout /t 2 /nobreak >nul
