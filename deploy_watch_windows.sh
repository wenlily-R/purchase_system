#!/bin/bash
# 采购系统自动部署守护(Windows/git-bash版): Mac push 后 ≤30s 自动生效
# 使用方法(在Windows上):
#   1. git-bash 进入代码目录:  cd <你的代码目录>
#   2. 运行本脚本:             bash deploy_watch_windows.sh   (保持窗口开着=守护运行中)
#   3. 想开机自启: 用"任务计划程序"建任务, 操作=启动 git-bash 并 -c "bash deploy_watch_windows.sh"
# 安全机制(与Mac版deploy_auto.sh一致): 仅安全快进, 本地有未提交改动自动跳过, 绝不force
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

auto_deploy() {
  git fetch origin 2>/dev/null || return 1
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && return 0
  git status --porcelain | grep -v '^??' | grep -q . && { echo "$(date '+%F %T') 本地有未提交改动,跳过自动部署"; return 0; }
  git pull --ff-only origin main 2>&1 || { echo "$(date '+%F %T') 自动部署失败(可能冲突),等待手动处理"; return 1; }
  # 数据库迁移: 优先项目venv python(路径按实际环境自动探测)
  if [ -f .venv/Scripts/python.exe ]; then
    .venv/Scripts/python.exe migrate_db.py
  elif [ -f ../.venv/Scripts/python.exe ]; then
    ../.venv/Scripts/python.exe migrate_db.py
  else
    python migrate_db.py 2>/dev/null || echo "提示: 未找到venv python,跳过数据库迁移(无表结构变更时可忽略)"
  fi
  echo "$(date '+%F %T') 自动部署完成: $(git log --oneline -1)"
}

while true; do
  auto_deploy
  sleep 20
done
