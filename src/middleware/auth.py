from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import logging

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_keys: list = None):
        super().__init__(app)
        self.api_keys = api_keys or []

    async def dispatch(self, request: Request, call_next):
        # 跳过健康检查和其他公共端点
        if request.url.path in ['/health', '/', '/docs', '/redoc', '/openapi.json']:
            return await call_next(request)
        
        # 检查API密钥
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            logger.warning("未提供API密钥")
            raise HTTPException(
                status_code=401,
                detail="未提供API密钥"
            )
        
        if api_key not in self.api_keys:
            logger.warning(f"无效的API密钥: {api_key}")
            raise HTTPException(
                status_code=401,
                detail="无效的API密钥"
            )
        
        # 继续处理请求
        response = await call_next(request)
        return response
