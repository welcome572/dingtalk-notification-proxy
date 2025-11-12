from fastapi import FastAPI, HTTPException
import logging
import requests
import json
import hmac
import hashlib
import base64
import time
import urllib.parse
from datetime import datetime, timedelta
import yaml
import os
from typing import Dict, Set, List
from collections import defaultdict
import asyncio
import threading
from queue import Queue, Empty
import re

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 加载配置
def load_config():
    try:
        config_path = os.path.join('config', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")
        return {}

config = load_config()
dingtalk_config = config.get('dingtalk', {}).get('webhooks', {}).get('default', {})

app = FastAPI(
    title="DingTalk通知中转服务",
    description="全类型通知中转服务",
    version="7.9.0"
)

# 消息队列和重试机制
class MessageQueue:
    def __init__(self):
        self.queue = Queue()
        self.is_processing = False
        self.last_send_time = 0
        self.min_interval = 3.1  # 最小发送间隔3.1秒（钉钉限制20条/分钟）
        self.retry_count = 0
        self.max_retries = 3
        
    def add_message(self, webhook_url: str, secret: str, message: dict, message_key: str):
        """添加消息到队列"""
        self.queue.put({
            'webhook_url': webhook_url,
            'secret': secret,
            'message': message,
            'message_key': message_key,
            'timestamp': time.time(),
            'retries': 0
        })
        logger.info(f"消息已添加到队列，当前队列大小: {self.queue.qsize()}")
        
        # 如果没有在处理，启动处理线程
        if not self.is_processing:
            self.start_processing()
    
    def start_processing(self):
        """启动消息处理线程"""
        if not self.is_processing:
            self.is_processing = True
            thread = threading.Thread(target=self._process_queue, daemon=True)
            thread.start()
            logger.info("消息队列处理线程已启动")
    
    def _process_queue(self):
        """处理队列中的消息"""
        while not self.queue.empty() or self.is_processing:
            try:
                # 从队列中获取消息（非阻塞）
                message_data = self.queue.get(timeout=1)
                
                # 检查发送频率
                current_time = time.time()
                time_since_last_send = current_time - self.last_send_time
                
                if time_since_last_send < self.min_interval:
                    # 等待达到最小间隔
                    wait_time = self.min_interval - time_since_last_send
                    logger.info(f"频率限制，等待 {wait_time:.1f} 秒后发送")
                    time.sleep(wait_time)
                
                # 发送消息
                result = DingTalkSender.send_to_dingtalk(
                    message_data['webhook_url'],
                    message_data['secret'],
                    message_data['message']
                )
                
                if result["success"]:
                    self.last_send_time = time.time()
                    message_id = result.get('message_id', '未知')
                    logger.info(f"消息发送成功，消息ID: {message_id}")
                else:
                    # 处理发送失败的情况
                    if "too many messages" in result.get('error', '').lower():
                        # 频率限制，等待1分钟后重试
                        logger.warning("达到钉钉频率限制，等待60秒后重试")
                        time.sleep(60)
                        
                        # 重新添加到队列（增加重试次数）
                        message_data['retries'] += 1
                        if message_data['retries'] < self.max_retries:
                            self.queue.put(message_data)
                            logger.info(f"消息重新添加到队列，重试次数: {message_data['retries']}")
                        else:
                            logger.error(f"消息重试次数超过限制，已丢弃: {message_data['message_key']}")
                    else:
                        # 其他错误，根据重试次数决定是否重试
                        message_data['retries'] += 1
                        if message_data['retries'] < self.max_retries:
                            # 等待指数退避时间后重试
                            wait_time = 2 ** message_data['retries']
                            logger.info(f"发送失败，等待 {wait_time} 秒后重试")
                            time.sleep(wait_time)
                            self.queue.put(message_data)
                        else:
                            logger.error(f"消息发送失败且重试次数用尽: {message_data['message_key']}, 错误: {result.get('error')}")
                
                # 标记任务完成
                self.queue.task_done()
                
            except Empty:
                # 队列为空，继续等待
                continue
            except Exception as e:
                logger.error(f"处理消息队列时发生错误: {str(e)}")
                time.sleep(1)
        
        # 队列处理完成
        self.is_processing = False
        logger.info("消息队列处理完成")

# 全局消息队列实例
message_queue = MessageQueue()

# 消息去重
class MessageDeduplicator:
    def __init__(self):
        self.sent_messages: Set[str] = set()
        self.cleanup_interval = 300  # 5分钟清理一次
        self.last_cleanup = time.time()
    
    def should_send(self, message_key: str) -> bool:
        # 定期清理过期消息
        if time.time() - self.last_cleanup > self.cleanup_interval:
            self.sent_messages.clear()
            self.last_cleanup = time.time()
            logger.info("已清理消息去重缓存")
        
        # 检查是否已发送过
        if message_key in self.sent_messages:
            logger.info(f"消息去重：已发送过相同内容的消息 {message_key}")
            return False
        
        self.sent_messages.add(message_key)
        return True

deduplicator = MessageDeduplicator()

class DingTalkSender:
    @staticmethod
    def generate_signature(secret: str) -> dict:
        timestamp = str(round(time.time() * 1000))
        secret_enc = secret.encode('utf-8')
        string_to_sign = f'{timestamp}\n{secret}'
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return {"timestamp": timestamp, "sign": sign}

    @staticmethod
    def send_to_dingtalk(webhook_url: str, secret: str, message: dict) -> dict:
        try:
            signature = DingTalkSender.generate_signature(secret)
            full_url = f"{webhook_url}&timestamp={signature['timestamp']}&sign={signature['sign']}"
            
            headers = {'Content-Type': 'application/json'}
            logger.info(f"发送钉钉消息: {json.dumps(message, ensure_ascii=False)}")
            response = requests.post(full_url, data=json.dumps(message, ensure_ascii=False), headers=headers, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('errcode') == 0:
                logger.info("钉钉消息发送成功")
                return {"success": True, "message_id": result.get('msgid', '未知')}
            else:
                logger.error(f"钉钉API错误: {result.get('errmsg')}")
                return {"success": False, "error": result.get('errmsg')}
                
        except Exception as e:
            logger.error(f"发送钉钉消息失败: {str(e)}")
            return {"success": False, "error": str(e)}

def detect_project_type(data: dict) -> str:
    """检测项目类型并返回项目名称"""
    # 优先检测Emby项目 - 精确检测Emby特有字段结构
    if 'Event' in data and 'Item' in data and 'Server' in data:
        # 检查是否是Emby的特定事件结构
        event = data.get('Event', '')
        item = data.get('Item', {})
        server = data.get('Server', {})
        
        # Emby的典型字段结构
        if (isinstance(item, dict) and 'Id' in item and 'Type' in item and 
            isinstance(server, dict) and 'Name' in server and 'Id' in server):
            return 'Emby'
    
    # 将数据转换为字符串进行其他项目检测
    data_str = json.dumps(data, ensure_ascii=False).lower()
    
    # CAS项目检测 - 扩展关键词范围
    cas_keywords = [
        'strm', 'strm文件', '生成strm', '文件完成',
        '总文件数', '成功数', '失败数', '跳过数',
        'cloud189', '天翼云盘', 'auto-save', '追更',
        '抓娃娃', '姐姐妹妹', '冷宫', 'maternity', 'matron',
        '通知emby入库成功', '自动重命名', '重命名', '入库成功',
        '生成strm文件完成', '资源名'
    ]
    
    # 检查数据中是否包含CAS关键词
    for keyword in cas_keywords:
        if keyword in data_str:
            return 'CAS'
    
    # 监控项目检测
    if any(keyword in data_str for keyword in ['prometheus', 'alertmanager', 'zabbix', '监控', '告警']):
        return '监控'
    
    # Git项目检测
    if any(keyword in data_str for keyword in ['git', 'repository', 'commit', 'push', 'pull']):
        return 'Git'
    
    # Docker项目检测
    if any(keyword in data_str for keyword in ['docker', 'container', 'kubernetes']):
        return 'Docker'
    
    # 备份项目检测
    if any(keyword in data_str for keyword in ['backup', '备份', 'restic', 'borg']):
        return '备份'
    
    # 如果无法识别具体项目，返回"系统"
    return '系统'

def parse_emby_notification(data: dict) -> str:
    """专门解析Emby通知，返回中文格式的消息内容"""
    event = data.get('Event', '')
    user = data.get('User', {}).get('Name', '未知用户')
    item = data.get('Item', {})
    item_name = item.get('Name', '')
    item_type = item.get('Type', '')
    series_name = item.get('SeriesName', '')
    season_name = item.get('SeasonName', '')
    production_year = item.get('ProductionYear', '')
    device = data.get('Session', {}).get('DeviceName', '未知设备')
    client = data.get('Session', {}).get('Client', '未知客户端')
    
    # 事件类型映射为中文
    event_translations = {
        'playback.start': '开始播放',
        'playback.stop': '停止播放', 
        'playback.pause': '暂停播放',
        'playback.unpause': '继续播放',
        'playback.resume': '继续播放',
        'user.authenticated': '用户登录',
        'user.locked.out': '用户锁定',
        'session.start': '会话开始',
        'session.end': '会话结束',
        'system.notification': '系统通知',
        'library.new': '新增媒体',
        'item.added': '项目添加'
    }
    
    event_cn = event_translations.get(event, event)
    
    # 构建中文内容
    content_parts = []
    
    # 处理新增入库事件
    if event in ['library.new', 'item.added']:
        content_parts.append(f"**🎉 新增内容入库**")
        
        if item_type == 'Movie':
            # 电影类型
            if item_name and production_year:
                content_parts.append(f"**🎬 电影:** {item_name} ({production_year})")
            elif item_name:
                content_parts.append(f"**🎬 电影:** {item_name}")
                
        elif item_type == 'Episode':
            # 剧集类型
            if series_name:
                content_parts.append(f"**📺 剧集:** {series_name}")
            if season_name:
                content_parts.append(f"**📁 季度:** {season_name}")
            if item_name:
                content_parts.append(f"**🎞️ 集数:** {item_name}")
                
        elif item_type == 'Series':
            # 系列类型
            if item_name and production_year:
                content_parts.append(f"**📺 剧集系列:** {item_name} ({production_year})")
            elif item_name:
                content_parts.append(f"**📺 剧集系列:** {item_name}")
                
        elif item_type == 'Season':
            # 季度类型
            if series_name:
                content_parts.append(f"**📺 剧集:** {series_name}")
            if item_name:
                content_parts.append(f"**📁 季度:** {item_name}")
        
        # 添加媒体类型
        type_translations = {
            'Movie': '电影',
            'Episode': '剧集',
            'Series': '剧集系列', 
            'Season': '季度',
            'Audio': '音乐',
            'Book': '书籍'
        }
        type_cn = type_translations.get(item_type, item_type)
        content_parts.append(f"**📄 类型:** {type_cn}")
        
    else:
        # 播放相关事件
        if user and user != '未知用户':
            content_parts.append(f"**👤 用户:** {user}")
        
        if item_name:
            if series_name:
                content_parts.append(f"**📺 剧集:** {series_name} - {item_name}")
            else:
                content_parts.append(f"**📄 内容:** {item_name}")
        
        if device and device != '未知设备':
            content_parts.append(f"**💻 设备:** {device} ({client})")
    
    content_parts.append(f"**🎯 事件:** {event_cn}")
    
    return "\n".join(content_parts)

def parse_cas_notification(data: dict) -> str:
    """专门解析CAS通知，返回中文格式的消息内容"""
    # 从text字段提取信息（如果是简单文本格式）
    text_content = data.get('text', '')
    if text_content:
        # 处理简单的文本消息
        return text_content
    
    # 检查是否是自动重命名消息
    title = data.get('Title', '')
    if '自动重命名' in title:
        description = data.get('Description', '')
        # 提取重命名信息
        if '→' in description:
            parts = description.split('→')
            if len(parts) == 2:
                old_name = parts[0].strip()
                new_name = parts[1].strip()
                return f"**🔄 自动重命名完成**\n\n**📁 原文件名:** {old_name}\n**📁 新文件名:** {new_name}"
    
    # CAS事件类型映射为中文
    event_translations = {
        'library.new': '新文件入库',
        'library.add': '文件添加',
        'library.update': '文件更新',
        'library.delete': '文件删除',
        'item.added': '项目添加',
        'item.updated': '项目更新',
        'item.removed': '项目移除'
    }
    
    event = data.get('Event', '')
    description = data.get('Description', '')
    
    # 转换事件为中文
    event_cn = event_translations.get(event, event)
    
    # 构建中文内容
    content_parts = []
    
    if title:
        # 清理标题中的英文信息
        title_cn = title.replace('新 ', '新增').replace('S1, Ep', '第1季 第').replace('剧', '剧集')
        content_parts.append(f"**📺 标题:** {title_cn}")
    
    if description:
        # 转换描述中的英文日期时间格式
        desc_cn = description
        # 简单的日期时间转换
        desc_cn = desc_cn.replace('Monday', '星期一').replace('Tuesday', '星期二').replace('Wednesday', '星期三')\
                        .replace('Thursday', '星期四').replace('Friday', '星期五').replace('Saturday', '星期六')\
                        .replace('Sunday', '星期日').replace('上午', 'AM').replace('下午', 'PM')
        content_parts.append(f"**📝 描述:** {desc_cn}")
    
    content_parts.append(f"**🎯 事件:** {event_cn}")
    
    # 如果有Item信息，添加详细信息
    item = data.get('Item', {})
    if item:
        series_name = item.get('SeriesName', '')
        if series_name:
            content_parts.append(f"**🎬 系列:** {series_name}")
        
        season_name = item.get('SeasonName', '')
        if season_name:
            content_parts.append(f"**📁 季度:** {season_name}")
    
    return "\n".join(content_parts)

def parse_notification(data: dict) -> dict:
    """统一解析通知"""
    # 检测项目类型
    project_type = detect_project_type(data)
    logger.info(f"检测到项目类型: {project_type}")
    
    # 根据项目类型使用不同的解析器
    if project_type == 'Emby':
        message = parse_emby_notification(data)
    elif project_type == 'CAS':
        message = parse_cas_notification(data)
    else:
        # 其他项目的消息内容提取
        message = data.get('message', data.get('content', data.get('text', data.get('body', ''))))
        
        # 如果消息是字典，转换为格式化的字符串
        if isinstance(message, dict):
            message_parts = []
            for key, value in message.items():
                if isinstance(value, (str, int, float, bool)):
                    message_parts.append(f"**{key}:** {value}")
            message = "\n".join(message_parts) if message_parts else ""
        
        # 如果没有消息内容，使用数据中的其他信息
        if not message:
            message_parts = []
            for key, value in data.items():
                if key not in ['title', 'Title', 'subject'] and isinstance(value, (str, int, float, bool)):
                    message_parts.append(f"**{key}:** {value}")
            message = "\n".join(message_parts) if message_parts else "收到新的通知"
    
    # 根据项目类型和状态设置图标
    message_str = str(message).lower()
    
    # CAS项目特殊图标处理
    if project_type == 'CAS':
        if any(word in message_str for word in ['失败', '错误', 'error']):
            icon = '❌'
        elif any(word in message_str for word in ['重命名完成', '自动重命名']):
            icon = '🔄'  # 重命名使用循环箭头
        elif any(word in message_str for word in ['入库成功', '完成', '成功', 'success']):
            icon = '✅'
        elif any(word in message_str for word in ['生成strm', 'strm文件']):
            icon = '📄'  # 文件生成使用文档图标
        elif any(word in message_str for word in ['警告', 'warning']):
            icon = '⚠️'
        else:
            icon = '📥'  # 默认CAS图标
    
    # 其他项目图标处理
    else:
        icon_configs = {
            'Emby': '🎬',
            '监控': '⚠️',
            'Git': '🔗',
            'Docker': '🐳',
            '备份': '💾',
            '系统': '📢'
        }
        icon = icon_configs.get(project_type, '📢')
        
        # 其他项目的状态检测
        if any(word in message_str for word in ['失败', '错误', 'error']):
            icon = '❌'
        elif any(word in message_str for word in ['完成', '成功', 'success']):
            icon = '✅'
        elif any(word in message_str for word in ['警告', 'warning']):
            icon = '⚠️'
    
    # 修复：确保所有项目类型都有正确的标题格式
    title = f"{icon} {project_type}通知"
    
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"""## {title}

{message}

**⏰ 时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        }
    }

def generate_message_key(data: dict) -> str:
    """生成消息去重键"""
    # 基于关键信息生成唯一键
    project_type = detect_project_type(data)
    
    if project_type == 'CAS':
        # 对于CAS项目，使用text内容或提取资源名
        text_content = data.get('text', '')
        if text_content:
            # 从text中提取关键信息
            resource_match = re.search(r'资源名:([^,\n]+)', text_content)
            if resource_match:
                resource_name = resource_match.group(1).strip()
                return f"CAS_{resource_name}"
            
            # 提取电影/剧集名
            movie_match = re.search(r'([^(]+)\([^)]+\)', text_content)
            if movie_match:
                movie_name = movie_match.group(1).strip()
                return f"CAS_{movie_name}"
            
            # 使用整个text内容的哈希
            return f"CAS_{hash(text_content)}"
        
        # 检查自动重命名消息
        title = data.get('Title', '')
        if '自动重命名' in title:
            description = data.get('Description', '')
            if '→' in description:
                parts = description.split('→')
                if len(parts) == 2:
                    new_name = parts[1].strip()
                    return f"CAS_Rename_{new_name}"
        
        event = data.get('Event', '')
        item_name = data.get('Item', {}).get('Name', '')
        series_name = data.get('Item', {}).get('SeriesName', '')
        return f"CAS_{event}_{series_name}_{item_name}"
    
    elif project_type == 'Emby':
        event = data.get('Event', '')
        user = data.get('User', {}).get('Name', '')
        item_name = data.get('Item', {}).get('Name', '')
        item_type = data.get('Item', {}).get('Type', '')
        
        # 对于新增入库事件，使用更具体的键
        if event in ['library.new', 'item.added']:
            return f"Emby_Add_{item_type}_{item_name}"
        else:
            return f"Emby_{event}_{user}_{item_name}"
    
    else:
        # 对于其他类型，使用数据哈希
        return f"{project_type}_{hash(json.dumps(data, sort_keys=True))}"

@app.get("/")
async def root():
    return {"message": "全类型通知中转服务", "version": "7.9.0"}

@app.get("/health")
async def health():
    return {
        "status": "healthy", 
        "service": "dingtalk-notification-proxy",
        "queue_size": message_queue.queue.qsize(),
        "is_processing": message_queue.is_processing
    }

@app.post("/api/v1/webhook/dingtalk")
async def dingtalk_webhook_v1(data: dict):
    return await process_webhook(data)

@app.post("/webhook")
async def webhook_compatible(data: dict):
    return await process_webhook(data)

@app.post("/dingtalk")
async def dingtalk_compatible(data: dict):
    return await process_webhook(data)

@app.post("/{path:path}")
async def catch_all_webhook(path: str, data: dict):
    logger.info(f"收到路径 /{path} 的webhook请求")
    return await process_webhook(data)

async def process_webhook(data: dict):
    try:
        logger.info(f"收到原始数据: {json.dumps(data, ensure_ascii=False)}")
        
        if not dingtalk_config.get('url') or not dingtalk_config.get('secret'):
            raise HTTPException(status_code=500, detail="钉钉配置不完整")
        
        # 检查消息去重
        message_key = generate_message_key(data)
        logger.info(f"生成消息键: {message_key}")
        
        if not deduplicator.should_send(message_key):
            return {
                "success": True,
                "message": "重复消息，已忽略",
                "queue_size": message_queue.queue.qsize()
            }
        
        # 解析通知
        dingtalk_message = parse_notification(data)
        
        logger.info(f"准备发送的钉钉消息: {json.dumps(dingtalk_message, ensure_ascii=False)}")
        
        # 添加到消息队列（异步发送）
        message_queue.add_message(
            dingtalk_config["url"],
            dingtalk_config["secret"],
            dingtalk_message,
            message_key
        )
        
        return {
            "success": True,
            "message": "通知已加入发送队列",
            "queue_size": message_queue.queue.qsize()
        }
            
    except Exception as e:
        logger.error(f"处理webhook失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
