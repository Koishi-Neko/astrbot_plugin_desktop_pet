# 一键启动桌宠全套环境：WSL(SBV2+docker/AstrBot/NapCat) + 桌宠壳
# 用法：powershell -File start_all.ps1

$ErrorActionPreference = "Continue"
$repo = "C:\Users\15263\astrbot_plugin_desktop_pet"

Write-Output "[1/3] 启动 WSL 服务（SBV2 TTS + Docker 容器）..."
wsl -e bash -lc "systemctl start docker sbv2-tts 2>/dev/null; docker start astrbot napcat 2>/dev/null; echo done"

Write-Output "[2/3] 等待 AstrBot 就绪（端口 6185）..."
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 3
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", 6185)
        $tcp.Close()
        $ready = $true
        break
    } catch {}
}
if ($ready) { Write-Output "  AstrBot 已就绪" } else { Write-Output "  警告：等待超时，AstrBot 可能仍在启动中" }

Write-Output "[3/3] 启动桌宠壳..."
$petExe = "$repo\pet_shell\src-tauri\target\release\pet_shell.exe"
if (Get-Process pet_shell -ErrorAction SilentlyContinue) {
    Write-Output "  桌宠已在运行，跳过"
} elseif (Test-Path $petExe) {
    Start-Process -FilePath $petExe -WorkingDirectory "$repo\pet_shell\src-tauri" -WindowStyle Hidden
    Write-Output "  桌宠已启动"
} else {
    Write-Output "  错误：找不到 $petExe（需要先 cargo build 一次）"
}
Write-Output "全部完成。"
