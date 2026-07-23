# 一键停止桌宠全套环境：桌宠壳 + AstrBot/NapCat 容器 + SBV2 + 关闭 WSL
# 用法：powershell -File stop_all.ps1

$ErrorActionPreference = "Continue"

Write-Output "[1/3] 停止桌宠壳..."
$pet = Get-Process pet_shell -ErrorAction SilentlyContinue
if ($pet) {
    taskkill /T /F /PID $($pet.Id) | Out-Null
    Write-Output "  已停止"
} else {
    Write-Output "  未在运行"
}

Write-Output "[2/3] 停止 AstrBot / NapCat 容器与 SBV2 服务..."
wsl -e bash -lc "docker stop astrbot napcat 2>/dev/null; systemctl stop sbv2-tts 2>/dev/null; echo done"

Write-Output "[3/3] 关闭 WSL 虚拟机（释放内存）..."
wsl --shutdown
Write-Output "全部停止。"
