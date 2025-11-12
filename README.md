通知中转服务 一个全类型通知中转到钉钉的 Docker 服务。

## 功能特性

- ✅ 支持多种通知格式（JSON、原始文本）
- ✅ 支持 Markdown 消息模板
- ✅ 速率限制保护
- ✅ 健康检查
- ✅ Docker 容器化部署

## 快速开始


git clone https://github.com/welcome572/dingtalk-notification-proxy.git


cd dingtalk-notification-proxy


nano config/config.yaml

配置钉钉







docker run -d \
   --name dingtalk-now \
   -p 8000:8000 \
   -v $(pwd):/app \
   -w /app \  
   -e TZ=Asia/Shanghai
   -e HTTP_PROXY="" \   #代理地址 
   -e HTTPS_PROXY="" \  #代理地址 
   python:3.9-alpine \
   sh -c "pip install fastapi uvicorn pyyaml requests && uvicorn src.app:app --host 0.0.0.0 --port 8000"
