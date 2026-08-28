#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::OnceLock;

use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

mod capture;

static CLICK_THROUGH: AtomicBool = AtomicBool::new(false); // 手动穿透（菜单/快捷键）
static AUTO_PASS: AtomicBool = AtomicBool::new(false); // 检测结果：前台窗口全屏-like
static AUTO_PASS_ENABLED: AtomicBool = AtomicBool::new(true); // 功能开关（托盘可切）
static AUTO_PASS_SUPPRESS: AtomicBool = AtomicBool::new(false); // 全屏中用户主动恢复交互
static PASS_EFFECTIVE: AtomicBool = AtomicBool::new(false); // 已应用的生效态

/// 生效态 = 手动 || (功能开启 && 前台全屏 && 未被压制)；仅变化时应用并回调前端。
fn apply_pass_through(app: &tauri::AppHandle) {
    let manual = CLICK_THROUGH.load(Ordering::SeqCst);
    let auto = AUTO_PASS.load(Ordering::SeqCst)
        && AUTO_PASS_ENABLED.load(Ordering::SeqCst)
        && !AUTO_PASS_SUPPRESS.load(Ordering::SeqCst);
    let effective = manual || auto;
    if PASS_EFFECTIVE.swap(effective, Ordering::SeqCst) == effective {
        return;
    }
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_ignore_cursor_events(effective);
        let _ = window.eval(&format!(
            "window.onClickThrough && window.onClickThrough({effective}, {})",
            effective && auto && !manual
        ));
    }
}

fn set_click_through_state(app: &tauri::AppHandle, enabled: bool) {
    CLICK_THROUGH.store(enabled, Ordering::SeqCst);
    if enabled {
        AUTO_PASS_SUPPRESS.store(false, Ordering::SeqCst);
    }
    apply_pass_through(app);
}

/// 切换作用于生效态：自动穿透中按下 = 压制自动检测（本次全屏期间恢复交互），
/// 压制在离开全屏后自动复位。
fn toggle_click_through(app: &tauri::AppHandle) {
    if PASS_EFFECTIVE.load(Ordering::SeqCst) {
        CLICK_THROUGH.store(false, Ordering::SeqCst);
        if AUTO_PASS.load(Ordering::SeqCst) {
            AUTO_PASS_SUPPRESS.store(true, Ordering::SeqCst);
        }
        apply_pass_through(app);
    } else {
        set_click_through_state(app, true);
    }
}

// ---------- 前台切换看门狗（置顶 + WebView2 视觉树重断言 + 全屏自动穿透） ----------
// 全屏/最大化的 flip-model 应用（如 Windows 照片查看器）关闭时，DWM 从独立翻转
// 切回桌面合成，WebView2 挂在本窗口的 DirectComposition 视觉树会失效；透明窗口
// 没有 GDI 内容可兜底重绘 = 桌宠看起来"消失"，直到下次 z-order/焦点变化才恢复。
// 监听 EVENT_SYSTEM_FOREGROUND：前台易主即重插 topmost 并通知 WebView2 重建视觉。
// 同一事件源顺带做全屏自动穿透：前台是全屏-like 窗口（游戏）时桌宠改 click-through，
// 否则恢复——解决置顶桌宠在全屏游戏里抢鼠标的问题（置顶保留，只挡输入）。

static WATCHDOG_APP: OnceLock<tauri::AppHandle> = OnceLock::new();
static WATCHDOG_EPOCH: AtomicU64 = AtomicU64::new(0);
static WATCHDOG_FIRES: AtomicU64 = AtomicU64::new(0);
static WATCHDOG_REASSERTS: AtomicU64 = AtomicU64::new(0);
static WATCHDOG_INSTALLED: AtomicBool = AtomicBool::new(false);

/// 前台窗口是否"全屏-like"。两路判定：① 窗口矩形覆盖所在显示器（无边框窗口化
/// 游戏）；② SHQueryUserNotificationState 报全屏（独占全屏 D3D，矩形判定在独占
/// 切换瞬间可能失真）。排除桌面/explorer（Alt+Tab 回桌面时 Progman 也是全屏矩形，
/// 不排除会导致回到桌面仍保持穿透）。多显示器注意：QUNS 是全会话级信号，全屏应用
/// 在别的屏时本屏也会判 true（本机单屏使用，可接受）。
fn foreground_is_fullscreen_like() -> bool {
    use windows::Win32::Foundation::{CloseHandle, RECT};
    use windows::Win32::Graphics::Gdi::{
        GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST,
    };
    use windows::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
        PROCESS_QUERY_LIMITED_INFORMATION,
    };
    use windows::Win32::UI::Shell::{
        SHQueryUserNotificationState, QUNS_BUSY, QUNS_PRESENTATION_MODE,
        QUNS_RUNNING_D3D_FULL_SCREEN,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetShellWindow, GetWindowRect, GetWindowThreadProcessId,
    };

    unsafe {
        let hwnd = GetForegroundWindow();
        if hwnd.is_invalid() || hwnd == GetShellWindow() {
            return false;
        }
        // 排除 explorer（桌面/任务栏获得焦点的场景）
        let mut pid = 0u32;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        if pid != 0 {
            if let Ok(hproc) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
                let mut pbuf = [0u16; 512];
                let mut len = pbuf.len() as u32;
                let name = if QueryFullProcessImageNameW(
                    hproc,
                    PROCESS_NAME_WIN32,
                    windows::core::PWSTR(pbuf.as_mut_ptr()),
                    &mut len,
                )
                .is_ok()
                {
                    String::from_utf16_lossy(&pbuf[..len as usize])
                } else {
                    String::new()
                };
                let _ = CloseHandle(hproc);
                let exe = name.rsplit('\\').next().unwrap_or(&name);
                if exe.eq_ignore_ascii_case("explorer.exe") {
                    return false;
                }
            }
        }
        // ① 矩形覆盖所在显示器
        let mut rc = RECT::default();
        if GetWindowRect(hwnd, &mut rc).is_ok() {
            let mon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
            let mut mi = MONITORINFO {
                cbSize: std::mem::size_of::<MONITORINFO>() as u32,
                ..Default::default()
            };
            if GetMonitorInfoW(mon, &mut mi).as_bool() {
                let m = mi.rcMonitor;
                if rc.left <= m.left
                    && rc.top <= m.top
                    && rc.right >= m.right
                    && rc.bottom >= m.bottom
                {
                    return true;
                }
            }
        }
        // ② 独占全屏兜取（QUNS_BUSY = 有全屏应用在跑）
        if let Ok(quns) = SHQueryUserNotificationState() {
            if quns == QUNS_BUSY
                || quns == QUNS_RUNNING_D3D_FULL_SCREEN
                || quns == QUNS_PRESENTATION_MODE
            {
                return true;
            }
        }
        false
    }
}

/// 重插 topmost 触发 DWM 合成树重排 + 通知 WebView2 重建视觉树（官方恢复 API）。
fn reassert_topmost_and_visual(window: &tauri::WebviewWindow) {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
    };
    if let Ok(hwnd) = window.hwnd() {
        // tauri 2.11 内部 windows 0.61 与本 crate 0.62 的 HWND 是不同类型，取裸指针重建
        let hwnd = HWND(hwnd.0);
        unsafe {
            let _ = SetWindowPos(
                hwnd,
                Some(HWND_TOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            );
        }
    }
    let _ = window.with_webview(|webview| unsafe {
        let _ = webview.controller().NotifyParentWindowPositionChanged();
    });
}

unsafe extern "system" fn foreground_event_proc(
    _hook: windows::Win32::UI::Accessibility::HWINEVENTHOOK,
    _event: u32,
    _hwnd: windows::Win32::Foundation::HWND,
    _id_object: i32,
    _id_child: i32,
    _id_event_thread: u32,
    _dwms_event_time: u32,
) {
    WATCHDOG_FIRES.fetch_add(1, Ordering::SeqCst);
    let epoch = WATCHDOG_EPOCH.fetch_add(1, Ordering::SeqCst) + 1;
    let Some(app) = WATCHDOG_APP.get().cloned() else {
        return;
    };
    std::thread::spawn(move || {
        // 防抖：关闭全屏应用时前台常连续易主，200ms 内合并为一次重断言
        std::thread::sleep(std::time::Duration::from_millis(200));
        if WATCHDOG_EPOCH.load(Ordering::SeqCst) != epoch {
            return;
        }
        let fullscreen = foreground_is_fullscreen_like();
        let app2 = app.clone();
        let _ = app.run_on_main_thread(move || {
            WATCHDOG_REASSERTS.fetch_add(1, Ordering::SeqCst);
            if let Some(w) = app2.get_webview_window("main") {
                reassert_topmost_and_visual(&w);
            }
            // 全屏自动穿透：离开全屏时顺带复位用户压制
            AUTO_PASS.store(fullscreen, Ordering::SeqCst);
            if !fullscreen {
                AUTO_PASS_SUPPRESS.store(false, Ordering::SeqCst);
            }
            apply_pass_through(&app2);
        });
    });
}

fn install_foreground_watchdog(app: &tauri::AppHandle) {
    use windows::Win32::UI::Accessibility::SetWinEventHook;
    use windows::Win32::UI::WindowsAndMessaging::{
        EVENT_SYSTEM_FOREGROUND, WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS,
    };
    let _ = WATCHDOG_APP.set(app.clone());
    unsafe {
        let hook = SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,
            Some(foreground_event_proc),
            0,
            0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        );
        let ok = !hook.0.is_null();
        WATCHDOG_INSTALLED.store(ok, Ordering::SeqCst);
        if !ok {
            eprintln!("foreground watchdog: SetWinEventHook failed");
        }
        // hook 句柄故意不保存：与进程同生共死，退出时由 OS 回收
    }
    // 启动即补一次全屏检测：开机自启时若游戏已是前台，不会有前台事件可触发
    {
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_secs(1));
            let fs = foreground_is_fullscreen_like();
            let app2 = app.clone();
            let _ = app.run_on_main_thread(move || {
                AUTO_PASS.store(fs, Ordering::SeqCst);
                apply_pass_through(&app2);
            });
        });
    }
}

/// 看门狗状态（CDP 调试用）：hook 是否安装、前台事件数、实际重断言数、穿透各态。
#[tauri::command]
fn pet_watchdog_status() -> serde_json::Value {
    serde_json::json!({
        "installed": WATCHDOG_INSTALLED.load(Ordering::SeqCst),
        "fires": WATCHDOG_FIRES.load(Ordering::SeqCst),
        "reasserts": WATCHDOG_REASSERTS.load(Ordering::SeqCst),
        "auto_pass_raw": AUTO_PASS.load(Ordering::SeqCst),
        "auto_pass_enabled": AUTO_PASS_ENABLED.load(Ordering::SeqCst),
        "auto_pass_suppressed": AUTO_PASS_SUPPRESS.load(Ordering::SeqCst),
        "pass_effective": PASS_EFFECTIVE.load(Ordering::SeqCst),
    })
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
        let mut emitted_any = false; // 是否已向前端推送过任何 data: 帧
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
                                emitted_any = true;
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
        // HTTP 200 但并非 SSE（如 open API 参数校验失败返回 {"status":"error",...} JSON）：
        // 一帧都没有时把残余报文体作为 connect_error 透出，否则前端会永远等不到 end 帧而卡死。
        if !emitted_any {
            let preview: String = buf.trim().chars().take(300).collect();
            let msg = if preview.is_empty() {
                "empty response (no SSE frames)".to_string()
            } else {
                preview
            };
            let _ = window.emit(
                "pet-chat",
                serde_json::json!({"type": "connect_error", "message": msg}),
            );
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

/// 能力自检：用当前 key 探测 plugin/chat/file 三项 scope 与默认模型可用性。
/// 探测手法：发"故意错误"的请求看状态码——401/403 = 缺 scope，其他（400/422/200）= scope 在。
/// 不产生 LLM 消耗、不留附件。
#[derive(serde::Serialize)]
struct Capabilities {
    plugin: bool,
    chat: bool,
    file: bool,
    provider: bool,
}

#[tauri::command]
async fn pet_capabilities(base_url: String, api_key: String) -> Result<Capabilities, String> {
    let base = base_url.trim_end_matches('/');
    let root = base.strip_suffix("/plugins/extensions").unwrap_or(base);
    let client = reqwest::Client::new();

    let (plugin, provider) = match client
        .get(format!("{base}/desktop_pet/pet/health"))
        .header("X-API-Key", &api_key)
        .send()
        .await
    {
        Ok(r) => {
            if r.status().is_success() {
                let t = r.text().await.unwrap_or_default();
                let prov = serde_json::from_str::<serde_json::Value>(&t)
                    .ok()
                    .and_then(|v| v.get("default_provider_available").and_then(|x| x.as_bool()))
                    .unwrap_or(false);
                (true, prov)
            } else {
                (false, false)
            }
        }
        Err(e) => return Err(format!("连接失败: {e}")),
    };

    let chat = match client
        .post(format!("{root}/chat"))
        .header("X-API-Key", &api_key)
        .header("Content-Type", "application/json")
        .body("{}")
        .send()
        .await
    {
        Ok(r) => {
            let s = r.status().as_u16();
            s != 401 && s != 403
        }
        Err(_) => false,
    };

    let file = match client
        .post(format!("{root}/file"))
        .header("X-API-Key", &api_key)
        .multipart(reqwest::multipart::Form::new())
        .send()
        .await
    {
        Ok(r) => {
            let s = r.status().as_u16();
            s != 401 && s != 403
        }
        Err(_) => false,
    };

    Ok(Capabilities {
        plugin,
        chat,
        file,
        provider,
    })
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

// ---------- 独立模式（无 AstrBot）：直连 OpenAI 兼容 LLM / SBV2 ----------

/// 独立模式对话：直连 OpenAI 兼容 chat/completions（非流式），返回回复全文。
/// base_url 三种写法均可：.../v1、...（根）、.../v1/chat/completions（完整路径原样用）。
/// 带 image_b64（jpeg）时把最后一条用户消息扩展为 text+image_url 段（data URL 内联，免上传）。
#[tauri::command]
async fn pet_chat_direct(
    base_url: String,
    api_key: String,
    model: String,
    messages: serde_json::Value,
    image_b64: Option<String>,
) -> Result<String, String> {
    let base = base_url.trim().trim_end_matches('/');
    let url = if base.ends_with("/chat/completions") {
        base.to_string()
    } else if base.ends_with("/v1") {
        format!("{base}/chat/completions")
    } else {
        format!("{base}/v1/chat/completions")
    };

    let mut msgs = messages;
    if let Some(b64) = image_b64.filter(|s| !s.is_empty()) {
        if let Some(last) = msgs.as_array_mut().and_then(|arr| arr.last_mut()) {
            if let Some(text) = last.get("content").and_then(|c| c.as_str()) {
                last["content"] = serde_json::json!([
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": format!("data:image/jpeg;base64,{b64}")}
                    }
                ]);
            }
        }
    }

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(180))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .bearer_auth(&api_key)
        .json(&serde_json::json!({"model": model, "messages": msgs, "stream": false}))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {text}"));
    }
    let content = serde_json::from_str::<serde_json::Value>(&text)
        .ok()
        .and_then(|v| v["choices"][0]["message"]["content"].as_str().map(|s| s.to_string()))
        .unwrap_or_default();
    if content.is_empty() {
        let head = text.chars().take(300).collect::<String>();
        return Err(format!("模型返回为空（HTTP 200），原文: {head}"));
    }
    Ok(content)
}

/// 独立模式 TTS：直连 Style-Bert-VITS2 /voice（Query 参数，非 JSON），
/// 返回 JSON 文本 {"audio": "<base64 wav>", "format": "wav"}，与插件 pet/tts 同构。
/// 参数与插件 _synthesize 一致；主窗口直连 WSL SBV2（127.0.0.1:5000，经 WSL2 localhost 转发）。
#[tauri::command]
async fn pet_tts_sbv2(
    url: String,
    text: String,
    model_id: Option<i64>,
    speaker_id: Option<i64>,
    style: Option<String>,
    length: Option<f64>,
) -> Result<String, String> {
    let base = url.trim().trim_end_matches('/');
    let params = [
        ("text", text),
        ("language", "JP".to_string()),
        ("model_id", model_id.unwrap_or(0).to_string()),
        ("speaker_id", speaker_id.unwrap_or(0).to_string()),
        ("style", style.unwrap_or_else(|| "Neutral".to_string())),
        ("length", length.unwrap_or(1.0).to_string()),
    ];
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(format!("{base}/voice"))
        .query(&params)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    if !status.is_success() {
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("HTTP {status}: {text}"));
    }
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    use base64::Engine;
    let b64 = base64::engine::general_purpose::STANDARD.encode(&bytes);
    Ok(serde_json::json!({"audio": b64, "format": "wav"}).to_string())
}

// ---------- 语音输入（本地 ASR @ 127.0.0.1:15055，whisper @ NPU） ----------

/// 授予 WebView2 麦克风权限（tauri.localhost / dev 地址），启动时调用一次；
/// 失败仅记日志（WebView2 系统弹窗兜底）。
#[tauri::command]
fn grant_mic_permission(window: tauri::WebviewWindow) -> Result<(), String> {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_STATE, ICoreWebView2Profile4,
    };
    use windows_core::{HSTRING, Interface};
    let granted = std::cell::Cell::new(false);
    let last_err_cell = std::cell::RefCell::new(String::new());
    let granted_out = granted.clone();
    let last_err_out = last_err_cell.clone();
    window
        .with_webview(move |webview| unsafe {
            // MICROPHONE=1、ALLOW=1（COREWEBVIEW2_PERMISSION_KIND/STATE 的 SDK 枚举值）
            match webview.controller().cast::<ICoreWebView2Profile4>() {
                Ok(profile) => {
                    for origin in ["http://tauri.localhost", "http://127.0.0.1:1430"] {
                        if let Err(e) = profile.SetPermissionState(
                            COREWEBVIEW2_PERMISSION_KIND(1),
                            &HSTRING::from(origin),
                            COREWEBVIEW2_PERMISSION_STATE(1),
                            None, // 完成回调可空，调用同步生效
                        ) {
                            *last_err_cell.borrow_mut() =
                                format!("SetPermissionState({origin}) failed: {e}");
                        } else {
                            granted.set(true);
                        }
                    }
                }
                Err(e) => {
                    *last_err_cell.borrow_mut() =
                        format!("cast to ICoreWebView2Profile4 failed: {e}")
                }
            }
        })
        .map_err(|e| format!("with_webview failed: {e}"))?;
    if granted_out.get() {
        Ok(())
    } else {
        let msg = last_err_out.into_inner();
        Err(if msg.is_empty() {
            "grant_mic_permission: no origin granted".to_string()
        } else {
            msg
        })
    }
}

/// 语音输入服务健康探测：GET /health，返回 JSON（status/device/model/load_error）。
/// 走 Rust 层（前端直连会撞 CORS）；服务未监听（未启动/加载中）时返回连接失败。
#[tauri::command]
async fn asr_health(url: String) -> Result<String, String> {
    let base = url.trim().trim_end_matches('/');
    let endpoint = if base.ends_with("/health") {
        base.to_string()
    } else {
        format!("{base}/health")
    };
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(endpoint)
        .send()
        .await
        .map_err(|e| format!("连接 ASR 服务失败: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {text}"));
    }
    Ok(text)
}

/// 语音输入转写：上传 WAV 字节（b64）到本地 ASR 服务，返回 JSON {"text", "elapsed_s", "audio_s"}。
#[tauri::command]
async fn asr_transcribe(url: String, wav_b64: String, initial_prompt: Option<String>) -> Result<String, String> {
    use base64::Engine;
    let wav = base64::engine::general_purpose::STANDARD
        .decode(&wav_b64)
        .map_err(|e| format!("wav base64 解码失败: {e}"))?;
    let base = url.trim().trim_end_matches('/');
    let endpoint = if base.ends_with("/transcribe") {
        base.to_string()
    } else {
        format!("{base}/transcribe")
    };
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;
    let mut req = client.post(endpoint).header("Content-Type", "application/octet-stream");
    if let Some(p) = initial_prompt {
        req = req.query(&[("initial_prompt", p)]);
    }
    let resp = req
        .body(wav)
        .send()
        .await
        .map_err(|e| format!("连接 ASR 服务失败: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {text}"));
    }
    Ok(text)
}

/// 桌面感知：抓取当前前台窗口画面（WGC 进程级），返回 JPEG base64 与窗口信息。
#[tauri::command]
fn capture_window() -> Result<capture::CaptureResult, String> {
    capture::capture_foreground()
}

// ---------- 用户模型上传/卸载（磁盘模型运行时经 asset protocol 加载） ----------

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

#[derive(serde::Serialize)]
struct UploadedModel {
    key: String,
    name: String,
    entry_path: String,
}

fn models_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let p = app
        .path()
        .app_local_data_dir()
        .map_err(|e| e.to_string())?
        .join("models");
    std::fs::create_dir_all(&p).map_err(|e| e.to_string())?;
    Ok(p)
}

/// 目录名 → key：小写字母数字-_，其余折叠为 _；空则 model
fn ascii_slug(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        let lc = c.to_ascii_lowercase();
        if lc.is_ascii_alphanumeric() || lc == '-' || lc == '_' {
            out.push(lc);
        } else if !out.ends_with('_') {
            out.push('_');
        }
    }
    let t = out.trim_matches('_').to_string();
    if t.is_empty() { "model".into() } else { t }
}

fn unique_key(root: &Path, base: &str) -> String {
    let mut k = base.to_string();
    let mut i = 2;
    while root.join(&k).exists() {
        k = format!("{base}_{i}");
        i += 1;
    }
    k
}

/// 文件名是否可直接保留（asset protocol 对非 ASCII 路径不友好，坑 4）
fn ascii_filename(name: &str) -> Option<String> {
    if name.is_ascii()
        && !name.starts_with('.')
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-' || c == '_')
    {
        Some(name.to_string())
    } else {
        None
    }
}

/// 递归找入口 .model3.json（多个时取路径最浅的）
fn find_model3(root: &Path) -> Result<PathBuf, String> {
    let mut found = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in std::fs::read_dir(&d).map_err(|e| e.to_string())? {
            let p = e.map_err(|e| e.to_string())?.path();
            if p.is_dir() {
                stack.push(p);
            } else if p
                .file_name()
                .map(|n| n.to_string_lossy().ends_with(".model3.json"))
                .unwrap_or(false)
            {
                found.push(p);
            }
        }
    }
    match found.len() {
        0 => Err("未找到 .model3.json 入口文件".into()),
        _ => {
            found.sort_by_key(|p| p.components().count());
            Ok(found.remove(0))
        }
    }
}

/// moc3 头校验：magic MOC3 + 版本字节 1..=5（1=3.0~3.2 … 5=5.0；仅远古 .moc 不支持）
fn check_moc3(path: &Path) -> Result<(), String> {
    use std::io::Read;
    let mut f = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut b = [0u8; 8];
    let n = f.read(&mut b).map_err(|e| e.to_string())?;
    if n < 5 || &b[0..4] != b"MOC3" {
        return Err("moc3 文件无效（magic 不是 MOC3）".into());
    }
    let v = b[4];
    if !(1..=5).contains(&v) {
        return Err(format!("不支持的 moc3 版本字节 {v}（支持 1~5，即 Cubism 3~5）"));
    }
    Ok(())
}

fn extract_zip(zip_path: &Path) -> Result<PathBuf, String> {
    let f = std::fs::File::open(zip_path).map_err(|e| e.to_string())?;
    let mut za = zip::ZipArchive::new(f).map_err(|e| format!("zip 打开失败: {e}"))?;
    let dest = std::env::temp_dir().join(format!("pet_shell_upload_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dest);
    std::fs::create_dir_all(&dest).map_err(|e| e.to_string())?;
    for i in 0..za.len() {
        let mut zf = za.by_index(i).map_err(|e| e.to_string())?;
        let Some(rel) = zf.enclosed_name() else { continue };
        let out = dest.join(rel);
        if zf.is_dir() {
            std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;
        } else {
            if let Some(p) = out.parent() {
                std::fs::create_dir_all(p).map_err(|e| e.to_string())?;
            }
            let mut w = std::fs::File::create(&out).map_err(|e| e.to_string())?;
            std::io::copy(&mut zf, &mut w).map_err(|e| e.to_string())?;
        }
    }
    Ok(dest)
}

/// 改写 model3.json 的 FileReferences，把相对引用替换为扁平化后的新文件名
fn rewrite_model3(json_text: &str, map: &HashMap<String, String>) -> Result<String, String> {
    let mut v: serde_json::Value = serde_json::from_str(json_text).map_err(|e| e.to_string())?;
    let norm = |s: &str| s.replace('\\', "/").trim_start_matches("./").to_string();
    let Some(fr) = v.get_mut("FileReferences") else {
        return Err("model3.json 缺少 FileReferences".into());
    };
    for key in ["Moc", "Physics", "Pose", "DisplayInfo", "UserData"] {
        if let Some(old) = fr.get(key).and_then(|x| x.as_str()).map(|s| s.to_string()) {
            if let Some(n) = map.get(&norm(&old)) {
                fr[key] = serde_json::Value::String(n.clone());
            }
        }
    }
    if let Some(tex) = fr.get_mut("Textures").and_then(|x| x.as_array_mut()) {
        for t in tex.iter_mut() {
            if let Some(s) = t.as_str() {
                if let Some(n) = map.get(&norm(s)) {
                    *t = serde_json::Value::String(n.clone());
                }
            }
        }
    }
    if let Some(groups) = fr.get_mut("Motions").and_then(|x| x.as_object_mut()) {
        for (_, arr) in groups.iter_mut() {
            if let Some(items) = arr.as_array_mut() {
                for it in items.iter_mut() {
                    if let Some(old) = it.get("File").and_then(|x| x.as_str()).map(|s| s.to_string()) {
                        if let Some(n) = map.get(&norm(&old)) {
                            it["File"] = serde_json::Value::String(n.clone());
                        }
                    }
                }
            }
        }
    }
    if let Some(exprs) = fr.get_mut("Expressions").and_then(|x| x.as_array_mut()) {
        for it in exprs.iter_mut() {
            if let Some(old) = it.get("File").and_then(|x| x.as_str()).map(|s| s.to_string()) {
                if let Some(n) = map.get(&norm(&old)) {
                    it["File"] = serde_json::Value::String(n.clone());
                }
            }
        }
    }
    serde_json::to_string_pretty(&v).map_err(|e| e.to_string())
}

fn import_model_dir(
    app: &tauri::AppHandle,
    src: &Path,
    display_override: Option<String>,
) -> Result<UploadedModel, String> {
    // 入口与模型根目录（入口所在目录即根，其外层的 VTS 配置等不带）
    let entry = if src.is_file() {
        if !src
            .file_name()
            .map(|n| n.to_string_lossy().ends_with(".model3.json"))
            .unwrap_or(false)
        {
            return Err("所选文件不是 .model3.json".into());
        }
        src.to_path_buf()
    } else {
        find_model3(src)?
    };
    let root = entry.parent().ok_or("模型路径异常")?;
    let display = display_override
        .filter(|s| !s.is_empty())
        .or_else(|| root.file_name().map(|n| n.to_string_lossy().to_string()))
        .unwrap_or_else(|| "未命名模型".into());

    // 收集全部文件（递归），生成 相对路径 -> 扁平 ASCII 新名 映射
    let mut files = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in std::fs::read_dir(&d).map_err(|e| e.to_string())? {
            let p = e.map_err(|e| e.to_string())?.path();
            if p.is_dir() {
                stack.push(p);
            } else {
                files.push(p);
            }
        }
    }
    let entry_rel = entry
        .strip_prefix(root)
        .map_err(|e| e.to_string())?
        .to_string_lossy()
        .replace('\\', "/");
    let mut map: HashMap<String, String> = HashMap::new();
    let mut used: HashSet<String> = HashSet::new();
    used.insert("model.model3.json".to_string()); // 入口统一命名，先占位
    used.insert("meta.json".to_string());
    let mut idx = 0u32;
    for p in &files {
        let rel = p
            .strip_prefix(root)
            .map_err(|e| e.to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        if rel == entry_rel {
            continue; // 入口最后统一处理
        }
        let fname = p
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let mut candidate = ascii_filename(&fname);
        if candidate.as_deref().map(|c| used.contains(c)).unwrap_or(true) {
            // 复合扩展名（.motion3.json 等）从第一个点开始保留，非 ASCII 安全则取最后一段
            let ext = match fname.find('.') {
                Some(i) => {
                    let e = &fname[i..];
                    if ascii_filename(&format!("x{e}")).is_some() {
                        e.to_ascii_lowercase()
                    } else {
                        p.extension()
                            .map(|x| format!(".{}", x.to_string_lossy().to_ascii_lowercase()))
                            .unwrap_or_default()
                    }
                }
                None => String::new(),
            };
            loop {
                idx += 1;
                let c = format!("f{idx}{ext}");
                if !used.contains(&c) {
                    candidate = Some(c);
                    break;
                }
            }
        }
        let name = candidate.unwrap();
        used.insert(name.clone());
        map.insert(rel, name);
    }

    // 校验 model3.json + moc3
    let model3_text = std::fs::read_to_string(&entry)
        .map_err(|e| format!("model3.json 读取失败（需 UTF-8）: {e}"))?;
    let model3: serde_json::Value =
        serde_json::from_str(&model3_text).map_err(|e| format!("model3.json 解析失败: {e}"))?;
    if model3.get("Version").and_then(|v| v.as_i64()).unwrap_or(0) < 3 {
        return Err("model3.json Version 缺失或过低（仅支持 Cubism 3~5 的 moc3 模型）".into());
    }
    let moc_rel = model3
        .get("FileReferences")
        .and_then(|fr| fr.get("Moc"))
        .and_then(|m| m.as_str())
        .ok_or("model3.json 缺少 FileReferences.Moc")?
        .replace('\\', "/");
    let moc_path = root.join(&moc_rel);
    if !moc_path.exists() {
        return Err(format!("moc 文件不存在: {moc_rel}"));
    }
    check_moc3(&moc_path)?;

    // 落盘：复制（扁平新名）+ 重写后的入口 + meta.json
    let dest_root = models_root(app)?;
    let key = unique_key(&dest_root, &ascii_slug(&display));
    let dest = dest_root.join(&key);
    std::fs::create_dir_all(&dest).map_err(|e| e.to_string())?;
    let result = (|| -> Result<(), String> {
        for p in &files {
            let rel = p
                .strip_prefix(root)
                .map_err(|e| e.to_string())?
                .to_string_lossy()
                .replace('\\', "/");
            if rel == entry_rel {
                continue;
            }
            let Some(name) = map.get(&rel) else { continue };
            std::fs::copy(p, dest.join(name)).map_err(|e| e.to_string())?;
        }
        let rewritten = rewrite_model3(&model3_text, &map)?;
        std::fs::write(dest.join("model.model3.json"), rewritten).map_err(|e| e.to_string())?;
        let meta = serde_json::json!({
            "name": display,
            "entry": "model.model3.json",
        });
        std::fs::write(dest.join("meta.json"), meta.to_string()).map_err(|e| e.to_string())?;
        Ok(())
    })();
    if let Err(e) = result {
        let _ = std::fs::remove_dir_all(&dest);
        return Err(e);
    }
    Ok(UploadedModel {
        key,
        name: display,
        entry_path: dest.join("model.model3.json").to_string_lossy().to_string(),
    })
}

/// 上传模型：文件夹 / .model3.json / .zip，复制到 appdata models 目录并 ASCII 化。
#[tauri::command]
fn pet_model_upload(app: tauri::AppHandle, src_path: String) -> Result<UploadedModel, String> {
    let cleaned = src_path.trim().trim_matches('"').to_string();
    let mut src = PathBuf::from(&cleaned);
    if !src.exists() {
        return Err(format!("路径不存在: {cleaned}"));
    }
    let mut temp_dir: Option<PathBuf> = None;
    let mut display_override: Option<String> = None;
    if src.is_file()
        && src
            .extension()
            .map(|e| e.eq_ignore_ascii_case("zip"))
            .unwrap_or(false)
    {
        display_override = src.file_stem().map(|n| n.to_string_lossy().to_string());
        let d = extract_zip(&src)?;
        temp_dir = Some(d.clone());
        src = d;
    }
    let result = import_model_dir(&app, &src, display_override);
    if let Some(d) = temp_dir {
        let _ = std::fs::remove_dir_all(d);
    }
    result
}

/// 列出已上传模型（扫描 appdata models 目录）。
#[tauri::command]
fn pet_model_list(app: tauri::AppHandle) -> Result<Vec<UploadedModel>, String> {
    let root = models_root(&app)?;
    let mut out = Vec::new();
    for e in std::fs::read_dir(&root).map_err(|e| e.to_string())? {
        let dir = e.map_err(|e| e.to_string())?.path();
        if !dir.is_dir() {
            continue;
        }
        let Ok(meta_text) = std::fs::read_to_string(dir.join("meta.json")) else {
            continue;
        };
        let Ok(meta) = serde_json::from_str::<serde_json::Value>(&meta_text) else {
            continue;
        };
        let entry = meta
            .get("entry")
            .and_then(|x| x.as_str())
            .unwrap_or("model.model3.json");
        let entry_p = dir.join(entry);
        if !entry_p.exists() {
            continue;
        }
        let name = meta
            .get("name")
            .and_then(|x| x.as_str())
            .unwrap_or("未命名模型")
            .to_string();
        let key = dir
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        out.push(UploadedModel {
            key,
            name,
            entry_path: entry_p.to_string_lossy().to_string(),
        });
    }
    Ok(out)
}

/// 卸载模型：删除 appdata models 下的整个目录（key 限字符防路径穿越）。
#[tauri::command]
fn pet_model_delete(app: tauri::AppHandle, key: String) -> Result<(), String> {
    if key.is_empty()
        || !key
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err("非法模型 key".into());
    }
    let dir = models_root(&app)?.join(&key);
    if !dir.exists() {
        return Err("模型不存在".into());
    }
    std::fs::remove_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(())
}

/// 简单百分号解码（petmodel 协议路径用；文件名已扁平 ASCII 化，解码仅为兜底）
fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(v) = u8::from_str_radix(&s[i + 1..i + 3], 16) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).to_string()
}

/// petmodel://localhost/<key>/<file> —— 只服务 appdata models 目录下的已上传模型。
/// 自定义协议（Windows 下为 http://petmodel.localhost）路径带真实 "/" 层级，
/// model3.json 内的相对引用可正确解析（asset protocol 全量百分号编码做不到，坑 4 延伸）。
fn petmodel_response(status: u16, mime: &str, body: Vec<u8>) -> tauri::http::Response<Vec<u8>> {
    tauri::http::Response::builder()
        .status(status)
        .header("Content-Type", mime)
        .header("Access-Control-Allow-Origin", "*")
        .body(body)
        .unwrap()
}

fn serve_petmodel(app: &tauri::AppHandle, request: &tauri::http::Request<Vec<u8>>) -> tauri::http::Response<Vec<u8>> {
    let bad = |msg: &str| petmodel_response(403, "text/plain", msg.as_bytes().to_vec());
    let path = request.uri().path().trim_start_matches('/');
    let decoded = percent_decode(path);
    let parts: Vec<&str> = decoded.split('/').filter(|s| !s.is_empty()).collect();
    if parts.len() != 2 || parts.iter().any(|p| p.contains("..") || p.contains('\\')) {
        return bad("bad path");
    }
    let key = parts[0];
    let file = parts[1];
    if !key
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return bad("bad key");
    }
    let Ok(root) = models_root(app) else {
        return bad("no root");
    };
    let full = root.join(key).join(file);
    match std::fs::read(&full) {
        Ok(bytes) => {
            let mime = match full.extension().and_then(|e| e.to_str()).unwrap_or("") {
                "json" => "application/json",
                "png" => "image/png",
                _ => "application/octet-stream",
            };
            petmodel_response(200, mime, bytes)
        }
        Err(_) => petmodel_response(404, "text/plain", b"not found".to_vec()),
    }
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
        .register_uri_scheme_protocol("petmodel", |ctx, request| {
            serve_petmodel(ctx.app_handle(), &request)
        })
        .invoke_handler(tauri::generate_handler![
            set_click_through,
            quit_app,
            resize_window,
            pet_watchdog_status,
            pet_health,
            pet_capabilities,
            pet_open_chat,
            pet_tts,
            pet_upload_file,
            pet_get,
            pet_post_json,
            pet_chat_direct,
            pet_tts_sbv2,
            grant_mic_permission,
            asr_health,
            asr_transcribe,
            capture_window,
            get_system_context,
            pet_model_upload,
            pet_model_list,
            pet_model_delete
        ])
        .setup(|app| {
            #[cfg(debug_assertions)]
            if let Some(w) = app.get_webview_window("main") {
                w.open_devtools();
            }
            // 语音输入：启动即预授予麦克风权限（失败仅记日志，WebView2 弹窗兜底）
            if let Some(w) = app.get_webview_window("main") {
                if let Err(e) = grant_mic_permission(w) {
                    eprintln!("grant_mic_permission: {e}");
                }
            }
            // 前台切换看门狗：全屏应用关闭后 WebView2 视觉树失效导致"桌宠消失"的恢复
            install_foreground_watchdog(app.handle());
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
            let auto_pass = CheckMenuItem::with_id(
                app,
                "auto_pass",
                "全屏自动穿透（游戏防抢鼠标）",
                true,
                true,
                None::<&str>,
            )?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&pass, &auto_pass, &quit])?;
            let mut tray = TrayIconBuilder::with_id("main-tray")
                .menu(&menu)
                .tooltip("AstrBotPet");
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            let auto_pass_handle = auto_pass.clone();
            tray.on_menu_event(move |app, event| match event.id.as_ref() {
                "quit" => app.exit(0),
                "pass" => toggle_click_through(app),
                "auto_pass" => {
                    let new = !AUTO_PASS_ENABLED.load(Ordering::SeqCst);
                    AUTO_PASS_ENABLED.store(new, Ordering::SeqCst);
                    let _ = auto_pass_handle.set_checked(new);
                    apply_pass_through(app);
                }
                _ => {}
            })
            .build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running pet shell");
}
