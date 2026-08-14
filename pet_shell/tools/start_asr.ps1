# 手动启动本地 ASR 服务（语音输入，whisper-large-v3-turbo @ NPU）
# 用途：start_all 默认不启动 ASR，需要语音输入时单独运行本脚本（或双击 start_asr.vbs）
# 用法：powershell -File start_asr.ps1
# 停止：stop_all.ps1 会一并停止；或按端口 5055 / 进程名杀
# 日志：$asrDir\asr.log / asr.err.log

$ErrorActionPreference = "Continue"
$asrDir = if ($env:ASR_DIR) { $env:ASR_DIR } elseif ($env:USERPROFILE) { Join-Path $env:USERPROFILE "asr-npu" } else { Join-Path $HOME "asr-npu" }   # ASR 服务目录（venv + 模型），默认 %USERPROFILE%\asr-npu，可用环境变量 ASR_DIR 覆盖
$asrPort = 5055

# 双重幂等：端口监听（已就绪）或 asr_server.py 进程（加载中）任一存在即跳过
$asrListening = Get-NetTCPConnection -LocalPort $asrPort -State Listen -ErrorAction SilentlyContinue
$asrProc = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*asr_server.py*" -and $_.Name -in @("python.exe", "pythonw.exe")
}
if ($asrListening -or $asrProc) {
    Write-Output "ASR 已在运行（端口 $asrPort），跳过。"
} else {
    $asrExe = "$asrDir\.venv\Scripts\python.exe"
    if (Test-Path $asrExe) {
        Start-Process -FilePath $asrExe -ArgumentList "asr_server.py" -WorkingDirectory $asrDir -WindowStyle Hidden `
            -RedirectStandardOutput "$asrDir\asr.log" -RedirectStandardError "$asrDir\asr.err.log"
        Write-Output "ASR 启动中（首次 NPU 模型加载约 4 分钟，期间语音输入按钮为灰态，就绪后自动恢复）。"
        Write-Output "日志：$asrDir\asr.log"
    } else {
        Write-Output "错误：找不到 ASR venv（$asrExe）。"
    }
}
