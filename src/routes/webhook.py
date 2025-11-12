from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any, Optional
import json
import logging

from src.models.notification import BaseNotification, DingTalkMessage, DingTalkMarkdown, WebhookResponse
from src.services.dingtalk import DingTalkService
from src.services.template import TemplateService

router = APIRouter(prefix="/api/v1/webhook", tags=["webhook"])
logger = logging.getLogger(__name__)

# 存储配置（实际应该从配置文件加载）
dingtalk_config = {
    "default": {
        "url": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN",
        "secret": "YOUR_SECRET"
    }
}

@router.post("/dingtalk", response_model=WebhookResponse)
async def send_to_dingtalk(
    request: Request,
    notification: BaseNotification,
    webhook_key: str = "default"
):
    """
    发送通知到钉钉
    """
    try:
        # 获取webhook配置
        webhook_config = dingtalk_config.get(webhook_key)
        if not webhook_config:
            raise HTTPException(status_code=400, detail=f"未找到webhook配置: {webhook_key}")

        # 使用模板服务渲染内容
        template_data = {
            "content": notification.content,
            "title": notification.title or "系统通知",
            "level": notification.level.value,
            "source": notification.source or "未知来源",
            **notification.extra_data
        }
        
        template = TemplateService.get_default_template(notification.level.value)
        markdown_content = TemplateService.render_markdown_template(template, template_data)

        # 创建钉钉消息
        dingtalk_message = DingTalkMessage(
            msgtype="markdown",
            markdown=DingTalkMarkdown(
                title=notification.title or "系统通知",
                text=markdown_content
            )
        )

        # 发送消息
        dingtalk_service = DingTalkService()
        result = await dingtalk_service.send_message(
            dingtalk_message,
            webhook_config["url"],
            webhook_config["secret"]
        )

        return result

    except Exception as e:
        logger.error(f"发送钉钉消息失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"发送失败: {str(e)}")

@router.post("/dingtalk/raw")
async def send_raw_dingtalk(
    request: Request,
    webhook_key: str = "default"
):
    """
    接收原始数据并转发到钉钉
    """
    try:
        # 获取原始数据
        raw_data = await request.body()
        data_str = raw_data.decode('utf-8')
        
        # 尝试解析JSON
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            data = {"content": data_str}

        # 获取webhook配置
        webhook_config = dingtalk_config.get(webhook_key)
        if not webhook_config:
            raise HTTPException(status_code=400, detail=f"未找到webhook配置: {webhook_key}")

        # 创建通知对象
        notification = BaseNotification(
            content=str(data.get("content", data)),
            title=data.get("title", "原始数据通知"),
            level=data.get("level", "info"),
            source=data.get("source", "webhook"),
            extra_data=data
        )

        # 使用模板服务渲染内容
        template_data = {
            "content": notification.content,
            "title": notification.title,
            "level": notification.level.value,
            "source": notification.source,
            **notification.extra_data
        }
        
        template = TemplateService.get_default_template(notification.level.value)
        markdown_content = TemplateService.render_markdown_template(template, template_data)

        # 创建钉钉消息
        dingtalk_message = DingTalkMessage(
            msgtype="markdown",
            markdown=DingTalkMarkdown(
                title=notification.title,
                text=markdown_content
            )
        )

        # 发送消息
        dingtalk_service = DingTalkService()
        result = await dingtalk_service.send_message(
            dingtalk_message,
            webhook_config["url"],
            webhook_config["secret"]
        )

        return result

    except Exception as e:
        logger.error(f"处理原始webhook失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")

@router.get("/webhooks")
async def get_webhook_keys():
    """
    获取可用的webhook配置
    """
    return {
        "available_webhooks": list(dingtalk_config.keys()),
        "default_webhook": "default"
    }
