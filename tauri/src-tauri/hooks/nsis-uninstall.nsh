; VoxEcho NSIS 卸载钩子
; 卸载完成后删除 %APPDATA%\com.rayanyway.voxecho（含 API Key 与风格 Prompt）
; 该目录与 tauri.conf.json 的 identifier 一致，确保不留含密钥的残留
!macro NSIS_HOOK_POSTUNINSTALL
  RMDir /r "$APPDATA\com.rayanyway.voxecho"
!macroend
