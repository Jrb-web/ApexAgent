"""
ApexAgent 数据持久化层
- ini/config.ini: 设置持久化
- ini/conversations/*.json: 对话历史
"""

import os
import json
import uuid
import configparser
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT_DIR, "ini", "config.ini")
CONVERSATIONS_DIR = os.path.join(ROOT_DIR, "ini", "conversations")


# ============================================================
#  配置管理（INI）
# ============================================================
class ConfigManager:
    DEFAULT = {
        "Panel": {"opacity": "0.45", "x": "-1", "y": "-1"},
        "General": {"current_conversation": ""},
    }

    @classmethod
    def load(cls):
        cfg = configparser.ConfigParser()
        cfg.read_dict(cls.DEFAULT)
        if os.path.exists(CONFIG_FILE):
            cfg.read(CONFIG_FILE, encoding="utf-8")
        return cfg

    @classmethod
    def save(cls, cfg):
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)

    @classmethod
    def get_opacity(cls):
        cfg = cls.load()
        return float(cfg.get("Panel", "opacity", fallback="0.45"))

    @classmethod
    def set_opacity(cls, value):
        cfg = cls.load()
        cfg.set("Panel", "opacity", str(value))
        cls.save(cfg)

    @classmethod
    def get_panel_position(cls):
        cfg = cls.load()
        try:
            x = int(cfg.get("Panel", "x", fallback="-1"))
            y = int(cfg.get("Panel", "y", fallback="-1"))
            return (x, y) if x >= 0 and y >= 0 else None
        except ValueError:
            return None

    @classmethod
    def set_panel_position(cls, x, y):
        cfg = cls.load()
        cfg.set("Panel", "x", str(x))
        cfg.set("Panel", "y", str(y))
        cls.save(cfg)

    @classmethod
    def get_current_conversation(cls):
        cfg = cls.load()
        return cfg.get("General", "current_conversation", fallback="")

    @classmethod
    def set_current_conversation(cls, conv_id):
        cfg = cls.load()
        cfg.set("General", "current_conversation", str(conv_id))
        cls.save(cfg)


# ============================================================
#  对话存储（JSON）
# ============================================================
class ConversationStore:
    @staticmethod
    def _ensure_dir():
        os.makedirs(CONVERSATIONS_DIR, exist_ok=True)

    @classmethod
    def create(cls):
        cls._ensure_dir()
        now = datetime.now()
        conv = {
            "id": uuid.uuid4().hex[:12],
            "title": f"对话 {now.strftime('%m-%d %H:%M')}",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "messages": [],
        }
        cls._write(conv)
        return conv

    @classmethod
    def load(cls, conv_id):
        path = os.path.join(CONVERSATIONS_DIR, f"{conv_id}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    @classmethod
    def save(cls, conv):
        cls._ensure_dir()
        conv["updated_at"] = datetime.now().isoformat()
        cls._write(conv)

    @classmethod
    def _write(cls, conv):
        path = os.path.join(CONVERSATIONS_DIR, f"{conv['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conv, f, ensure_ascii=False, indent=2)

    @classmethod
    def list_all(cls):
        cls._ensure_dir()
        conversations = []
        for filename in os.listdir(CONVERSATIONS_DIR):
            if filename.endswith(".json"):
                path = os.path.join(CONVERSATIONS_DIR, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        conv = json.load(f)
                        conversations.append(conv)
                except Exception:
                    pass
        conversations.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return conversations

    @classmethod
    def delete(cls, conv_id):
        path = os.path.join(CONVERSATIONS_DIR, f"{conv_id}.json")
        if os.path.exists(path):
            os.remove(path)

    @classmethod
    def update_title(cls, conv_id, first_message):
        conv = cls.load(conv_id)
        if conv and conv.get("title", "").startswith("对话 "):
            preview = first_message[:20].replace("\n", " ")
            conv["title"] = preview if preview else conv["title"]
            cls._write(conv)