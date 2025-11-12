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
    version="7.9.8"
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

def parse_utc_time(utc_time_str: str) -> str:
    """将UTC时间字符串转换为本地时间字符串 - 使用简单可靠的方法"""
    try:
        logger.info(f"开始转换UTC时间: {utc_time_str}")
        
        # 处理Emby的UTC时间格式 (2025-11-12T14:08:45.9182263Z)
        if utc_time_str.endswith('Z'):
            # 去掉微秒部分，保留到秒
            if '.' in utc_time_str:
                utc_time_str = utc_time_str.split('.')[0] + 'Z'
            
        # 使用简单可靠的方法：手动解析并添加8小时（北京时间）
        if utc_time_str.endswith('Z'):
            # 提取时间部分
            time_part = utc_time_str.replace('Z', '')
            # 解析为datetime对象（无时区信息）
            utc_time = datetime.strptime(time_part, '%Y-%m-%dT%H:%M:%S')
            # 手动添加8小时（UTC+8）
            local_time = utc_time + timedelta(hours=8)
        else:
            # 如果不是Z结尾，尝试其他方法
            utc_time = datetime.fromisoformat(utc_time_str.replace('Z', '+00:00'))
            local_time = utc_time.astimezone()
        
        # 格式化为中文友好的时间格式
        result = local_time.strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"时间转换结果: UTC {utc_time_str} -> 本地 {result}")
        return result
        
    except Exception as e:
        logger.error(f"时间转换失败: {utc_time_str}, 错误: {e}")
        # 如果转换失败，返回当前时间
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"使用当前时间作为后备: {current_time}")
        return current_time

def detect_project_type(data: dict) -> str:
    """检测项目类型并返回项目名称"""
    # Emby特有的事件类型全集
    emby_events = [
        # 播放控制事件
        'playback.start', 'playback.stop', 'playback.pause', 'playback.unpause',
        'playback.resume', 'playback.progress',
        
        # 用户事件
        'user.authenticated', 'user.locked.out', 'user.created', 'user.deleted',
        'user.updated', 'user.password.changed', 'user.policy.updated',
        
        # 会话事件
        'session.start', 'session.end',
        
        # 系统事件
        'system.notification', 'system.task.completed', 'system.webhook.test',
        'system.webhook.failed', 'system.plugin.installed', 'system.plugin.uninstalled',
        'system.plugin.updated', 'system.restart', 'system.shutdown',
        
        # 媒体库事件
        'library.new', 'library.add', 'library.update', 'library.delete',
        'item.added', 'item.updated', 'item.removed', 'item.rate',
        
        # 认证事件
        'authentication.succeeded', 'authentication.failed', 'authentication.revoked',
        
        # 设备事件
        'device.offline', 'device.online', 'device.access',
        
        # 转码事件
        'transcode.start', 'transcode.stop', 'transcode.failed',
        
        # 订阅事件
        'subscription.added', 'subscription.removed', 'subscription.updated',
        
        # 同步事件
        'sync.job.created', 'sync.job.updated', 'sync.job.deleted',
        
        # 活动日志事件
        'activitylog.entry.created'
    ]
    
    event = data.get('Event', '')
    user = data.get('User', {})
    server = data.get('Server', {})
    session = data.get('Session', {})
    item = data.get('Item', {})
    
    # 方法1: 检查事件类型是否为Emby特有事件
    if event in emby_events:
        return 'Emby'
    
    # 方法2: 检查Emby特有的字段结构组合
    if (isinstance(server, dict) and server.get('Name') and 
        isinstance(server, dict) and server.get('Id') and
        'Version' in server):
        return 'Emby'
    
    # 方法3: 检查Session结构（Emby特有）
    if (isinstance(session, dict) and 
        any(key in session for key in ['Client', 'DeviceName', 'DeviceId', 'RemoteEndPoint'])):
        return 'Emby'
    
    # 方法4: 检查Item结构（Emby媒体项目）
    if (isinstance(item, dict) and 
        any(key in item for key in ['Id', 'Type', 'Name', 'ServerId', 'MediaType'])):
        return 'Emby'
    
    # 方法5: 检查User结构（Emby用户）
    if (isinstance(user, dict) and 
        any(key in user for key in ['Name', 'Id', 'ServerId', 'HasPassword', 'LastLoginDate'])):
        return 'Emby'
    
    # 将数据转换为字符串进行其他项目检测
    data_str = json.dumps(data, ensure_ascii=False).lower()
    
    # ddns-go项目检测
    if any(keyword in data_str for keyword in ['公网ip变了', 'ddns-go', 'ip地址', '域名更新']):
        return 'DDNS-Go'
    
    # TaoSync项目检测
    if 'taosync' in data_str or ('同步' in data_str and '来源目录' in data_str):
        return 'TaoSync'
    
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
    
    # 如果已经是钉钉格式消息，直接转发
    if data.get('msgtype') in ['markdown', 'text', 'link']:
        return '钉钉格式'
    
    # 如果无法识别具体项目，返回"系统"
    return '系统'

def parse_ddnsgo_notification(data: dict) -> str:
    """专门解析DDNS-Go通知，返回中文格式的消息内容"""
    # 如果已经是钉钉格式的markdown消息，直接使用
    if data.get('msgtype') == 'markdown':
        markdown_data = data.get('markdown', {})
        title = markdown_data.get('title', 'DDNS-Go通知')
        text = markdown_data.get('text', '')
        
        # 提取IP地址和更新结果
        ip_match = re.search(r'IPv4地址[：:]\s*([\d.]+)', text)
        result_match = re.search(r'域名更新结果[：:]\s*([^\s]+)', text)
        
        content_parts = []
        content_parts.append("**🌐 DDNS动态域名更新**")
        
        if ip_match:
            content_parts.append(f"**📡 IPv4地址:** {ip_match.group(1)}")
        
        if result_match:
            result = result_match.group(1)
            if result == '成功':
                content_parts.append("**✅ 更新状态:** 域名解析成功")
            else:
                content_parts.append(f"**❌ 更新状态:** {result}")
        
        return "\n".join(content_parts)
    
    # 如果是其他格式，尝试提取信息
    text_content = data.get('text', '')
    if '公网IP变了' in text_content:
        # 提取IP地址信息
        ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', text_content)
        content_parts = []
        content_parts.append("**🌐 公网IP变更通知**")
        
        if ip_match:
            content_parts.append(f"**📡 新的IPv4地址:** {ip_match.group(1)}")
        
        if '成功' in text_content:
            content_parts.append("**✅ 状态:** 域名解析更新成功")
        elif '失败' in text_content:
            content_parts.append("**❌ 状态:** 域名解析更新失败")
        
        return "\n".join(content_parts)
    
    return "DDNS-Go动态域名更新通知"

def parse_taosync_notification(data: dict) -> str:
    """专门解析TaoSync同步通知，返回中文格式的消息内容"""
    text_content = data.get('text', '')
    content = data.get('content', '')
    
    # 构建中文内容
    content_parts = []
    content_parts.append("**🔄 文件同步完成**")
    
    # 提取状态信息
    if '成功' in text_content:
        content_parts.append("**✅ 状态:** 同步成功")
    elif '失败' in text_content:
        content_parts.append("**❌ 状态:** 同步失败")
    else:
        content_parts.append("**ℹ️ 状态:** 同步完成")
    
    # 解析详细内容
    if content:
        # 提取源目录和目标目录
        source_match = re.search(r'来源目录为\s*([^、]+?)\s*、', content)
        target_match = re.search(r'目标目录为\s*([^、]+?)\s*的', content)
        
        if source_match:
            source_dir = source_match.group(1).strip()
            content_parts.append(f"**📁 源目录:** `{source_dir}`")
        if target_match:
            target_dir = target_match.group(1).strip()
            content_parts.append(f"**📂 目标目录:** `{target_dir}`")
        
        # 提取文件统计
        files_match = re.search(r'共\s*(\d+)\s*个需要同步的文件', content)
        success_match = re.search(r'成功\s*(\d+)\s*个', content)
        fail_match = re.search(r'失败\s*(\d+)\s*个', content)
        
        if files_match:
            content_parts.append(f"**📊 文件总数:** {files_match.group(1)}个")
        if success_match:
            content_parts.append(f"**✅ 成功数:** {success_match.group(1)}个")
        if fail_match:
            fail_count = fail_match.group(1)
            if fail_count != '0':
                content_parts.append(f"**❌ 失败数:** {fail_count}个")
        
        # 提取耗时和文件大小
        time_match = re.search(r'耗时[：:]\s*([^，]+?)\s*，', content)
        size_match = re.search(r'同步\s*([\d.]+)\s*([KMGT]?B)\s*文件', content)
        
        if time_match:
            content_parts.append(f"**⏱️ 同步耗时:** {time_match.group(1)}")
        if size_match:
            content_parts.append(f"**📦 同步大小:** {size_match.group(1)} {size_match.group(2)}")
    
    return "\n".join(content_parts)

def parse_emby_notification(data: dict) -> str:
    """专门解析Emby通知，返回中文格式的消息内容"""
    # ... 保持原有的Emby解析逻辑不变 ...
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
    server_name = data.get('Server', {}).get('Name', '未知服务器')
    remote_ip = data.get('Session', {}).get('RemoteEndPoint', '未知IP')
    
    # Emby事件类型完整映射为中文
    event_translations = {
        'playback.start': '开始播放',
        'playback.stop': '停止播放', 
        'playback.pause': '暂停播放',
        'playback.unpause': '继续播放',
        'user.authenticated': '用户登录',
        'item.added': '新增媒体',
        # ... 其他事件映射 ...
    }
    
    event_cn = event_translations.get(event, event)
    
    # 构建中文内容
    content_parts = []
    
    if event in ['user.authenticated', 'authentication.succeeded', 'authentication.failed']:
        content_parts.append("**🔐 用户登录通知**")
        content_parts.append(f"**👤 用户:** {user}")
        content_parts.append(f"**🖥️ 服务器:** {server_name}")
        # ... 其他登录相关逻辑 ...
    
    elif event.startswith('playback.'):
        content_parts.append("**🎬 播放事件**")
        # ... 播放相关逻辑 ...
    
    elif event in ['library.new', 'item.added', 'library.add']:
        content_parts.append("**🎉 新增内容入库**")
        # ... 新增内容逻辑 ...
    
    else:
        content_parts.append("**📢 Emby事件**")
        if user and user != '未知用户':
            content_parts.append(f"**👤 用户:** {user}")
        if item_name:
            content_parts.append(f"**📄 内容:** {item_name}")
    
    content_parts.append(f"**🎯 事件:** {event_cn}")
    
    return "\n".join(content_parts)

def parse_cas_notification(data: dict) -> str:
    """专门解析CAS通知，返回中文格式的消息内容"""
    # ... 保持原有的CAS解析逻辑不变 ...
    text_content = data.get('text', '')
    if text_content:
        return text_content
    
    title = data.get('Title', '')
    if '自动重命名' in title:
        description = data.get('Description', '')
        if '→' in description:
            parts = description.split('→')
            if len(parts) == 2:
                old_name = parts[0].strip()
                new_name = parts[1].strip()
                return f"**🔄 自动重命名完成**\n\n**📁 原文件名:** {old_name}\n**📁 新文件名:** {new_name}"
    
    return "CAS通知"

def parse_notification(data: dict) -> dict:
    """统一解析通知"""
    # 检测项目类型
    project_type = detect_project_type(data)
    logger.info(f"检测到项目类型: {project_type}")
    
    # 如果已经是钉钉格式消息，直接使用
    if project_type == '钉钉格式':
        logger.info("检测到钉钉格式消息，直接转发")
        return data
    
    # 根据项目类型使用不同的解析器
    if project_type == 'Emby':
        message = parse_emby_notification(data)
    elif project_type == 'CAS':
        message = parse_cas_notification(data)
    elif project_type == 'TaoSync':
        message = parse_taosync_notification(data)
    elif project_type == 'DDNS-Go':
        message = parse_ddnsgo_notification(data)
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
    
    # DDNS-Go项目特殊图标处理
    if project_type == 'DDNS-Go':
        if any(word in message_str for word in ['失败', '错误', 'error']):
            icon = '❌'
        elif any(word in message_str for word in ['成功', '完成']):
            icon = '✅'
        else:
            icon = '🌐'
    
    # Emby项目特殊图标处理
    elif project_type == 'Emby':
        if '用户登录' in message or 'user.authenticated' in str(data.get('Event', '')):
            icon = '🔐'
        elif '开始播放' in message or 'playback.start' in str(data.get('Event', '')):
            icon = '🎬'
        elif '新增媒体' in message or 'item.added' in str(data.get('Event', '')):
            icon = '🎉'
        elif any(word in message_str for word in ['失败', '错误', 'error']):
            icon = '❌'
        elif any(word in message_str for word in ['完成', '成功', 'success']):
            icon = '✅'
        elif any(word in message_str for word in ['警告', 'warning']):
            icon = '⚠️'
        else:
            icon = '🎬'
    
    # CAS项目特殊图标处理
    elif project_type == 'CAS':
        if any(word in message_str for word in ['失败', '错误', 'error']):
            icon = '❌'
        elif any(word in message_str for word in ['重命名完成', '自动重命名']):
            icon = '🔄'
        elif any(word in message_str for word in ['入库成功', '完成', '成功', 'success']):
            icon = '✅'
        elif any(word in message_str for word in ['生成strm', 'strm文件']):
            icon = '📄'
        elif any(word in message_str for word in ['警告', 'warning']):
            icon = '⚠️'
        else:
            icon = '📥'
    
    # TaoSync项目特殊图标处理
    elif project_type == 'TaoSync':
        if any(word in message_str for word in ['失败', '错误', 'error']):
            icon = '❌'
        elif any(word in message_str for word in ['成功', '完成']):
            icon = '✅'
        else:
            icon = '🔄'
    
    # 其他项目图标处理
    else:
        icon_configs = {
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
    
    # 如果是钉钉格式消息，直接返回
    if project_type == '钉钉格式':
        return data
    
    # 修复：确保所有项目类型都有正确的标题格式
    title = f"{icon} {project_type}通知"
    
    # 优化时间显示 - 使用事件发生时间而不是当前时间
    event_time = data.get('Date', '')
    if event_time:
        display_time = parse_utc_time(event_time)
        logger.info(f"使用事件时间: {event_time} -> {display_time}")
    else:
        display_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"使用当前时间: {display_time}")
    
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"""## {title}

{message}

**⏰ 时间:** {display_time}"""
        }
    }

def generate_message_key(data: dict) -> str:
    """生成消息去重键"""
    # 基于关键信息生成唯一键
    project_type = detect_project_type(data)
    
    # DDNS-Go项目去重键
    if project_type == 'DDNS-Go':
        # 提取IP地址作为去重键
        if data.get('msgtype') == 'markdown':
            text = data.get('markdown', {}).get('text', '')
            ip_match = re.search(r'IPv4地址[：:]\s*([\d.]+)', text)
            if ip_match:
                return f"DDNS-Go_{ip_match.group(1)}"
        return f"DDNS-Go_{hash(json.dumps(data, sort_keys=True))}"
    
    elif project_type == 'TaoSync':
        # 对于TaoSync项目，使用目录路径和文件数生成唯一键
        content = data.get('content', '')
        if content:
            source_match = re.search(r'来源目录为\s*([^、]+?)\s*、', content)
            if source_match:
                source_dir = source_match.group(1).strip()
                files_match = re.search(r'共\s*(\d+)\s*个需要同步的文件', content)
                if files_match:
                    return f"TaoSync_{source_dir}_{files_match.group(1)}"
                return f"TaoSync_{source_dir}"
        return f"TaoSync_{hash(json.dumps(data, sort_keys=True))}"
    
    elif project_type == 'CAS':
        # ... 保持原有的CAS去重逻辑 ...
        text_content = data.get('text', '')
        if text_content:
            resource_match = re.search(r'资源名:([^,\n]+)', text_content)
            if resource_match:
                resource_name = resource_match.group(1).strip()
                return f"CAS_{resource_name}"
            return f"CAS_{hash(text_content)}"
        return f"CAS_{hash(json.dumps(data, sort_keys=True))}"
    
    elif project_type == 'Emby':
        # ... 保持原有的Emby去重逻辑 ...
        event = data.get('Event', '')
        user = data.get('User', {}).get('Name', '')
        item_name = data.get('Item', {}).get('Name', '')
        return f"Emby_{event}_{user}_{item_name}"
    
    else:
        # 对于其他类型，使用数据哈希
        return f"{project_type}_{hash(json.dumps(data, sort_keys=True))}"

@app.get("/")
async def root():
    return {"message": "全类型通知中转服务", "version": "7.9.8"}

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
EOF
