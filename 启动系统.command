#!/bin/bash
# 采购系统一键启动 (开机后双击运行, 或加入登录项自动运行)
# 启动: 系统 + 代码自动重载守护 + 公网隧道保活
cd "$(dirname "$0")"

echo "===== 正成能源采购系统 一键启动 ====="

# 0. 自动同步守护(GitHub双向同步, 30秒轮询, push后公网自动更新)
if pgrep -f "mac_deploy_watch.sh" >/dev/null; then
  echo "[已运行] 自动同步守护"
else
  nohup bash mac_deploy_watch.sh >> data/auto_sync.log 2>&1 &
  echo "[启动] 自动同步守护"
fi

# 1. 代码守护(自动重启系统 + 检测代码变化)
if pgrep -f "app_watchdog.py" >/dev/null; then
  echo "[已运行] 代码守护"
else
  nohup .venv/bin/python app_watchdog.py >> data/launch.log 2>&1 &
  echo "[启动] 代码守护"
fi

sleep 2

# 2. 公网隧道保活
if pgrep -f "tunnel_watchdog.py" >/dev/null; then
  echo "[已运行] 隧道保活"
else
  nohup .venv/bin/python tunnel_watchdog.py >> data/launch.log 2>&1 &
  echo "[启动] 隧道保活"
fi

# 3. 等待系统起来
sleep 5
if curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:5899/ | grep -q 200; then
  echo ""
  echo "✅ 系统运行中:"
  echo "   局域网:  http://172.16.35.163:5899"
  echo "   公网:    $(cat data/public_url.txt 2>/dev/null)"
  echo ""
  echo "改代码后保存, 系统自动重启, 刷新页面即可看到效果。"
else
  echo "⚠️ 系统未响应, 请查看 data/launch.log"
fi
echo ""
read -p "按回车关闭窗口(系统继续后台运行)..."
