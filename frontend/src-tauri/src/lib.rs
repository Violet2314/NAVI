// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
use std::sync::Mutex;
use tauri::Manager;
use tauri::Emitter;

/// 持有 sidecar 子进程句柄，确保退出时能被 kill
struct BackendProcess(Mutex<Option<tauri_plugin_shell::process::CommandChild>>);

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

/// 获取全局光标位置（屏幕坐标），用于 Live2D 全屏鼠标跟踪
#[tauri::command]
fn get_cursor_position() -> (i32, i32) {
    #[cfg(target_os = "windows")]
    {
        use std::mem::MaybeUninit;

        #[repr(C)]
        struct POINT {
            x: i32,
            y: i32,
        }

        extern "system" {
            fn GetCursorPos(lp_point: *mut POINT) -> i32;
        }

        unsafe {
            let mut point = MaybeUninit::<POINT>::uninit();
            if GetCursorPos(point.as_mut_ptr()) != 0 {
                let point = point.assume_init();
                return (point.x, point.y);
            }
        }
        (0, 0)
    }
    #[cfg(not(target_os = "windows"))]
    {
        (0, 0)
    }
}

/// 获取主显示器尺寸
#[tauri::command]
fn get_screen_size() -> (i32, i32) {
    #[cfg(target_os = "windows")]
    {
        extern "system" {
            fn GetSystemMetrics(n_index: i32) -> i32;
        }
        unsafe {
            let w = GetSystemMetrics(0);
            let h = GetSystemMetrics(1);
            return (w, h);
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        (1920, 1080)
    }
}

/// 启动 Python 后端 sidecar 进程
fn spawn_backend(app: &tauri::AppHandle) -> Option<tauri_plugin_shell::process::CommandChild> {
    use tauri_plugin_shell::ShellExt;
    use std::path::PathBuf;

    let shell = app.shell();

    // Tauri 2 sidecar 在 NSIS 安装后可能被重命名为不带 target-triple 后缀的名字。
    // 先尝试标准 sidecar 名称，失败则尝试当前 exe 目录下的 navi-backend.exe
    let sidecar_command = match shell.sidecar("navi-backend") {
        Ok(cmd) => cmd,
        Err(_) => {
            // sidecar 查找失败，尝试直接使用当前 exe 同目录下的 navi-backend.exe
            let exe_dir = std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|d| d.to_path_buf()))
                .unwrap_or_default();
            let backend_path = exe_dir.join("navi-backend.exe");
            eprintln!("sidecar not found via Tauri, trying: {}", backend_path.display());
            shell.command(backend_path)
        }
    };

    let (mut rx, child) = match sidecar_command.spawn() {
        Ok((rx, child)) => {
            println!("Navi backend started (PID: {})", child.pid());
            (rx, child)
        }
        Err(e) => {
            eprintln!("Navi backend failed to start: {}", e);
            return None;
        }
    };

    // 异步监听后端输出
    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;

        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    println!("[navi-backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[navi-backend:err] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(status) => {
                    println!("Navi backend exited (code: {:?})", status.code);
                    let _ = app_handle.emit("backend-exited", status.code);
                    break;
                }
                CommandEvent::Error(err) => {
                    eprintln!("Navi backend error: {}", err);
                    break;
                }
                _ => {}
            }
        }
    });

    Some(child)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![greet, get_cursor_position, get_screen_size])
        .setup(|app| {
            use tauri::{
                image::Image,
                menu::{Menu, MenuItem},
                tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
            };

            // Start Python backend sidecar
            let backend_child = spawn_backend(&app.handle());
            if let Some(child) = backend_child {
                let state = app.state::<BackendProcess>();
                *state.0.lock().unwrap() = Some(child);
            } else {
                eprintln!("Backend failed to start, frontend will run without API");
            }

            // Tray menu
            let show_item = MenuItem::with_id(app, "show", "Show Navi", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &quit_item])?;

            let icon = Image::from_path(
                app.path().resource_dir()
                    .unwrap_or_else(|_| std::path::PathBuf::from("."))
                    .join("icons/icon.png")
            ).unwrap_or_else(|_| app.default_window_icon().unwrap().clone());

            TrayIconBuilder::new()
                .icon(icon)
                .menu(&menu)
                .tooltip("Navi - AI Desktop Companion")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                    "quit" => {
                        if let Some(state) = app.try_state::<BackendProcess>() {
                            if let Some(child) = state.0.lock().unwrap().take() {
                                println!("Shutting down Navi backend...");
                                let _ = child.kill();
                            }
                        }
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
                        let app = tray.app_handle();
                        if let Some(win) = app.get_webview_window("main") {
                            if win.is_visible().unwrap_or(false) {
                                let _ = win.hide();
                            } else {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}