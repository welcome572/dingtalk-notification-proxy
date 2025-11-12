#!/bin/bash

set -e

echo "🚀 开发模式启动 DingTalk 通知中转服务..."

# 检查配置文件
if [ ! -f "config/config.yaml" ]; then
    echo "❌ 配置文件 config/config.yaml 不存在"
    exit 1
fi

# 创建必要的目录
mkdir -p logs

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "📦 创建Python虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
echo "🔧 激活虚拟环境..."
source venv/bin/activate

# 升级pip并安装依赖
echo "📦 安装/更新Python依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 启动开发服务器
echo "🔧 启动开发服务器..."
uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload
