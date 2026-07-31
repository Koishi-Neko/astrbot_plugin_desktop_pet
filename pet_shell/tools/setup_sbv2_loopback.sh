#!/usr/bin/env bash
# 独立模式（无 AstrBot）TTS 回环桥：
# SBV2 监听在 docker 网桥 172.18.0.1:5000（Windows 宿主不可达），
# 本脚本在 WSL 内用 socat 桥到 127.0.0.1:5001，
# Windows 经 WSL2 localhost 转发即可访问 http://localhost:5001。
# 安全：socat 仅绑定 WSL 回环，不新增对外暴露面；SBV2 本体不动。
# 幂等：重复执行只重建/重启服务。回退：systemctl disable --now sbv2-loopback && 删 unit 文件。
set -euo pipefail

if ! command -v socat >/dev/null 2>&1; then
  echo "[sbv2-loopback] installing socat ..."
  apt-get update -qq
  apt-get install -y -qq socat
fi

cat > /etc/systemd/system/sbv2-loopback.service <<'EOF'
[Unit]
Description=SBV2 loopback bridge for desktop pet standalone mode (127.0.0.1:5001 -> 172.18.0.1:5000)
After=network.target sbv2-tts.service
Wants=sbv2-tts.service

[Service]
ExecStart=/usr/bin/socat TCP-LISTEN:5001,bind=127.0.0.1,reuseaddr,fork TCP:172.18.0.1:5000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable sbv2-loopback >/dev/null 2>&1 || true
systemctl restart sbv2-loopback
sleep 1
systemctl --no-pager --lines=3 status sbv2-loopback || true
