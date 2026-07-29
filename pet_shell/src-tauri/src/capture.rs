//! 进程级窗口抓取：Windows Graphics Capture 按前台 HWND 抓帧 → JPEG base64。
//! 相比截屏：只含目标窗口内容（隐私收敛）、遮挡窗口可抓、GPU 合成画面正常。
//! 已知限制：独占全屏游戏绕过 DWM 抓不到；最小化抓不到；DRM 内容黑帧（黑图检测拦截）。

use base64::Engine;
use windows::core::Interface;
use windows::Graphics::Capture::{Direct3D11CaptureFrame, Direct3D11CaptureFramePool, GraphicsCaptureItem, GraphicsCaptureSession};
use windows::Graphics::DirectX::Direct3D11::IDirect3DDevice;
use windows::Graphics::DirectX::DirectXPixelFormat;
use windows::Win32::Foundation::{CloseHandle, HWND};
use windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE_HARDWARE;
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
    D3D11_CPU_ACCESS_READ, D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_MAPPED_SUBRESOURCE,
    D3D11_MAP_READ, D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC, D3D11_USAGE_STAGING,
};
use windows::Win32::Graphics::Dxgi::IDXGIDevice;
use windows::Win32::System::Threading::{
    OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows::Win32::System::WinRT::Direct3D11::{
    CreateDirect3D11DeviceFromDXGIDevice, IDirect3DDxgiInterfaceAccess,
};
use windows::Win32::System::WinRT::Graphics::Capture::IGraphicsCaptureItemInterop;
use windows::Win32::UI::WindowsAndMessaging::{
    GetForegroundWindow, GetWindowTextW, GetWindowThreadProcessId, IsIconic,
};

#[derive(serde::Serialize)]
pub struct CaptureResult {
    pub jpeg_b64: String,
    pub width: u32,
    pub height: u32,
    pub window_title: String,
    pub process: String,
}

fn process_name_of_pid(pid: u32) -> String {
    unsafe {
        let mut name = String::new();
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
                name = full.rsplit('\\').next().unwrap_or(&full).to_string();
            }
            let _ = CloseHandle(hproc);
        }
        name
    }
}

/// 抓取当前前台窗口画面。错误字符串是给 JS 侧分类用的语义标签。
pub fn capture_foreground() -> Result<CaptureResult, String> {
    unsafe {
        let hwnd = GetForegroundWindow();
        if hwnd.is_invalid() {
            return Err("no_foreground".into());
        }
        let mut pid = 0u32;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        if pid == std::process::id() {
            return Err("self_window".into());
        }
        if IsIconic(hwnd).as_bool() {
            return Err("minimized".into());
        }
        let mut tbuf = [0u16; 512];
        let n = GetWindowTextW(hwnd, &mut tbuf);
        let window_title = if n > 0 {
            String::from_utf16_lossy(&tbuf[..n as usize])
        } else {
            String::new()
        };
        let process = process_name_of_pid(pid);

        if !GraphicsCaptureSession::IsSupported().unwrap_or(false) {
            return Err("wgc_unsupported".into());
        }

        // D3D11 设备（按次创建，避免全局设备失效后的状态管理）
        let mut device_opt: Option<ID3D11Device> = None;
        let mut context_opt: Option<ID3D11DeviceContext> = None;
        D3D11CreateDevice(
            None,
            D3D_DRIVER_TYPE_HARDWARE,
            windows::Win32::Foundation::HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            None,
            D3D11_SDK_VERSION,
            Some(&mut device_opt),
            None,
            Some(&mut context_opt),
        )
        .map_err(|e| format!("d3d11_device: {e}"))?;
        let device = device_opt.ok_or("d3d11_device_null")?;
        let context = context_opt.ok_or("d3d11_context_null")?;
        let dxgi: IDXGIDevice = device.cast().map_err(|e| format!("dxgi_cast: {e}"))?;
        let inspectable =
            CreateDirect3D11DeviceFromDXGIDevice(&dxgi).map_err(|e| format!("rt_device: {e}"))?;
        let rt_device: IDirect3DDevice =
            inspectable.cast().map_err(|e| format!("rt_device_cast: {e}"))?;

        // 按窗口建捕获项
        let interop = windows::core::factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>()
            .map_err(|e| format!("wgc_interop: {e}"))?;
        let item: GraphicsCaptureItem = interop
            .CreateForWindow(hwnd)
            .map_err(|e| format!("create_item: {e}"))?;
        let size = item.Size().map_err(|e| format!("item_size: {e}"))?;
        if size.Width <= 0 || size.Height <= 0 {
            return Err("empty_item".into());
        }

        let pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            &rt_device,
            DirectXPixelFormat::B8G8R8A8UIntNormalized,
            2,
            size,
        )
        .map_err(|e| format!("frame_pool: {e}"))?;
        let session = pool
            .CreateCaptureSession(&item)
            .map_err(|e| format!("session: {e}"))?;
        let _ = session.SetIsCursorCaptureEnabled(false);
        let _ = session.SetIsBorderRequired(false);
        session.StartCapture().map_err(|e| format!("start: {e}"))?;

        // 取帧：首帧可能是陈旧的，取到帧后再短暂 drain 取最新
        let deadline = std::time::Instant::now() + std::time::Duration::from_millis(1500);
        let mut latest: Option<Direct3D11CaptureFrame> = None;
        loop {
            while let Ok(f) = pool.TryGetNextFrame() {
                latest = Some(f);
            }
            if latest.is_some() {
                std::thread::sleep(std::time::Duration::from_millis(30));
                while let Ok(f) = pool.TryGetNextFrame() {
                    latest = Some(f);
                }
                break;
            }
            if std::time::Instant::now() >= deadline {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(15));
        }
        let frame = latest.ok_or("no_frame")?;

        let content = frame.ContentSize().map_err(|e| format!("content_size: {e}"))?;
        let surface = frame.Surface().map_err(|e| format!("surface: {e}"))?;
        let access: IDirect3DDxgiInterfaceAccess =
            surface.cast().map_err(|e| format!("surface_cast: {e}"))?;
        let texture: ID3D11Texture2D = access.GetInterface().map_err(|e| format!("texture: {e}"))?;

        // staging 纹理读回 CPU
        let mut desc = D3D11_TEXTURE2D_DESC::default();
        texture.GetDesc(&mut desc);
        desc.Usage = D3D11_USAGE_STAGING;
        desc.BindFlags = 0;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ.0 as u32;
        desc.MiscFlags = 0;
        let mut staging_opt: Option<ID3D11Texture2D> = None;
        device
            .CreateTexture2D(&desc, None, Some(&mut staging_opt))
            .map_err(|e| format!("staging: {e}"))?;
        let staging = staging_opt.ok_or("staging_null")?;
        context.CopyResource(&staging, &texture);

        let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
        context
            .Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped))
            .map_err(|e| format!("map: {e}"))?;
        let w = (content.Width.min(desc.Width as i32)).max(0) as usize;
        let h = (content.Height.min(desc.Height as i32)).max(0) as usize;
        let pitch = mapped.RowPitch as usize;
        let mut bgra = vec![0u8; w * h * 4];
        if w > 0 && h > 0 && !mapped.pData.is_null() {
            for y in 0..h {
                let src = (mapped.pData as *const u8).add(y * pitch);
                let dst = bgra.as_mut_ptr().add(y * w * 4);
                std::ptr::copy_nonoverlapping(src, dst, w * 4);
            }
        }
        context.Unmap(&staging, 0);
        let _ = session.Close();
        let _ = pool.Close();

        if w == 0 || h == 0 {
            return Err("empty_frame".into());
        }

        // BGRA -> RGB
        let mut rgb = Vec::with_capacity(w * h * 3);
        for px in bgra.chunks_exact(4) {
            rgb.extend_from_slice(&[px[2], px[1], px[0]]);
        }
        let img = image::RgbImage::from_raw(w as u32, h as u32, rgb).ok_or("rgb_image")?;

        // 黑图检测（DRM/独占全屏会抓到全黑）
        let total = (img.width() as usize) * (img.height() as usize);
        let step = (total / 4096).max(1);
        let mut sum = 0u64;
        let mut cnt = 0u64;
        for (i, p) in img.pixels().enumerate() {
            if i % step == 0 {
                sum += (p[0] as u64 + p[1] as u64 + p[2] as u64) / 3;
                cnt += 1;
            }
        }
        if cnt > 0 && sum / cnt < 4 {
            return Err("black_frame".into());
        }

        // 缩放到长边 <=1280 后 JPEG q70
        const MAX_DIM: u32 = 1280;
        let long = img.width().max(img.height());
        let img = if long > MAX_DIM {
            let s = MAX_DIM as f32 / long as f32;
            let nw = ((img.width() as f32 * s).round() as u32).max(1);
            let nh = ((img.height() as f32 * s).round() as u32).max(1);
            image::imageops::resize(&img, nw, nh, image::imageops::FilterType::Triangle)
        } else {
            img
        };
        let mut jpeg: Vec<u8> = Vec::new();
        let mut enc = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut jpeg, 70);
        enc.encode_image(&img).map_err(|e| format!("jpeg: {e}"))?;

        Ok(CaptureResult {
            jpeg_b64: base64::engine::general_purpose::STANDARD.encode(&jpeg),
            width: img.width(),
            height: img.height(),
            window_title,
            process,
        })
    }
}

// HWND 从 windows::Win32::Foundation 引入（与 WinRT 捕获互操作参数类型一致）
#[allow(dead_code)]
type _HwndCheck = HWND;
