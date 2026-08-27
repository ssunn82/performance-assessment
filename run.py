"""
中层绩效考核系统 - 启动脚本
支持本地运行和云端部署
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import app
import uvicorn


def main():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    print("=" * 50)
    print("  中层绩效考核系统")
    print("=" * 50)
    print(f"  服务地址: http://localhost:{port}")
    print(f"  管理后台: http://localhost:{port}/")
    print(f"  按 Ctrl+C 停止服务")
    print("=" * 50)
    print()

    uvicorn.run(app, host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
