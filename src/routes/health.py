from fastapi import APIRouter
from datetime import datetime
import psutil
import os

router = APIRouter(tags=["health"])

@router.get("/health")
async def health_check():
    """
    健康检查端点
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "dingtalk-notification-proxy",
        "version": "1.0.0"
    }

@router.get("/health/detailed")
async def detailed_health_check():
    """
    详细健康检查
    """
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "memory_usage_mb": round(memory_info.rss / 1024 / 1024, 2),
        "cpu_percent": process.cpu_percent(),
        "uptime_seconds": round(process.create_time()),
        "active_threads": process.num_threads()
    }

@router.get("/")
async def root():
    """
    根端点
    """
    return {
        "message": "DingTalk通知中转服务",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }
