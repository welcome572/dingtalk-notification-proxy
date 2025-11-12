from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime

class NotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class NotificationType(str, Enum):
    MARKDOWN = "markdown"
    TEXT = "text"
    LINK = "link"

class BaseNotification(BaseModel):
    content: str = Field(..., description="通知内容")
    title: Optional[str] = Field(None, description="通知标题")
    level: NotificationLevel = Field(NotificationLevel.INFO, description="通知级别")
    source: Optional[str] = Field(None, description="通知来源")
    timestamp: Optional[datetime] = Field(default_factory=datetime.now)
    extra_data: Optional[Dict[str, Any]] = Field(default_factory=dict)

class DingTalkMarkdown(BaseModel):
    title: str = Field(..., description="标题")
    text: str = Field(..., description="markdown内容")

class DingTalkText(BaseModel):
    content: str = Field(..., description="文本内容")

class DingTalkLink(BaseModel):
    title: str = Field(..., description="链接标题")
    text: str = Field(..., description="链接内容")
    messageUrl: str = Field(..., description="链接URL")
    picUrl: Optional[str] = Field(None, description="图片URL")

class DingTalkMessage(BaseModel):
    msgtype: str = Field(..., description="消息类型")
    markdown: Optional[DingTalkMarkdown] = None
    text: Optional[DingTalkText] = None
    link: Optional[DingTalkLink] = None
    at: Optional[Dict[str, Any]] = Field(None, description="@用户配置")

class WebhookResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="响应消息")
    dingtalk_msg_id: Optional[str] = Field(None, description="钉钉消息ID")
    timestamp: datetime = Field(default_factory=datetime.now)
