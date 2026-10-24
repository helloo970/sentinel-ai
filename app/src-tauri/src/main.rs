// No console window in the release build
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Tauri shell for SENTINEL AI.
//!
//! - starts the Python backend (development: venv, release: sidecar) on 127.0.0.1
//! - creates a random session token on every start and hands it only to its own frontend
//! - the tray menu and the global hotkey (Ctrl+Alt+End) trigger the emergency stop directly in the backend
//! - a triple clap (detected in the backend) brings the window to the front

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

enum BackendChild {
    Dev(std::process::Child),
    Sidecar(CommandChild),
}

struct Backend {
    port: u16,
    token: String,
    child: Mutex<Option<BackendChild>>,
}

#[tauri::command]
fn backend_config(state: State<'_, Backend>) -> Value {
    json!({ "port": state.port, "token": state.token })
}

/// Brings the main window to the front (e.g. after a triple clap).
#[tauri::command]
fn show_main_window(app: AppHandle) {
    show_main(&app);
}

fn app_exe() -> String {
    std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_default()
}

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("OS random generator unavailable");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(8765)
}

fn data_dir() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    base.join("SentinelAI")
}

fn log_file() -> Option<File> {
    let dir = data_dir();
    fs::create_dir_all(&dir).ok()?;
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("backend.log"))
        .ok()
}

fn spawn_backend(app: &AppHandle, port: u16, token: &str) -> Result<BackendChild, String> {
    let port_text = port.to_string();
    let parent_pid = std::process::id().to_string();
    if cfg!(debug_assertions) {
        // Development: start the backend from the venv in the repo
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..");
        let backend_dir = repo.join("backend");
        let python = std::env::var_os("SENTINEL_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| backend_dir.join(".venv").join("Scripts").join("python.exe"));
        let mut command = std::process::Command::new(python);
        command
            .args(["-m", "sentinel"])
            .current_dir(&backend_dir)
            .env("SENTINEL_API_TOKEN", token)
            .env("SENTINEL_PORT", &port_text)
            .env("SENTINEL_PARENT_PID", &parent_pid)
            .env("SENTINEL_APP_EXE", app_exe());
        if let Some(log) = log_file() {
            if let Ok(err) = log.try_clone() {
                command.stderr(err);
            }
            command.stdout(log);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        return command
            .spawn()
            .map(BackendChild::Dev)
            .map_err(|e| e.to_string());
    }

    let (mut events, child) = app
        .shell()
        .sidecar("sentinel-backend")
        .map_err(|e| e.to_string())?
        .env("SENTINEL_API_TOKEN", token)
        .env("SENTINEL_PORT", &port_text)
        .env("SENTINEL_PARENT_PID", &parent_pid)
        .env("SENTINEL_APP_EXE", app_exe())
        .spawn()
        .map_err(|e| e.to_string())?;
    tauri::async_runtime::spawn(async move {
        let mut log = log_file();
        while let Some(event) = events.recv().await {
            if let (Some(file), CommandEvent::Stdout(line) | CommandEvent::Stderr(line)) =
                (log.as_mut(), &event)
            {
                let _ = file.write_all(line);
                let _ = file.write_all(b"\n");
            }
        }
    });
    Ok(BackendChild::Sidecar(child))
}

/// Emergency stop straight to the backend over HTTP – works even when the window is hidden.
fn send_halt(app: &AppHandle, via: &str) {
    let state = app.state::<Backend>();
    let body = json!({ "via": via }).to_string();
    let request = format!(
        "POST /control/halt HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\n\
         Content-Type: application/json\r\nContent-Length: {len}\r\nConnection: close\r\n\r\n{body}",
        port = state.port,
        token = state.token,
        len = body.len(),
    );
    let result = TcpStream::connect(("127.0.0.1", state.port)).and_then(|mut stream| {
        stream.set_read_timeout(Some(Duration::from_secs(3)))?;
        stream.write_all(request.as_bytes())?;
        let mut response = String::new();
        let _ = stream.read_to_string(&mut response);
        Ok(response)
    });
    if result
        .map(|r| !r.starts_with("HTTP/1.1 200"))
        .unwrap_or(true)
    {
        eprintln!("emergency stop request failed");
    }
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn stop_backend(app: &AppHandle) {
    let state = app.state::<Backend>();
    let child = state.child.lock().ok().and_then(|mut guard| guard.take());
    match child {
        Some(BackendChild::Dev(mut child)) => {
            let _ = child.kill();
            let _ = child.wait();
        }
        Some(BackendChild::Sidecar(child)) => {
            let _ = child.kill();
        }
        None => {}
    }
}

fn main() {
    let port = free_port();
    let token = random_token();
    let halt_shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::End);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    if shortcut == &halt_shortcut && event.state() == ShortcutState::Pressed {
                        send_halt(app, "hotkey");
                    }
                })
                .build(),
        )
        .manage(Backend {
            port,
            token: token.clone(),
            child: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![backend_config, show_main_window])
        .setup(move |app| {
            let handle = app.handle().clone();

            show_main(&handle);
            match spawn_backend(&handle, port, &token) {
                Ok(child) => {
                    if let Ok(mut guard) = app.state::<Backend>().child.lock() {
                        *guard = Some(child);
                    }
                }
                Err(error) => eprintln!("backend start failed: {error}"),
            }

            if let Err(error) = app.global_shortcut().register(halt_shortcut) {
                eprintln!("hotkey registration failed: {error}");
            }

            let open = MenuItem::with_id(app, "open", "Open SENTINEL", true, None::<&str>)?;
            let halt = MenuItem::with_id(app, "halt", "EMERGENCY STOP", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let menu = Menu::with_items(app, &[&open, &halt, &separator, &quit])?;
            let mut tray = TrayIconBuilder::with_id("main")
                .tooltip("SENTINEL AI")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open" => show_main(app),
                    "halt" => send_halt(app, "tray"),
                    "quit" => {
                        stop_backend(app);
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
                        show_main(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing only hides the window: monitoring keeps running in the tray
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building SENTINEL AI");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_backend(handle);
        }
    });
}
