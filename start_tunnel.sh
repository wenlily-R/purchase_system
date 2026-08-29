#!/bin/sh
# 隧道启动脚本 - 用 sh 执行, 绕过 zsh login shell 的 conda 污染
# 2026-08-19: hermes 后台进程用 zsh -lic 启动会重新加载 conda, 导致 ssh 被劫持(code 255)
export CONDA_NO_PLUGINS=1
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
cd "/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system" || exit 1
# 循环: ssh 断开自动重连
while true; do
  echo "[$(date '+%H:%M:%S')] 建立隧道..."
  /usr/bin/ssh -T \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=5 \
    -o ServerAliveCountMax=120 \
    -o ExitOnForwardFailure=yes \
    -o TCPKeepAlive=yes \
    -o ConnectionAttempts=3 \
    -o ConnectTimeout=15 \
    -R 80:127.0.0.1:5899 \
    nokey@localhost.run 2>&1 | while read -r line; do
      echo "$line"
      # 抓到新地址立即写入文件
      URL=$(echo "$line" | grep -oE 'https://[a-z0-9]+\.lhr\.life' | head -1)
      if [ -n "$URL" ]; then
        echo "$URL" > data/public_url.txt
        echo "[$(date '+%H:%M:%S')] 新地址: $URL"
      fi
    done
  echo "[$(date '+%H:%M:%S')] 隧道断开, 5秒后重连..."
  sleep 5
done
