#!/usr/bin/env bash
# ============================================================
# 中层绩效考核系统 - 版本更新脚本（用于已部署实例）
# 适用：已在 /opt/performance_assessment 部署过的实例
# 用法:
#   方式一（git 安装）：  sudo ./update.sh
#   方式二（zip 安装）：  sudo ./update.sh 中层绩效考核系统_更新包_v1.0.zip
# 脚本会: 备份数据库 -> 拉取/解压新代码 -> 重启服务 -> 健康检查
# ============================================================
set -e

APP_DIR="/opt/performance_assessment"
SERVICE_NAME="perf-assessment"
cd "$APP_DIR"

echo "==> [1/4] 备份当前数据库（含 WAL/SHM）"
sudo cp -f data_v2.db data_v2.db.bak 2>/dev/null || true
sudo cp -f data_v2.db-wal data_v2.db-wal.bak 2>/dev/null || true
sudo cp -f data_v2.db-shm data_v2.db-shm.bak 2>/dev/null || true

if [ -d ".git" ]; then
  echo "==> [2/4] 检测到 git 仓库，执行 git pull"
  sudo git pull --ff-only
else
  ZIP="${1:-更新包.zip}"
  if [ ! -f "$ZIP" ]; then
    echo "✗ 未找到更新包: $ZIP"
    echo "  请将更新包 zip 传到 $APP_DIR 后重试，或传入路径: sudo ./update.sh /path/to/xxx.zip"
    exit 1
  fi
  echo "==> [2/4] 解压更新包 $ZIP（仅覆盖源码，不触碰 data_v2.db）"
  sudo unzip -o "$ZIP" -x "data_v2.db*" "*.bak" "venv/*" >/dev/null
fi

echo "==> [3/4] 重启服务（systemd 自动迁移数据库表结构）"
sudo systemctl restart "$SERVICE_NAME"
sleep 2

echo "==> [4/4] 健康检查"
CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost || true)
echo "   本地 HTTP 状态: ${CODE:-无响应}"

echo ""
echo "✅ 更新完成！"
echo "   公网访问: http://<服务器公网IP>"
echo "   若状态码非 200，请查日志: journalctl -u $SERVICE_NAME -n 50 --no-pager"
echo "   如需回滚: 用 data_v2.db.bak 等替换原文件后 sudo systemctl restart $SERVICE_NAME"
