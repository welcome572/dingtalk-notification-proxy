#!/bin/bash

set -e

echo "🚀 启动 DingTalk 通知中转服务..."

# 检查配置文件是否存在
if [ ! -f "config/config.yaml" ]; then
    echo "❌ 配置文件 config/config.yaml 不存在"
    exit 1
fi

# 创建日志目录
mkdir -p logs

# 检查是否在虚拟环境中
if [ -z "$VIRTUAL_ENV" ]; then
    echo "📦 创建Python虚拟环境..."
    python3 -m venv venv
    source venv/bin/activate
fi

# 安装依赖
echo "📦 安装Python依赖..."
pip install -r requirements.txt

# 启动服务
echo "🔧 启动服务..."
exec uvicorn src.app:app --host 0.0.0.0 --port 8000
