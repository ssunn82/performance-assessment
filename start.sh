#!/bin/bash
# 中层绩效考核系统 - Linux/Mac 启动脚本

echo "=================================================="
echo "  中层绩效考核系统"
echo "=================================================="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python3，请先安装 Python 3.10+"
    exit 1
fi

# 检查依赖
echo "[1/3] 检查依赖..."
python3 -c "import fastapi, uvicorn, qrcode, openpyxl" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "正在安装依赖..."
    pip3 install fastapi uvicorn qrcode pillow openpyxl jinja2 --quiet
fi

# 启动服务
echo "[2/3] 启动服务..."
echo ""
echo "服务地址: http://localhost:8000"
echo "管理后台: http://localhost:8000/"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""
echo "=================================================="
echo ""

cd "$(dirname "$0")"
python3 main.py
