; FewType NSIS 卸载钩子
; 卸载前：结束后端 sidecar 与主程序，否则 fewtype-backend.exe 被占用 → 卸载删不掉文件、安装目录残留
; 卸载后：删除 %APPDATA%\com.rayanyway.fewtype（含 API Key 与风格 Prompt）
;         并顺带清理改名前的旧目录 %APPDATA%\com.rayanyway.voxecho，确保不留含密钥的残留
; 目录与 tauri.conf.json 的 identifier 一致
!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToLog 'taskkill /IM fewtype-backend.exe /F'
  nsExec::ExecToLog 'taskkill /IM fewtype.exe /F'
  Sleep 500
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  RMDir /r "$APPDATA\com.rayanyway.fewtype"
  RMDir /r "$APPDATA\com.rayanyway.voxecho"
!macroend
