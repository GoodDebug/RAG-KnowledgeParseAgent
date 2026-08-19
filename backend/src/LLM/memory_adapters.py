# memory_adapters.py
# -*- coding: utf-8 -*-
import functools
import json
import logging
import os
import re

from typing import Dict, List, Optional, Callable

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    message_to_dict,
    messages_from_dict,
)
from langchain_core.chat_history import (
    BaseChatMessageHistory,
    InMemoryChatMessageHistory,
)


def guard_unloaded(func):
    """装饰器：校验实例是否已卸载，提前抛出清晰异常。

    用 functools.wraps 保留原函数 __name__/__doc__ 等元信息，
    使被装饰方法的文档字符串可通过 introspection / help() 查看。
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if self._unloaded:
            raise RuntimeError(
                f"当前 MemoryAdapter 实例已调用 unload() 进入僵尸态，"
                f"不可再执行 {func.__name__}，请新建实例"
            )
        return func(self, *args, **kwargs)
    return wrapper


# ====================== 抽象基类 ======================

class BaseMemoryAdapter:
    """
    对话记忆底层驱动抽象基类。
    与 EmbeddingAdapter / RerankAdapter 设计范式统一。

    - get_session_history(session_id) -> BaseChatMessageHistory
      返回值即为 RunnableWithMessageHistory 所需的
      Callable[[str], BaseChatMessageHistory]，零适配成本。
    - clear_session / list_sessions 提供额外会话管理能力。
    - 支持 with 语句自动清理资源。
    """

    def __init__(self):
        self._unloaded = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.unload()

    @guard_unloaded
    def get_session_history(self, session_id: str) -> BaseChatMessageHistory:
        """获取 LangChain 标准消息历史存储实例"""
        raise NotImplementedError

    @guard_unloaded
    def clear_session(self, session_id: str) -> None:
        """清空单个会话全部历史"""
        raise NotImplementedError

    @guard_unloaded
    def list_sessions(self) -> List[str]:
        """列出全部存在的 session_id"""
        raise NotImplementedError

    @guard_unloaded
    def trim_messages(
        self,
        messages: List[BaseMessage],
        max_token: int = 2000,
        token_counter: Optional[Callable] = None,
    ) -> List[BaseMessage]:
        """通用消息截断，超出 max_token 时按"保留最近"策略截断"""
        if not token_counter:
            return messages
        from langchain_core.messages import trim_messages as _trim
        return _trim(
            messages,
            max_tokens=max_token,
            strategy="last",
            token_counter=token_counter,
            include_system=True,
        )

    @guard_unloaded
    def unload(self):
        """安全释放资源，幂等"""
        if self._unloaded:
            logging.warning("MemoryAdapter 已执行 unload，无需重复释放")
            return
        self._unloaded = True
        logging.info("MemoryAdapter 资源释放完成")


# ====================== 自定义 BaseChatMessageHistory 实现 ======================
# 用于支持 JSON 文件持久化的消息历史存储

class JsonFileChatMessageHistory(BaseChatMessageHistory):
    """
    将消息历史以 JSON 格式持久化到单个文件。
    每次 add_message 会立即写盘，确保进程异常退出时数据不丢。

    消息序列化使用 LangChain 标准的 message_to_dict / messages_from_dict，
    保证与 InMemoryChatMessageHistory 的格式兼容。
    """

    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self._messages: List[BaseMessage] = []
        self._load()

    def _load(self):
        """从 JSON 文件恢复消息"""
        if not os.path.exists(self.file_path):
            self._messages = []
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._messages = messages_from_dict(raw)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logging.warning("读取历史文件失败 %s，将重新创建: %s", self.file_path, e)
            self._messages = []

    def _save(self):
        """将消息写入 JSON 文件"""
        raw = [message_to_dict(m) for m in self._messages]
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    @property
    def messages(self) -> List[BaseMessage]:
        return self._messages

    def add_message(self, message: BaseMessage) -> None:
        self._messages.append(message)
        self._save()

    def clear(self) -> None:
        self._messages = []
        if os.path.exists(self.file_path):
            os.remove(self.file_path)


# ====================== 内存实现 ======================

class InMemoryAdapter(BaseMemoryAdapter):
    """
    内存级对话记忆。
    所有 session 存储在进程内存 Dict 中，重启后丢失。
    适合开发调试或对持久化无要求的场景。
    """
    def __init__(self):
        super().__init__()
        self._store: Dict[str, InMemoryChatMessageHistory] = {}

    @guard_unloaded
    def get_session_history(self, session_id: str) -> InMemoryChatMessageHistory:
        if session_id not in self._store:
            self._store[session_id] = InMemoryChatMessageHistory()
        return self._store[session_id]

    @guard_unloaded
    def clear_session(self, session_id: str) -> None:
        if session_id in self._store:
            self._store[session_id].clear()
            del self._store[session_id]

    def list_sessions(self) -> List[str]:
        return list(self._store.keys())


# ====================== JSON 文件持久化实现 ======================

class FileMemoryAdapter(BaseMemoryAdapter):
    """
    JSON 文件持久化对话记忆。
    每个 session_id 对应一个 .json 文件，存放在 root_dir 目录下。
    每次写入立即刷盘，进程异常退出不丢数据。
    进程重启后历史仍在，适合需要持久记忆的场景。

    :param root_dir: 会话历史文件存放根目录，默认为 ./chat_sessions
    """
    def __init__(self, root_dir: str = "./chat_sessions"):
        super().__init__()
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)

    def _get_file_path(self, session_id: str) -> str:
        return os.path.join(self.root_dir, f"{session_id}.json")

    @guard_unloaded
    def get_session_history(self, session_id: str) -> JsonFileChatMessageHistory:
        return JsonFileChatMessageHistory(self._get_file_path(session_id))

    @guard_unloaded
    def clear_session(self, session_id: str) -> None:
        file_path = self._get_file_path(session_id)
        if os.path.exists(file_path):
            os.remove(file_path)

    @guard_unloaded
    def list_sessions(self) -> List[str]:
        sessions = []
        if not os.path.isdir(self.root_dir):
            return sessions
        for fname in os.listdir(self.root_dir):
            if fname.endswith(".json"):
                sid = fname[:-5]
                sessions.append(sid)
        return sessions


# ====================== MySQL 持久化实现（Spec-B） ======================
# 会话定位：内部 Key `user_{user_id}_{key}` → sessions 表 (user_id, key) 唯一行。

class SqlChatMessageHistory(BaseChatMessageHistory):
    """
    将消息历史持久化到 MySQL `messages` 表（Spec-B）。

    - add_message 立即 INSERT + commit，返回 messages.id（供 router 层按 id 补写 source_refs）。
    - messages 属性按 id ASC 读回（id 即插入顺序）。
    - role 映射：HumanMessage→'user'，AIMessage→'assistant'，SystemMessage→'system'。
    - db.models 采用方法内懒 import，保持本模块顶层不依赖 DB 栈。
    """

    # DB role 字符串 → LangChain 消息类的反向映射（读回时还原消息对象）
    _ROLE_TO_CLASS = {
        "user": HumanMessage,
        "assistant": AIMessage,
        "system": SystemMessage,
    }

    def __init__(self, session_id: int, session_factory: Callable):
        """绑定到某个 sessions 行。

        :param session_id: sessions 表主键（int），该会话所有消息挂在它名下
        :param session_factory: 返回 SQLAlchemy Session 的可调用对象（一般传 db.SessionLocal），
            每个操作现场开一个短生命周期会话、用后即关
        """
        super().__init__()
        self.session_id = session_id
        self.session_factory = session_factory

    @staticmethod
    def _role_of(message: BaseMessage) -> str:
        """LangChain 消息 → DB role 字符串（正向映射，写库时用）。

        非 Human/System 的消息（如 ToolMessage、泛化消息）一律归为 'assistant'，
        保证不会写入 messages.role 枚举之外的非法值。
        """
        if isinstance(message, HumanMessage):
            return "user"
        if isinstance(message, SystemMessage):
            return "system"
        return "assistant"

    def add_message(self, message: BaseMessage) -> int:
        """插入一条消息并立即 commit。

        返回 messages.id（自增主键）——供 router 层落库后按该 id
        `UPDATE messages SET source_refs=?` 补写引用来源。
        每次操作独立开 session，finally 关闭，不长期占用连接。
        """
        from db.models import Message  # 懒 import，顶层不依赖 DB 栈

        db = self.session_factory()
        try:
            row = Message(
                session_id=self.session_id,          # 归属哪个会话
                role=self._role_of(message),          # LangChain 消息 → 'user'/'assistant'/'system'
                content=message.content or "",        # 空内容也落库，避免 NULL
            )
            db.add(row)
            db.commit()    # 立即持久化，进程异常退出不丢
            db.refresh(row)  # 回读自增 id
            return row.id
        finally:
            db.close()

    @property
    def messages(self) -> List[BaseMessage]:
        """读回该会话全部消息，按 id 升序（id 即插入顺序，保证多轮不乱序）。

        反向映射 role 字符串 → LangChain 消息对象；
        source_refs/intent/feedback 为 router 层专属字段，不参与 LLM 上下文，故不带回。
        """
        from db.models import Message  # 懒 import

        db = self.session_factory()
        try:
            rows = (
                db.query(Message)
                .filter(Message.session_id == self.session_id)
                .order_by(Message.id.asc())   # 升序 = 时间顺序
                .all()
            )
            return [
                self._ROLE_TO_CLASS.get(r.role, HumanMessage)(content=r.content)
                for r in rows
            ]
        finally:
            db.close()

    def clear(self) -> None:
        """清空该会话全部消息（只删 messages 行，保留 sessions 会话外壳）。"""
        from db.models import Message  # 懒 import

        db = self.session_factory()
        try:
            db.query(Message).filter(Message.session_id == self.session_id).delete()
            db.commit()
        finally:
            db.close()


class MysqlMemoryAdapter(BaseMemoryAdapter):
    """
    MySQL 持久化对话记忆（Spec-B）。

    - get_session_history(internal_key)：解析 `user_{uid}_{key}` → 按 (user_id, key)
      查/建 sessions 行（并发建会话用 IntegrityError 兜底重查），返回 SqlChatMessageHistory。
    - clear_session：删除该会话 messages，保留 sessions 行。
    - list_sessions：补齐基类契约。
    """

    # 内部 Key 格式：user_{user_id}_{key}（user_id 前缀实现多用户会话隔离）
    _KEY_RE = re.compile(r"^user_(\d+)_(.+)$")

    def __init__(self, session_factory: Optional[Callable] = None):
        """初始化 MySQL 记忆适配器。

        :param session_factory: 会话工厂（可调用，返回 SQLAlchemy Session）；
            缺省时懒加载 db.SessionLocal。测试可传入自定义工厂注入测试会话。
        """
        super().__init__()
        self._session_factory = session_factory

    def _get_factory(self) -> Callable:
        """返回会话工厂；未显式注入时懒加载 db.SessionLocal（顶层不依赖 DB 栈）。"""
        if self._session_factory is None:
            from db import SessionLocal  # 懒 import

            self._session_factory = SessionLocal
        return self._session_factory

    @staticmethod
    def parse_key(internal_key: str) -> tuple:
        """解析 `user_{user_id}_{key}` → (user_id:int, key:str 截断 64)。

        非法格式（缺 user_ 前缀 / user_id 非数字）抛 ValueError；
        key 截断到 64 字符与 DDL `sessions.key VARCHAR(64)` 对齐，防超长 session 撑爆列。
        """
        m = MysqlMemoryAdapter._KEY_RE.match(internal_key)
        if not m:
            raise ValueError(f"非法会话 Key: {internal_key}")
        user_id = int(m.group(1))
        key = m.group(2)[:64]
        return user_id, key

    @guard_unloaded
    def get_session_history(self, internal_key: str) -> SqlChatMessageHistory:
        """按内部 Key 定位会话，缺失自动创建，返回该会话的消息历史对象。

        流程：解析 (user_id, key) → 按唯一约束 (user_id, key) 查 sessions 行；
        查不到则 INSERT 新会话（title='新会话'）。并发建同一会话时，后插入者撞
        uk_sessions_user_key 唯一约束 → IntegrityError → 回滚后重查（此时另一请求已提交）。
        返回 SqlChatMessageHistory(sessions.id, factory)——消息都挂在 sessions.id 上。
        """
        from sqlalchemy.exc import IntegrityError

        from db.models import Session as DBSession  # 别名规避与 sqlalchemy.orm.Session 重名

        user_id, key = self.parse_key(internal_key)
        factory = self._get_factory()
        db = factory()
        try:
            # 1) 查：按 (user_id, key) 唯一定位会话行
            row = db.query(DBSession).filter_by(user_id=user_id, key=key).first()
            if row is None:
                # 2) 建：首次访问自动创建会话外壳
                row = DBSession(user_id=user_id, key=key, title="新会话")
                db.add(row)
                try:
                    db.commit()
                    db.refresh(row)
                except IntegrityError:
                    # 3) 并发竞态兜底：唯一约束冲突说明已被并发请求创建，回滚重查
                    db.rollback()
                    row = db.query(DBSession).filter_by(user_id=user_id, key=key).one()
            return SqlChatMessageHistory(row.id, factory)
        finally:
            db.close()

    @guard_unloaded
    def clear_session(self, internal_key: str) -> None:
        """清空某会话的消息（保留 sessions 会话外壳；会话不存在则 no-op）。"""
        from db.models import Message, Session as DBSession

        user_id, key = self.parse_key(internal_key)
        db = self._get_factory()()
        try:
            srow = db.query(DBSession).filter_by(user_id=user_id, key=key).first()
            if srow is not None:
                db.query(Message).filter(Message.session_id == srow.id).delete()
                db.commit()
        finally:
            db.close()

    @guard_unloaded
    def list_sessions(self) -> List[str]:
        """列出全部会话的内部 Key（`user_{user_id}_{key}`），补齐基类契约。"""
        from db.models import Session as DBSession

        db = self._get_factory()()
        try:
            rows = db.query(DBSession).all()
            return [f"user_{r.user_id}_{r.key}" for r in rows]
        finally:
            db.close()


# ====================== 工厂函数 ======================

def create_memory_adapter(
    interface_format: str,
    **kwargs,
) -> BaseMemoryAdapter:
    """
    工厂函数：根据 interface_format 创建对应的记忆适配器实例。

    :param interface_format: "memory" → 内存模式 | "file" → JSON 文件持久化模式 | "mysql" → MySQL 持久化模式
    :param kwargs: 传递给具体实现类的参数（如 file 模式的 root_dir；mysql 模式可传 session_factory）
    :return: BaseMemoryAdapter 实例
    """
    fmt = interface_format.strip().lower()
    if fmt == "memory":
        return InMemoryAdapter()
    elif fmt == "file":
        return FileMemoryAdapter(**kwargs)
    elif fmt == "mysql":
        return MysqlMemoryAdapter(**kwargs)
    else:
        raise ValueError(f"不支持的记忆适配器类型：{interface_format}")
