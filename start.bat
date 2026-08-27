@echo off
chcp 65001 >nul
title 中层绩效考核系统 - 启动器

echo ==================================================
echo   中层绩效考核系统
echo ==================================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

:: 检查依赖
echo [1/3] 检查依赖...
python -c "import fastapi, uvicorn, qrcode, openpyxl, multipart" 2>nul
if errorlevel 1 (
    echo 正在安装依赖...
    pip install fastapi uvicorn qrcode pillow openpyxl jinja2 python-multipart -q
)

:: 启动服务
echo [2/3] 启动服务...
echo.
echo 服务地址: http://localhost:8000
echo 管理后台: http://localhost:8000/
echo.
echo 按 Ctrl+C 停止服务
echo.
echo ==================================================
echo.

cd /d "%~dp0"
python main.py

pause
