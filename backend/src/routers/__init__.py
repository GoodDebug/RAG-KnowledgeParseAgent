from .chat import router as chat_router
from .ingest import router as ingest_router
from .crawler import router as crawler_router
from .auth import router as auth_router
from .documents import router as documents_router
from .sessions import router as sessions_router
from .sessions import sessions_list_router as sessions_list_router
from .novel import router as novel_router  # 子任务 10：小说解构 API
