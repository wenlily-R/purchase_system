#!/bin/bash
# 采购系统代码同步 - 推送我的修改(在改了代码的电脑上运行)
# 用法: 双击运行, 或终端执行 ./同步推送.command
cd "$(dirname "$0")"

echo "===== 推送我的修改到共享仓库 ====="
git add -A
git commit -m "同步修改 $(date '+%Y-%m-%d %H:%M')"
git push
echo ""
echo "完成! 对方电脑运行【同步拉取】即可看到你的修改。"
echo "(如果提示 pull first, 先让对方推送, 或按提示执行 git pull)"
read -p "按回车关闭..."
