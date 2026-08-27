#!/usr/bin/env bash
# ============================================================
# 中层绩效考核系统 - 轻量应用服务器一键部署脚本 (Ubuntu 22.04)
# 用法:
#   1) 将本项目 zip 上传到服务器并解压到当前目录
#   2) 赋予执行权限: chmod +x deploy.sh
#   3) 运行: sudo ./deploy.sh
# 脚本会: 装依赖 -> 建虚拟环境 -> 生成密钥 -> 注册 systemd 服务
#         -> 配置 nginx 反代 -> 开放端口
# ============================================================
set -e

APP_DIR="/opt/performance_assessment"
SERVICE_NAME="perf-assessment"
PORT="${PORT:-8000}"

echo "==> [1/6] 安装系统依赖 (python3-venv, nginx)"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip nginx

echo "==> [2/6] 复制代码到 $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo cp -r ./* "$APP_DIR"/ 2>/dev/null || true
sudo cp -r ./.[!.]* "$APP_DIR"/ 2>/dev/null || true
cd "$APP_DIR"

echo "==> [3/6] 创建虚拟环境并安装 Python 依赖"
sudo python3 -m venv venv
sudo "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/venv/bin/pip" install -r requirements.txt

echo "==> [4/6] 准备会话密钥"
if [ -z "$SESSION_SECRET_KEY" ]; then
  export SESSION_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")
fi

echo "==> [5/6] 注册 systemd 服务 (开机自启 + 崩溃重启)"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=中层绩效考核系统
After=network.target

[Service]
User=root
WorkingDirectory=${APP_DIR}
Environment=SESSION_SECRET_KEY=${SESSION_SECRET_KEY}
Environment=PORT=${PORT}
ExecStart=${APP_DIR}/venv/bin/gunicorn -c ${APP_DIR}/gunicorn.conf.py main:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

echo "==> [6/6] 配置 nginx 反向代理"
sudo cp "${APP_DIR}/nginx.conf" /etc/nginx/sites-available/${SERVICE_NAME}
sudo ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/${SERVICE_NAME}
# 关闭默认站点，避免冲突
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo ""
echo "✅ 部署完成！"
echo "   本地访问:  http://localhost"
echo "   公网访问:  http://<服务器公网IP>   (需在轻量服务器控制台防火墙放行 80 端口)"
echo "   默认账号:  admin / admin123  (请登录后在「系统设置」立即修改密码)"
echo ""
echo "📌 下一步建议:"
echo "   1) 到轻量服务器控制台『防火墙』放行 TCP 80 (和 443 若上 HTTPS)"
echo "   2) 绑定域名并申请 HTTPS: sudo certbot --nginx -d 你的域名"
echo "   3) 用 Excel 模板录入考核人 / 被考核人 / 指标，或手动在后台添加"
