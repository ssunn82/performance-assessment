"""
gunicorn 生产配置
用法: gunicorn -c gunicorn.conf.py main:app
"""
import os

# 监听地址与端口（与 nginx 反代、防火墙端口一致）
bind = "0.0.0.0:" + os.getenv("PORT", "8000")

# Worker 数量：轻量服务器 2核2G 建议 2；公式 (2 x CPU) + 1 可自行调整
workers = int(os.getenv("GUNICORN_WORKERS", "2"))

# 使用 uvicorn 的 Worker 运行 ASGI 应用
worker_class = "uvicorn.workers.UvicornWorker"

# 每个 Worker 处理的请求数，达到后重启，防止内存泄漏
max_requests = 1000
max_requests_jitter = 50

# 超时与保活
timeout = 60
keepalive = 5

# 日志输出到 stdout/stderr，便于 systemd / docker 收集
accesslog = "-"
errorlog = "-"
loglevel = "info"
