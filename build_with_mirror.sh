#!/bin/bash
echo "🚀 使用国内镜像构建 Docker 服务..."

# 停止现有容器
docker-compose down

# 清理缓存
docker system prune -f

# 使用国内镜像构建
echo "📦 开始构建镜像（使用国内镜像加速）..."
docker-compose build --no-cache --progress=plain

echo "✅ 构建完成！"
echo "🎯 启动服务: docker-compose up -d"
