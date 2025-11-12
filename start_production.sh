#!/bin/bash
echo "🚀 启动生产环境 DingTalk 服务..."

# 停止现有容器
docker rm -f dingtalk-prod 2>/dev/null

# 启动生产容器
docker run -d \
  --name dingtalk-prod \
  -p 8000:8000 \
  -v $(pwd):/app \
  -w /app \
  --restart unless-stopped \
  python:3.9-alpine \
  sh -c "pip install fastapi uvicorn && uvicorn src.app:app --host 0.0.0.0 --port 8000"

echo "✅ 服务已启动"
echo "📊 查看状态: docker logs dingtalk-prod"
echo "🌐 访问地址: http://localhost:8000/docs"
