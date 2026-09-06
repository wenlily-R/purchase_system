#!/bin/bash
# ============================================================
# mac_deploy_watch.sh — Mac 生产机自动同步守护(常驻版)
# 采购系统公网自动更新核心: 30秒一查 GitHub, 有新提交自动部署
#
# 机制(双向自动同步):
#   温温(Windows)改代码 push GitHub ──┐
#                                      ├─→ 本守护30秒拉取→生效→公网自动更新
#   邢果(Mac)改代码 push GitHub ───────┘
#
# 用法(一次性安装, 永久运行):
#   cd <采购系统目录>
#   nohup bash mac_deploy_watch.sh >> data/auto_sync.log 2>&1 &
#   验证: pgrep -fl mac_deploy_watch
#
# 安全机制:
#   - 仅安全快进(ff-only), 绝不 force, 绝不覆盖本地未提交改动
#   - 本地有未提交的业务改动 → 跳过本轮(邢果正在改, 不打扰), 提交后自动续上
#   - 本地有未推送提交 → ff-only 失败自动跳过, 不硬来
#   - 每次 pull 后自动跑 migrate_db.py(表结构迁移, 有备份可重复)
#   - watchdog 监视文件变化自动重启 Flask; 若无 watchdog 且服务挂了则主动拉起
# ============================================================

# 仓库目录 = 脚本所在目录(路径自适应, 不写死)
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || { echo "$(date '+%F %T') [FATAL] 无法进入仓库目录 $DIR"; exit 1; }

# SSH 用部署专用 key(若存在); 没有则用默认
if [ -f "$HOME/.ssh/github_deploy_key" ]; then
  export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/github_deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi
# cron/守护环境 PATH 精简, 防 alias/干扰
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin

LOG="data/auto_sync.log"
mkdir -p data
INTERVAL=30

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# 找 python: venv 优先, 否则系统 python3
find_py() {
  if [ -x .venv/bin/python ]; then echo ".venv/bin/python";
  elif [ -x ../.venv/bin/python ]; then echo "../.venv/bin/python";
  else echo "python3"; fi
}

auto_deploy() {
  # 1) 拉取远端索引
  if ! git fetch origin -q 2>>"$LOG"; then
    log "[WARN] git fetch 失败(网络?), 下轮重试"
    return 1
  fi

  # 2) 本地是否已最新
  local local_head remote_head
  local_head=$(git rev-parse HEAD 2>/dev/null)
  remote_head=$(git rev-parse origin/main 2>/dev/null)
  [ "$local_head" = "$remote_head" ] && return 0   # 已最新, 无需动作

  # 3) 本地有未提交的已跟踪改动? (未跟踪的日志/数据库不算)
  if git status --porcelain | grep -v '^??' | grep -q .; then
    log "[SKIP] 本地有未提交改动, 跳过本轮(等提交后自动续上)"
    return 1
  fi

  # 4) 安全快进拉取
  log "发现新提交: $remote_head (当前 $local_head), 开始自动部署"
  if ! git merge --ff-only origin/main >>"$LOG" 2>&1; then
    log "[WARN] ff-only 拉取失败(可能本地有未推送提交), 等对方推送后自动续上"
    return 1
  fi

  # 5) 数据库迁移(表结构变更自动应用)
  local py
  py=$(find_py)
  "$py" migrate_db.py >>"$LOG" 2>&1 || log "[WARN] migrate_db.py 异常, 见上方输出"
  log "[DONE] 已更新到: $(git log --oneline -1)"

  # 6) 服务健康检查: watchdog 会因文件变化自动重启; 此处兜底——若 5899 没监听则拉起
  sleep 8
  if ! curl -s -o /dev/null -m 5 "http://127.0.0.1:5899/"; then
    log "[WARN] 5899 无响应, 尝试拉起 app.py"
    nohup "$py" app.py >> data/app_run.log 2>&1 &
    sleep 6
    curl -s -o /dev/null -m 5 "http://127.0.0.1:5899/" && log "[OK] app.py 已拉起" || log "[ERR] app.py 拉起失败, 需人工检查"
  else
    log "[OK] 服务 5899 响应正常"
  fi
}

log "=========================================="
log "自动同步守护启动: 每 ${INTERVAL}s 检查 GitHub, 仓库: $DIR"
log "当前版本: $(git log --oneline -1 2>/dev/null)"
log "=========================================="

while true; do
  auto_deploy
  sleep "$INTERVAL"
done
