import requests
import json
import logging
from typing import Optional
from src.models.notification import DingTalkMessage, WebhookResponse
from src.utils.security import DingTalkSecurity

logger = logging.getLogger(__name__)

class DingTalkService:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json'
        })

    async def send_message(
        self, 
        message: DingTalkMessage, 
        webhook_url: str, 
        secret: Optional[str] = None
    ) -> WebhookResponse:
        """
        发送消息到钉钉
        """
        try:
            data = message.dict(exclude_none=True)
            
            if secret:
                signature = DingTalkSecurity.generate_signature(secret)
                full_url = f"{webhook_url}&timestamp={signature['timestamp']}&sign={signature['sign']}"
            else:
                full_url = webhook_url

            response = self.session.post(full_url, data=json.dumps(data), timeout=10)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('errcode') == 0:
                logger.info(f"消息发送成功: {result.get('msgid')}")
                return WebhookResponse(
                    success=True,
                    message="消息发送成功",
                    dingtalk_msg_id=result.get('msgid')
                )
            else:
                logger.error(f"钉钉API错误: {result.get('errmsg')}")
                return WebhookResponse(
                    success=False,
                    message=f"钉钉API错误: {result.get('errmsg')}"
                )
                
        except requests.exceptions.Timeout:
            logger.error("钉钉API请求超时")
            return WebhookResponse(
                success=False,
                message="请求钉钉API超时"
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"钉钉API请求失败: {str(e)}")
            return WebhookResponse(
                success=False,
                message=f"请求钉钉API失败: {str(e)}"
            )
        except Exception as e:
            logger.error(f"发送消息时发生未知错误: {str(e)}")
            return WebhookResponse(
                success=False,
                message=f"未知错误: {str(e)}"
            )
