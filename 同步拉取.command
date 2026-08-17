#!/bin/bash
# 采购系统代码同步 - 拉取对方的修改(在要查看对方改动的电脑上运行)
# 用法: 双击运行, 或终端执行 ./同步拉取.command
cd "$(dirname "$0")"

echo "===== 拉取对方的最新修改 ====="
git pull
echo ""
echo "完成! 修改已更新到本机。"
echo "注意: 如果改了 app.py 等后端代码, 需要重启系统才生效:"
echo "  1. 关闭正在运行的 python app.py 窗口"
echo "  2. 重新运行 启动系统 脚本"
read -p "按回车关闭..."
