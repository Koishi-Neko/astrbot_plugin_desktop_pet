# 一键停止桌宠全套环境：桌宠 + ASR 服务 + AstrBot/NapCat 容器 + SBV2 + 关闭 WSL
# 用法：powershell -File stop_all.ps1

$ErrorActionPreference = "Continue"

Write-Output "[1/4] 停止桌宠..."
$pet = Get-Process pet_shell -ErrorAction SilentlyContinue
if ($pet) {
    taskkill /T /F /PID $($pet.Id) | Out-Null
    Write-Output "  桌宠已停止"
} else {
    Write-Output "  未在运行"
}

Write-Output "[2/4] 停止本地 ASR 服务（语音输入）..."
$asr = Get-NetTCPConnection -LocalPort $(if ($env:ASR_PORT) { [int]$env:ASR_PORT } else { 15055 }) -State Listen -ErrorAction SilentlyContinue
if ($asr) {
    $asrPid = ($asr | Select-Object -First 1).OwningProcess
    Stop-Process -Id $asrPid -Force -ErrorAction SilentlyContinue
    Write-Output "  ASR 已停止（PID $asrPid）"
} else {
    Write-Output "  未在运行"
}

Write-Output "[3/4] 停止 AstrBot / NapCat 容器与 SBV2 服务..."
wsl -e bash -lc "export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/1000} DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}; systemctl --user stop astrbot 2>/dev/null; echo done"
wsl -u root -e bash -lc "systemctl stop napcat sbv2-tts 2>/dev/null; echo done"

Write-Output "[4/4] 关闭 WSL（释放后台内存）..."
wsl --shutdown
Write-Output "全部停止完成。"
