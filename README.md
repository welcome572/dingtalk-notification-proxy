# DingTalk 通知中转服务

一个全类型通知中转到钉钉的 Docker 服务。

## 功能特性

- ✅ 支持多种通知格式（JSON、原始文本）
- ✅ 支持 Markdown 消息模板
- ✅ 速率限制保护
- ✅ 健康检查
- ✅ Docker 容器化部署

## 快速开始

### 1. 配置钉钉机器人

在钉钉群中添加机器人，获取 webhook URL 和签名密钥。

### 2. 修改配置

复制环境变量文件并修改配置：

```bash
cp .env.example .env
# 编辑 .env 文件，填入你的钉钉机器人配置
3. 构建和运行 使用 Docker Compose： bash 复制 下载  docker-compose up -d  或者直接使用 Docker： bash 复制 下载  docker build -t dingtalk-notification .
docker run -p 8000:8000 dingtalk-notification  4. 测试服务 访问 http://localhost:8000/docs 查看 API 文档。 发送测试通知： bash 复制 下载  curl -X POST "http://localhost:8000/api/v1/webhook/dingtalk" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "这是一条测试通知",
    "title": "测试通知",
    "level": "info",
    "source": "测试脚本"
  }'  API 文档 发送通知 POST /api/v1/webhook/dingtalk  请求体： json 复制 下载  {
  "content": "通知内容",
  "title": "通知标题",
  "level": "info",
  "source": "来源系统",
  "extra_data": {}
}  健康检查 GET /health  配置说明 详见  config/config.yaml  文件。
EOF text 复制 下载  
## 第十一步：创建测试文件

### 1. 创建 tests/test_webhook.py
```bash
cat > tests/test_webhook.py << 'EOF'
import pytest
from fastapi.testclient import TestClient
from src.app import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()

def test_webhook_keys():
    response = client.get("/api/v1/webhook/webhooks")
    assert response.status_code == 200
    assert "available_webhooks" in response.json()
