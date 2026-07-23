#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

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

#[derive(serde::Deserialize)]
struct HistoryItem {
    role: String,
    content: String,
}

/// 发起一次桌宠对话：POST SSE 端点，把每一帧原样以 "pet-chat" 事件推给前端。
#[tauri::command]
async fn pet_chat(
    window: tauri::WebviewWindow,
    base_url: String,
    api_key: String,
    message: String,
    history: Vec<HistoryItem>,
) -> Result<(), String> {
    use tauri::Emitter;
    tauri::async_runtime::spawn(async move {
        let url = format!("{}/desktop_pet/pet/chat", base_url.trim_end_matches('/'));
        let history: Vec<serde_json::Value> = history
            .iter()
            .map(|h| serde_json::json!({"role": h.role, "content": h.content}))
            .collect();
        let body = serde_json::json!({"message": message, "history": history});

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
) -> Result<(), String> {
    use tauri::Emitter;
    tauri::async_runtime::spawn(async move {
        let body = serde_json::json!({
            "message": message,
            "session_id": session_id,
            "username": username,
        });

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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            set_click_through,
            quit_app,
            resize_window,
            pet_chat,
            pet_health,
            pet_open_chat,
            pet_tts
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
