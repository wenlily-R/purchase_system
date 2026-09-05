#!/bin/bash
# 采购系统自动部署: Windows push 后 3 分钟内自动生效(仅安全快进,绝不覆盖本地改动)
# Mac 适配: .venv/bin/python + github_deploy_key + cron 环境 PATH
cd "/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system" || exit 1
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export GIT_SSH_COMMAND="ssh -i /Users/a0/.ssh/github_deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git fetch origin 2>/dev/null || exit 1
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
git status --porcelain | grep -v '^??' | grep -q . && { echo "$(date) 本地有未提交改动,跳过自动部署"; exit 0; }
git pull --ff-only origin main 2>&1 || { echo "$(date) 自动部署失败(可能冲突),等待手动处理"; exit 1; }
.venv/bin/python migrate_db.py 2>&1 || echo "$(date) 数据库迁移失败,见上方输出"
echo "$(date) 自动部署完成: $(git log --oneline -1)"
