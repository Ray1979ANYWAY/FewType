//! FewType Tauri 壳
//!
//! 职责：
//! - 承载 React 前端（dist 静态资源，Tauri WebView）
//! - 随应用启动拉起 Python FastAPI 后端（端口 5010），托盘「退出」时回收
//!   - 开发模式（tauri dev）：spawn 系统 python 运行 ../backend/api/main.py
//!   - 生产模式（tauri build）：externalBin sidecar（binaries/fewtype-backend-<triple>.exe）
//! - 系统托盘常驻：关窗最小化到托盘（不退出），托盘菜单「显示主窗口 / 退出」
//! - 单实例锁：重复启动时唤起已有窗口
//! - 前端通过 http://127.0.0.1:5010 与后端通信（见 frontend/src/api.ts）

use std::process::Command;
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, WindowEvent,
};
use tauri_plugin_shell::ShellExt;

/// 后端子进程句柄（托盘「退出」时 kill）
struct BackendProcess(Mutex<Option<BackendChild>>);

enum BackendChild {
    /// 开发模式：系统 python 进程
    Std(std::process::Child),
    /// 生产模式：sidecar 进程（tauri-plugin-shell）
    Sidecar(tauri_plugin_shell::process::CommandChild),
}

/// 持有托盘图标，防止 drop 后被系统移除
struct TrayHandle(TrayIcon);

const BACKEND_PORT: &str = "5010";

/// WebView2 Runtime 是否已安装（查注册表，与 NSIS 安装器同一 GUID 口径）
fn webview2_installed() -> bool {
    let keys = [
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"HKCU\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    ];
    for k in keys {
        if let Ok(out) = Command::new("reg").args(["query", k, "/v", "pv"]).output() {
            if out.status.success() {
                return true;
            }
        }
    }
    false
}

/// 免安装版环境自愈：WebView2 缺失时静默运行同目录 WebView2Setup.exe 安装，
/// 让用户解压后双击即可无感使用（NSIS 安装版由安装器负责，此处作为兜底，已装则直接跳过）。
/// 轮询等待最多 120 秒（下载+静默安装），断网超时则放弃、照常启动，不无限卡住。
fn ensure_webview2() {
    if webview2_installed() {
        return;
    }
    let setup_path = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join("WebView2Setup.exe")))
        .unwrap_or_default();
    if !setup_path.exists() {
        return;
    }
    if let Ok(mut child) = Command::new(&setup_path).args(["/silent", "/install"]).spawn() {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(120);
        loop {
            if webview2_installed() {
                let _ = child.kill();
                break;
            }
            if std::time::Instant::now() > deadline {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(2000));
        }
    }
}

/// 开发模式后端命令（依赖本机 Python 环境，可通过环境变量 FEWTYPE_PYTHON 覆盖）
fn dev_backend_command() -> (String, Vec<String>) {
    let manifest_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let backend_py = manifest_dir.join("../backend/api/main.py");
    // 本机验证过带完整依赖的解释器；FEWTYPE_PYTHON 可显式指定
    let python = std::env::var("FEWTYPE_PYTHON")
        .unwrap_or_else(|_| "D:/Program Files/Python/Python310/python.exe".to_string());
    (
        python,
        vec![
            backend_py.to_string_lossy().to_string(),
            "--port".to_string(),
            BACKEND_PORT.to_string(),
        ],
    )
}

fn spawn_backend(app: &tauri::App) {
    let child = if tauri::is_dev() {
        // 开发：直接跑仓库里的 main.py
        let (prog, args) = dev_backend_command();
        match Command::new(&prog).args(&args).spawn() {
            Ok(c) => Some(BackendChild::Std(c)),
            Err(e) => {
                eprintln!("[fewtype] 后端启动失败 ({prog}): {e}");
                None
            }
        }
    } else {
        // 生产：externalBin sidecar（PyInstaller onefile）
        match app.shell().sidecar("fewtype-backend") {
            Ok(sidecar_cmd) => match sidecar_cmd.args(["--port", BACKEND_PORT]).spawn() {
                Ok((_rx, child)) => Some(BackendChild::Sidecar(child)),
                Err(e) => {
                    eprintln!("[fewtype] sidecar 启动失败: {e}");
                    None
                }
            },
            Err(e) => {
                eprintln!("[fewtype] sidecar 解析失败: {e}");
                None
            }
        }
    };

    if let Some(child) = child {
        app.manage(BackendProcess(Mutex::new(Some(child))));
    }
}

/// 更新安装前结束后端 sidecar（前端在 download() 完成后、install() 之前调用）。
/// NSIS 要覆盖 fewtype-backend.exe，进程仍存活会弹「无法打开要写入的文件」中止更新；
/// 主进程 process::exit(0) 不会自动回收 sidecar，必须显式调用本命令。
/// 在 Chrome 中打开 chrome://extensions/（Tauri shell 对 chrome:// scheme 支持不好，直接调系统）。
#[tauri::command]
fn open_chrome_extensions() {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        let candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            &format!(r"{}\Google\Chrome\Application\chrome.exe", std::env::var("LOCALAPPDATA").unwrap_or_default()),
        ];
        for p in &candidates {
            if std::path::Path::new(p).exists() {
                let _ = std::process::Command::new(p)
                    .args(["--new-window", "chrome://extensions/"])
                    .creation_flags(0x08000000)
                    .spawn();
                return;
            }
        }
        // fallback：让系统默认浏览器处理
        let _ = std::process::Command::new("cmd")
            .args(["/C", "start", "", "chrome://extensions/"])
            .creation_flags(0x08000000)
            .spawn();
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = std::process::Command::new("xdg-open").arg("chrome://extensions/").status();
    }
}

#[tauri::command]
fn kill_backend(app: AppHandle) {
    if let Some(state) = app.try_state::<BackendProcess>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(child) = guard.take() {
                match child {
                    BackendChild::Std(mut c) => {
                        let _ = c.kill();
                        let _ = c.wait();
                    }
                    BackendChild::Sidecar(c) => {
                        // PyInstaller onefile：sidecar 引导器会解压出真正的 Python 子进程，
                        // kill() 只杀引导器、子进程会成孤儿继续跑（表现为"退出后热键仍能录音"）。
                        // 这里用 taskkill /T 杀整棵进程树，并阻塞等待完成后再退出主程序。
                        let pid = c.pid();
                        let _ = std::process::Command::new("taskkill")
                            .args(["/PID", &pid.to_string(), "/T", "/F"])
                            .status();
                    }
                }
            }
        }
    }
}

/// 显示并聚焦主窗口（托盘菜单 / 单实例唤起共用）
fn show_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

/// 系统托盘：左键单击显示窗口，右键菜单「显示主窗口 / 退出」
fn setup_tray(app: &tauri::App) -> tauri::Result<()> {
    let show_i = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show_i, &quit_i])?;
    let icon = app
        .default_window_icon()
        .cloned()
        .expect("bundle icon 缺失");

    let tray = TrayIconBuilder::with_id("fewtype-tray")
        .icon(icon)
        .tooltip("FewType 语音处理平台")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => show_main_window(app),
            "quit" => {
                kill_backend(app.clone());
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        })
        .build(app)?;

    app.manage(TrayHandle(tray));
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // 窗口创建前先确保 WebView2（portable 免安装版缺运行库时自动静默安装）
    ensure_webview2();
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        // 单实例锁：重复启动时唤起已有窗口并退出新实例
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main_window(app);
        }))
        // 自动更新：检查 GitHub Releases manifest（见 tauri.conf.json plugins.updater）
        // 注意：安装前杀后端由前端流程完成——download() 完成后 invoke("kill_backend")，再 install()
        .plugin(tauri_plugin_updater::Builder::new().build())
        // 更新安装完成后重启应用（@tauri-apps/plugin-process 的 relaunch）
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![kill_backend, open_chrome_extensions])
        .setup(|app| {
            spawn_backend(app);
            setup_tray(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // 关窗 → 隐藏到托盘（常驻）；完全退出走托盘菜单「退出」
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running FewType");
}
