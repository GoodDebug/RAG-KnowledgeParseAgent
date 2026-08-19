# -*- coding: utf-8 -*-
"""
novel 解构编排器 —— 图的"点火开关"。

run_job(job_id)：从 MySQL 读任务 → 构建初始 NovelJobState → 用 InMemorySaver 驱动 JobGraph
一步步跑完，并把每一步的 streaming 事件打到日志（09 子任务接 SSE）。

LangGraph 学习点（运行时配置）：
  - `configurable.thread_id`：一个 job 对应一个"线程"（checkpoint 按 thread 隔离）；
  - `recursion_limit`：图最大 superstep 数（默认 25，这里放宽到 200 防大书误触发）；
  - `max_concurrency`：同一 superstep 最多并行执行的节点数（LLM 限流，避免 API 并发爆发）；
  - `stream_mode=["updates", "custom"]`：流式看每步输出（updates=节点粒度）;
  - `graph.get_state(config)`：跑完后从 checkpointer 快照取最终状态。

**快照生命周期约定（生效决议，防未来踩坑）**：
  - `run_job` **每次调用新建** `build_job_graph()` → 新 `InMemorySaver`；
    函数返回后 graph/saver 无外部引用，交给 GC 回收 —— 快照内存自动释放，无需手动清理。
  - **禁止**把 graph/saver 做成模块级单例共享：InMemorySaver **非线程安全**
    （并发 job 共享 checkpoint 存储有竞态）、快照**永不释放**（多书 OOM）、
    且本架构断点续传**不依赖 checkpointer**（靠 `deconstruct_chapter_state` 自建表驱动）。
  - 若未来确需长驻图（挂 app_state 统一管理）：换**持久化 checkpointer**
    （文件/Redis/Postgres，快照落盘、线程安全），勿共享 InMemorySaver。
"""
import argparse
import asyncio
import logging

from db import SessionLocal
from novel import events
from novel.config import novel_agent_max_concurrency, novel_recursion_limit
from novel.graph.job_graph import build_job_graph
from novel.graph.nodes.job_nodes import JobNotFoundError
from novel.persistence import job_state, repositories
from novel.persistence.job_state import get_job

logger = logging.getLogger("novel.orchestrator")


def load_job_initial_state(job_id: str) -> dict:
    """从 MySQL 读 job 行 → 组装 NovelJobState 的初始值。

    注意：`chapters` 留空 —— 真正的章节清单由 JobGraph 的 load_chapters 节点填充
    （保持"编排器只负责启动，数据读取交给图内节点"的职责分离）。
    """
    db = SessionLocal()
    try:
        job = get_job(db, job_id)
        if job is None:
            raise JobNotFoundError(f"deconstruct_job 不存在：{job_id}")
        return {
            "job_id": job["job_id"],
            "book_id": job["book_id"],
            "user_id": job["user_id"],
            "trigger_type": job["trigger_type"],
            "total_chapters": int(job["total_chapters"] or 0),
            "chapters": [],
            "chapter_results": [],
            "job_status": job["status"],
            "error_msg": job["error_msg"],
        }
    finally:
        db.close()


async def run_job(job_id: str, *, graph=None) -> dict:
    """异步运行一次解构任务；streaming 逐事件打到日志。

    :param graph: 可注入（测试时 mock 简化图）；默认 build_job_graph()
        （每次新建，快照生命周期见模块 docstring）
    :return: 最终 State（dict）—— 供调用方/测试读取归约结果
    """
    # 快照生命周期：默认每次新建 JobGraph + InMemorySaver，函数返回即释放（见模块 docstring）；
    # 注入的 graph（测试用）由调用方自行管理生命周期。
    graph = graph if graph is not None else build_job_graph()
    config = {
        "configurable": {"thread_id": job_id},          # 每 job 一个 checkpoint 线程
        "recursion_limit": novel_recursion_limit(),     # 最大 superstep 数（默认 200）
        "max_concurrency": novel_agent_max_concurrency(),   # LLM 限流（运行时 config，非 compile 参数）
    }
    init = load_job_initial_state(job_id)
    events.publish({"type": "job_started", "job_id": job_id, "book_id": init["book_id"]})

    # 逐 superstep 流式消费：mode="updates" 时 payload 形如 {节点名: 该节点返回的更新}
    async for mode, payload in graph.astream(init, config, stream_mode=["updates", "custom"]):
        logger.info("[stream] mode=%s payload=%s", mode, payload)
        if isinstance(payload, dict):
            # 广播含 chapter_results 的节点业务事件（09 子任务在此接 SSE）
            for update in payload.values():
                if isinstance(update, dict) and "chapter_results" in update:
                    events.publish(update)
    # 最终状态取 checkpointer 快照（astream 的 updates payload 是 {node: out}，不是状态本身）
    snapshot = graph.get_state(config)
    # 生命周期约定：此处返回后，本函数内的 graph/saver 失去引用 → GC 回收，快照自动释放
    return dict(snapshot.values) if snapshot else {}


def _main() -> None:
    """CLI（子任务 12）：python -m novel.orchestrator --book_id doc_1_5 [--dry-run] [--job_id ...]

    --dry-run：只盘点该书待解构章节数，不建 job、不跑 LLM、不写 11 表；
    默认（无 --dry-run）：为该 book 建 job（trigger_type=manual）并 run_job；
    --job_id：续传/重跑指定 job（复用 load_chapters 只挑 pending/failed）。
    """
    parser = argparse.ArgumentParser(description="小说解构编排 CLI")
    parser.add_argument("--book_id", help="doc_{user_id}_{doc_id}")
    parser.add_argument("--dry-run", action="store_true", help="只盘点章节数，不执行解构")
    parser.add_argument("--job_id", default=None, help="续传/重跑指定 job（默认新建）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.job_id:
            asyncio.run(run_job(args.job_id))
            return
        if not args.book_id:
            parser.error("--book_id 必填（或提供 --job_id）")
        chapters = repositories.list_chapters(db, args.book_id)
        print(f"待解构章节: {len(chapters)}")
        if args.dry_run:
            print(f"[dry-run] 仅盘点：book={args.book_id} chapters={len(chapters)}（不跑 LLM/不写表）")
            return
        job_id = job_state.create_job(db, book_id=args.book_id, user_id=0,
                                      trigger_type="manual", total=len(chapters))
        job_state.add_chapter_states(db, job_id, chapters)
        asyncio.run(run_job(job_id))
    finally:
        db.close()


if __name__ == "__main__":
    _main()
