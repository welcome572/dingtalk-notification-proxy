from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import time
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 60, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self.requests: Dict[str, list] = {}

    async def dispatch(self, request: Request, call_next):
        # 获取客户端标识
        client_ip = request.client.host
        current_time = time.time()
        
        # 清理过期的请求记录
        self._clean_old_requests(client_ip, current_time)
        
        # 检查速率限制
        if self._is_rate_limited(client_ip, current_time):
            logger.warning(f"速率限制触发: {client_ip}")
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后重试"
            )
        
        # 记录本次请求
        self._record_request(client_ip, current_time)
        
        # 继续处理请求
        response = await call_next(request)
        return response

    def _clean_old_requests(self, client_ip: str, current_time: float):
        if client_ip in self.requests:
            self.requests[client_ip] = [
                req_time for req_time in self.requests[client_ip]
                if current_time - req_time < self.window
            ]

    def _is_rate_limited(self, client_ip: str, current_time: float) -> bool:
        if client_ip not in self.requests:
            return False
        
        return len(self.requests[client_ip]) >= self.max_requests

    def _record_request(self, client_ip: str, current_time: float):
        if client_ip not in self.requests:
            self.requests[client_ip] = []
        self.requests[client_ip].append(current_time)
