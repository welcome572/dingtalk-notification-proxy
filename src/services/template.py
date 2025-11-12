from jinja2 import Template
from datetime import datetime
from typing import Dict, Any

class TemplateService:
    @staticmethod
    def render_markdown_template(template: str, data: Dict[str, Any]) -> str:
        """
        渲染markdown模板
        """
        try:
            jinja_template = Template(template)
            rendered = jinja_template.render(
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                **data
            )
            return rendered
        except Exception as e:
            return f"模板渲染错误: {str(e)}\n原始数据: {data}"

    @staticmethod
    def get_default_template(level: str) -> str:
        """
        获取默认模板
        """
        templates = {
            "info": """## ℹ️ 信息通知

**内容:** {{ content }}

**时间:** {{ timestamp }}
**来源:** {{ source }}""",

            "warning": """## ⚠️ 警告通知

**内容:** {{ content }}

**时间:** {{ timestamp }}
**级别:** {{ level }}
**来源:** {{ source }}""",

            "error": """## ❌ 错误通知

**内容:** {{ content }}

**时间:** {{ timestamp }}
**级别:** {{ level }}
**来源:** {{ source }}"""
        }
        return templates.get(level, templates["info"])
