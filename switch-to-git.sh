#!/usr/bin/env bash
# ============================================================
# 将「zip 安装」的实例原地转换为「git 安装」
# 适用：当初用 zip 上传部署、没有 .git 的实例
# 效果：之后即可用 `git pull` 更新，且保留 data_v2.db / venv / .env
# 用法（在 /opt/performance_assessment 目录执行）：
#   sudo ./switch-to-git.sh
# ============================================================
set -e

APP_DIR="/opt/performance_assessment"
SERVICE_NAME="perf-assessment"
REPO_URL="https://github.com/ssunn82/performance-assessment.git"

cd "$APP_DIR" || { echo "✗ 请先 cd 到 $APP_DIR 再执行"; exit 1; }

# 0) 前置检查
command -v git >/dev/null 2>&1 || { echo "✗ 未安装 git，请先执行: sudo apt install -y git"; exit 1; }

# 1) 备份数据库（转换本身不会删库，仅为保险）
echo "==> [1/6] 备份数据库"
for f in data_v2.db data_v2.db-wal data_v2.db-shm; do
  [ -f "$f" ] && sudo cp -f "$f" "$f.pre-git" 2>/dev/null || true
done

# 2) 初始化 git 并关联远端
echo "==> [2/6] 关联 GitHub 仓库"
if [ ! -d .git ]; then git init -q; fi
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"

# 3) 拉取（私有仓库会要求凭据；失败则中止，不影响现有运行）
echo "==> [3/6] 拉取最新代码（私有仓库请按提示输入账号/令牌）"
if ! git fetch -q origin; then
  echo "✗ git fetch 失败：仓库可能是私有的，需要凭据。"
  echo "  解决三选一："
  echo "    ① 把仓库设为 Public；"
  echo "    ② 配置 deploy key 或 Personal Access Token（HTTPS 地址里嵌 token）；"
  echo "    ③ 不改 git，继续用 zip 更新（sudo ./update.sh 更新包.zip）。"
  exit 1
fi

# 4) 让本地代码与 origin/main 对齐（不动 data_v2.db / venv / .env）
echo "==> [4/6] 对齐代码到 origin/main（保留数据与运行环境）"
git checkout -B main origin/main
git branch --set-upstream-to=origin/main 2>/dev/null || true

# 5) 校验 Python 依赖（venv 已存在则补装，缺包也不怕）
echo "==> [5/6] 校验依赖"
if [ -x venv/bin/pip ]; then
  sudo venv/bin/pip install -r requirements.txt
else
  sudo python3 -m venv venv
  sudo venv/bin/pip install -r requirements.txt
fi

# 6) 重启 + 健康检查
echo "==> [6/6] 重启服务（数据库自动迁移）"
sudo systemctl restart "$SERVICE_NAME"
sleep 2
CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost || true)
echo "   本地 HTTP 状态: ${CODE:-无响应}"

echo ""
echo "✅ 转换完成！今后更新只需两行："
echo "   cd $APP_DIR && sudo git pull && sudo systemctl restart $SERVICE_NAME"
