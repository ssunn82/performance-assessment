#!/usr/bin/env bash
# ============================================================
# 中层绩效考核系统 - 轻量应用服务器一键部署脚本 (Ubuntu 22.04)
# 用法:
#   1) 将本项目 zip 上传到服务器并解压到当前目录
#   2) 赋予执行权限: chmod +x deploy.sh
#   3) 运行: sudo ./deploy.sh
# 脚本会: 装依赖 -> 建虚拟环境 -> 生成密钥 -> 注册 systemd 服务
#         -> 配置 nginx 反代(8851) -> 开放本机防火墙端口
#
# 端口约定:
#   对外访问端口  8851  (nginx，公网访问 http://<公网IP>:8851)
#   内部应用端口  8000  (gunicorn，仅监听 127.0.0.1，公网不可达)
# ============================================================
set -e

APP_DIR="/opt/performance_assessment"
SERVICE_NAME="perf-assessment"
PORT="${PORT:-8000}"              # 内部 gunicorn 端口，不对外开放
PUBLIC_PORT="${PUBLIC_PORT:-8851}" # 对外 nginx 监听端口（纯端口访问模式）
# 外部访问地址：影响二维码/Excel 中的扫码链接。
# 配了域名反代时务必设置为 https://kaohe.你的域名.com，否则扫码方在外网打不开。
BASE_URL="${BASE_URL:-}"

echo "==> [1/7] 安装系统依赖 (python3-venv, nginx)"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip nginx

echo "==> [2/7] 复制代码到 $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo cp -r ./* "$APP_DIR"/ 2>/dev/null || true
sudo cp -r ./.[!.]* "$APP_DIR"/ 2>/dev/null || true
cd "$APP_DIR"

echo "==> [3/7] 创建虚拟环境并安装 Python 依赖"
sudo python3 -m venv venv
sudo "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/venv/bin/pip" install -r requirements.txt

echo "==> [4/7] 准备会话密钥"
if [ -z "$SESSION_SECRET_KEY" ]; then
  export SESSION_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")
fi

echo "==> [5/7] 注册 systemd 服务 (开机自启 + 崩溃重启)"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=中层绩效考核系统
After=network.target

[Service]
User=root
WorkingDirectory=${APP_DIR}
Environment=SESSION_SECRET_KEY=${SESSION_SECRET_KEY}
Environment=BIND=127.0.0.1
Environment=PORT=${PORT}
Environment=BASE_URL=${BASE_URL}
ExecStart=${APP_DIR}/venv/bin/gunicorn -c ${APP_DIR}/gunicorn.conf.py main:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

echo "==> [6/7] 配置 nginx 反向代理 (对外端口 ${PUBLIC_PORT})"
# 端口以 nginx.conf 为准；此处仅在 nginx.conf 未含 8851 时按 PUBLIC_PORT 动态改写
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}"
sudo cp "${APP_DIR}/nginx.conf" "$NGINX_SITE"
if [ "$PUBLIC_PORT" != "8851" ]; then
  echo "    自定义对外端口 $PUBLIC_PORT，动态改写 nginx 配置"
  sudo sed -i "s/^    listen 8851;/    listen ${PUBLIC_PORT};/" "$NGINX_SITE"
fi
sudo ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/${SERVICE_NAME}
# 关闭默认站点，避免 80 端口上的默认 server 干扰
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "==> [7/7] 放行本机防火墙端口 ${PUBLIC_PORT}"
sudo ufw allow ${PUBLIC_PORT}/tcp 2>/dev/null || echo "    (ufw 未启用或无权限，可跳过；腾讯云控制台防火墙仍需手动放行)"

echo ""
echo "✅ 部署完成！"
echo "   本机自检:  curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:${PUBLIC_PORT}"
echo "   公网访问:  http://<服务器公网IP>:${PUBLIC_PORT}"
echo "   默认账号:  admin / admin123  (请登录后在「系统设置」立即修改密码)"
echo ""
echo "📌 下一步必做:"
echo "   1) 腾讯云控制台 → 轻量应用服务器 → 防火墙 → 添加规则: TCP / 端口 ${PUBLIC_PORT} / 来源 0.0.0.0/0"
echo "      （若只想让公司内网访问，把来源改成你办公网段，例如 192.168.1.0/24，更安全）"
echo "   2) 浏览器打开 http://<公网IP>:${PUBLIC_PORT} 登录后立即修改默认密码"
echo "   3) 用 Excel 模板录入考核人 / 被考核人 / 指标，或手动在后台添加"
echo ""
echo "ℹ️ 应用内部端口 ${PORT} 仅监听 127.0.0.1，无需在控制台放行。"
if [ -n "$BASE_URL" ]; then
  echo "ℹ️ 已固定扫码链接前缀为: ${BASE_URL}（二维码与导出的链接都会用它）"
else
  echo ""
  echo "⚠️  未设置 BASE_URL：二维码里的链接会带本机 IP/端口。"
  echo "   若考核人从外网扫码，将无法访问。建议重新部署时带上："
  echo "   sudo BASE_URL=https://kaohe.你的域名.com ./deploy.sh"
  echo "   或纯端口访问时用：http://<公网IP>:${PUBLIC_PORT}"
fi
echo ""
echo "ℹ️ 想让考核人直接输入域名（无需带端口）访问，见 nginx-domain.conf 模板。"