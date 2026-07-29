#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

mod capture;

static CLICK_THROUGH: AtomicBool = AtomicBool::new(false);

fn set_click_through_state(app: &tauri::AppHandle, enabled: bool) {
    CLICK_THROUGH.store(enabled, Ordering::SeqCst);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_ignore_cursor_events(enabled);
        let _ = window.eval(&format!(
            "window.onClickThrough && window.onClickThrough({enabled})"
        ));
    }
}

fn toggle_click_through(app: &tauri::AppHandle) {
    set_click_through_state(app, !CLICK_THROUGH.load(Ordering::SeqCst));
}

#[tauri::command]
fn set_click_through(app: tauri::AppHandle, enabled: bool) {
    set_click_through_state(&app, enabled);
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.exit(0);
}

/// 调整窗口大小（拖动手柄用），单位是逻辑像素。
#[tauri::command]
fn resize_window(window: tauri::WebviewWindow, width: f64, height: f64) {
    let w = width.clamp(200.0, 800.0);
    let h = height.clamp(300.0, 1100.0);
    let _ = window.set_size(tauri::LogicalSize::new(w, h));
}

// ---------- 桌宠聊天（原生 HTTP，绕过 WebView CORS 限制） ----------

/// 探活：GET /pet/health，成功返回响应文本，失败返回错误描述。
#[tauri::command]
async fn pet_health(base_url: String, api_key: String) -> Result<String, String> {
    let url = format!("{}/desktop_pet/pet/health", base_url.trim_end_matches('/'));
    let resp = reqwest::Client::new()
        .get(&url)
        .header("X-API-Key", &api_key)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if status.is_success() {
        Ok(text)
    } else {
        Err(format!("HTTP {status}: {text}"))
    }
}

/// 管道模式对话：POST AstrBot open API /api/v1/chat（绝对 URL 由前端给出），
/// SSE 帧原样以 "pet-chat" 事件推给前端。
#[tauri::command]
async fn pet_open_chat(
    window: tauri::WebviewWindow,
    url: String,
    api_key: String,
    message: String,
    session_id: String,
    username: String,
    attachment_id: Option<String>,
    provider: Option<String>,
) -> Result<(), String> {
    use tauri::Emitter;
    tauri::async_runtime::spawn(async move {
        // 带图时 message 为富文本段列表（attachment_id 需先经 /api/v1/file 上传换取）
        let attachment_id = attachment_id.filter(|s| !s.is_empty());
        let msg = match &attachment_id {
            Some(aid) => serde_json::json!([
                {"type": "plain", "text": message},
                {"type": "image", "attachment_id": aid}
            ]),
            None => serde_json::Value::String(message),
        };
        let mut body = serde_json::json!({
            "message": msg,
            "session_id": session_id,
            "username": username,
        });
        // 强制指定 provider（识图请求需切到 modalities 含 image 的模型）
        if let Some(p) = provider.filter(|s| !s.is_empty()) {
            body["selected_provider"] = serde_json::Value::String(p);
        }

        let client = reqwest::Client::new();
        let resp = match client
            .post(&url)
            .header("Content-Type", "application/json")
            .header("X-API-Key", &api_key)
            .json(&body)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                let _ = window.emit(
                    "pet-chat",
                    serde_json::json!({"type": "connect_error", "message": e.to_string()}),
                );
                return;
            }
        };

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            let _ = window.emit(
                "pet-chat",
                serde_json::json!({"type": "connect_error", "message": format!("HTTP {status}: {text}")}),
            );
            return;
        }

        use futures_util::StreamExt;
        let mut stream = resp.bytes_stream();
        let mut buf = String::new();
        while let Some(chunk) = stream.next().await {
            match chunk {
                Ok(bytes) => {
                    buf.push_str(&String::from_utf8_lossy(&bytes));
                    while let Some(idx) = buf.find("\n\n") {
                        let frame = buf[..idx].trim().to_string();
                        buf.drain(..idx + 2);
                        // ": heartbeat" 等注释帧没有 data: 前缀，自然被跳过
                        if let Some(data) = frame.strip_prefix("data:") {
                            if let Ok(json) =
                                serde_json::from_str::<serde_json::Value>(data.trim())
                            {
                                let _ = window.emit("pet-chat", json);
                            }
                        }
                    }
                }
                Err(e) => {
                    let _ = window.emit(
                        "pet-chat",
                        serde_json::json!({"type": "connect_error", "message": e.to_string()}),
                    );
                    return;
                }
            }
        }
        let _ = window.emit("pet-chat", serde_json::json!({"type": "stream_end"}));
    });
    Ok(())
}

/// TTS 合成：POST 插件 /pet/tts，返回响应文本（JSON，含 base64 音频）。
#[tauri::command]
async fn pet_tts(url: String, api_key: String, text: String) -> Result<String, String> {
    let resp = reqwest::Client::new()
        .post(&url)
        .header("Content-Type", "application/json")
        .header("X-API-Key", &api_key)
        .json(&serde_json::json!({"text": text}))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if status.is_success() {
        Ok(text)
    } else {
        Err(format!("HTTP {status}: {text}"))
    }
}

/// 文件上传：POST open API /api/v1/file（multipart），返回响应文本（含 attachment_id）。
/// 桌面感知截图上传用；要求 API Key 带 file scope。
#[tauri::command]
async fn pet_upload_file(
    url: String,
    api_key: String,
    filename: String,
    content_type: String,
    data_b64: String,
) -> Result<String, String> {
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(&data_b64)
        .map_err(|e| e.to_string())?;
    let part = reqwest::multipart::Part::bytes(bytes)
        .file_name(filename)
        .mime_str(&content_type)
        .map_err(|e| e.to_string())?;
    let form = reqwest::multipart::Form::new().part("file", part);
    let resp = reqwest::Client::new()
        .post(&url)
        .header("X-API-Key", &api_key)
        .multipart(form)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if status.is_success() {
        Ok(text)
    } else {
        Err(format!("HTTP {status}: {text}"))
    }
}

/// 通用 GET（带 API Key），返回响应文本。壳端拉取插件配置等场景用。
#[tauri::command]
async fn pet_get(url: String, api_key: String) -> Result<String, String> {
    let resp = reqwest::Client::new()
        .get(&url)
        .header("X-API-Key", &api_key)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if status.is_success() {
        Ok(text)
    } else {
        Err(format!("HTTP {status}: {text}"))
    }
}

/// 通用 POST JSON（带 API Key），返回响应文本。壳端状态上报等场景用。
#[tauri::command]
async fn pet_post_json(url: String, api_key: String, body: serde_json::Value) -> Result<String, String> {
    let resp = reqwest::Client::new()
        .post(&url)
        .header("Content-Type", "application/json")
        .header("X-API-Key", &api_key)
        .json(&body)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if status.is_success() {
        Ok(text)
    } else {
        Err(format!("HTTP {status}: {text}"))
    }
}

/// 桌面感知：抓取当前前台窗口画面（WGC 进程级），返回 JPEG base64 与窗口信息。
#[tauri::command]
fn capture_window() -> Result<capture::CaptureResult, String> {
    capture::capture_foreground()
}

// ---------- 态势感知（主动对话用） ----------

#[derive(serde::Serialize)]
struct SystemContext {
    idle_seconds: u64,
    foreground_title: String,
    foreground_process: String,
    is_fullscreen: bool,
}

#[tauri::command]
fn get_system_context() -> Result<SystemContext, String> {
    use windows::Win32::Foundation::{CloseHandle, RECT};
    use windows::Win32::Graphics::Gdi::{
        GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTOPRIMARY,
    };
    use windows::Win32::System::SystemInformation::GetTickCount;
    use windows::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
        PROCESS_QUERY_LIMITED_INFORMATION,
    };
    use windows::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
    use windows::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowRect, GetWindowTextW, GetWindowThreadProcessId,
    };

    unsafe {
        // 用户空闲时长（秒）：任意键鼠输入都会归零
        let mut lii = LASTINPUTINFO {
            cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32,
            dwTime: 0,
        };
        GetLastInputInfo(&mut lii)
            .ok()
            .map_err(|e| e.to_string())?;
        let idle_seconds = GetTickCount().wrapping_sub(lii.dwTime) as u64 / 1000;

        // 前台窗口标题 / 进程名 / 是否全屏
        let hwnd = GetForegroundWindow();
        let mut foreground_title = String::new();
        let mut foreground_process = String::new();
        let mut is_fullscreen = false;
        if !hwnd.is_invalid() {
            let mut tbuf = [0u16; 512];
            let n = GetWindowTextW(hwnd, &mut tbuf);
            if n > 0 {
                foreground_title = String::from_utf16_lossy(&tbuf[..n as usize]);
            }
            let mut pid = 0u32;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            if pid != 0 {
                if let Ok(hproc) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
                    let mut pbuf = [0u16; 512];
                    let mut len = pbuf.len() as u32;
                    if QueryFullProcessImageNameW(
                        hproc,
                        PROCESS_NAME_WIN32,
                        windows::core::PWSTR(pbuf.as_mut_ptr()),
                        &mut len,
                    )
                    .is_ok()
                    {
                        let full = String::from_utf16_lossy(&pbuf[..len as usize]);
                        foreground_process =
                            full.rsplit('\\').next().unwrap_or(&full).to_string();
                    }
                    let _ = CloseHandle(hproc);
                }
            }
            let mut rc = RECT::default();
            if GetWindowRect(hwnd, &mut rc).is_ok() {
                let mon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTOPRIMARY);
                let mut mi = MONITORINFO {
                    cbSize: std::mem::size_of::<MONITORINFO>() as u32,
                    ..Default::default()
                };
                if GetMonitorInfoW(mon, &mut mi).as_bool() {
                    let m = mi.rcMonitor;
                    is_fullscreen = rc.left <= m.left
                        && rc.top <= m.top
                        && rc.right >= m.right
                        && rc.bottom >= m.bottom;
                }
            }
        }
        Ok(SystemContext {
            idle_seconds,
            foreground_title,
            foreground_process,
            is_fullscreen,
        })
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            set_click_through,
            quit_app,
            resize_window,
            pet_health,
            pet_open_chat,
            pet_tts,
            pet_upload_file,
            pet_get,
            pet_post_json,
            capture_window,
            get_system_context
        ])
        .setup(|app| {
            #[cfg(debug_assertions)]
            if let Some(w) = app.get_webview_window("main") {
                w.open_devtools();
            }
            // 全局快捷键 Ctrl+Shift+P 切换点击穿透（穿透开启后窗口收不到事件，只能靠它切回）
            app.global_shortcut().on_shortcut(
                "CmdOrControl+Shift+P",
                move |app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle_click_through(app);
                    }
                },
            )?;

            // 系统托盘
            let pass =
                MenuItem::with_id(app, "pass", "切换点击穿透 (Ctrl+Shift+P)", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&pass, &quit])?;
            let mut tray = TrayIconBuilder::with_id("main-tray")
                .menu(&menu)
                .tooltip("AstrBotPet");
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.on_menu_event(|app, event| match event.id.as_ref() {
                "quit" => app.exit(0),
                "pass" => toggle_click_through(app),
                _ => {}
            })
            .build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running pet shell");
}
