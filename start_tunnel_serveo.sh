#!/bin/sh
# serveo 隧道脚本 - 用 sh 执行绕过 conda 污染
# 2026-09-01: localhost.run 线路故障时切换到 serveo
export CONDA_NO_PLUGINS=1
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
cd "/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system" || exit 1
while true; do
  echo "[$(date '+%H:%M:%S')] serveo 建立隧道..."
  /usr/bin/ssh -T \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=10 \
    -o ServerAliveCountMax=300 \
    -o ExitOnForwardFailure=yes \
    -o TCPKeepAlive=yes \
    -o ConnectionAttempts=5 \
    -o ConnectTimeout=20 \
    -R 80:127.0.0.1:5899 serveo.net 2>&1 | while read -r line; do
      echo "$line"
      URL=$(echo "$line" | grep -oE 'https://[a-z0-9]+-[0-9]+-[0-9]+-[0-9]+-[0-9]+\.serveo\.net|https://[a-z0-9]+\.serveo\.net' | head -1)
      if [ -n "$URL" ]; then
        # V11.169: serveo 不再写 public_url.txt(主隧道独占, 避免双隧道互抢地址文件导致地址频繁跳变);
        # serveo 地址只写备用文件, 主隧道(localhost.run)失效时人工/脚本接管
        echo "$URL" > data/serveo_url.txt
        echo "[$(date '+%H:%M:%S')] serveo 备用地址: $URL"
      fi
    done
  echo "[$(date '+%H:%M:%S')] 隧道断开, 5秒后重连..."
  sleep 5
done
