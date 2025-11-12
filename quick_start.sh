#!/bin/bash
echo "🚀 快速启动 DingTalk 通知服务（使用国内镜像）..."

# 设置环境变量
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# 检查Docker是否运行
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 未运行，请启动 Docker 服务"
    exit 1
fi

# 构建和启动
echo "📦 构建镜像..."
docker-compose build --no-cache

echo "🔧 启动服务..."
docker-compose up -d

echo "⏳ 等待服务启动..."
sleep 10

# 检查服务状态
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ 服务启动成功！"
    echo "📚 API文档: http://localhost:8000/docs"
    echo "❤️  健康检查: http://localhost:8000/health"
else
    echo "❌ 服务启动失败，查看日志: docker-compose logs -f dingtalk-notification"
fi
