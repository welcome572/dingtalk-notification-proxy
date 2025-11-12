#!/bin/bash
echo "🚀 快速启动 DingTalk 服务..."

# 停止现有容器
docker-compose down 2>/dev/null
docker rm -f dingtalk-fast 2>/dev/null

# 直接运行（最快的方法）
docker run -d \
  --name dingtalk-fast \
  -p 8000:8000 \
  -v $(pwd):/app \
  -w /app \
  python:3.9-alpine \
  sh -c "pip install fastapi uvicorn && uvicorn src.app:app --host 0.0.0.0 --port 8000"

echo "⏳ 等待服务启动..."
sleep 5

# 测试服务
if curl -s http://localhost:8000/health > /dev/null; then
    echo "✅ 服务启动成功！"
    echo "📚 访问: http://localhost:8000/docs"
    echo "🔍 查看日志: docker logs -f dingtalk-fast"
else
    echo "❌ 服务启动失败"
    docker logs dingtalk-fast
fi
