"""进程内任务队列（P1）。

任务状态全部落库，服务重启后会重新排队未完成的任务。
生产环境换成 Redis + 独立 Worker 进程时，只需替换 enqueue 与消费循环。
"""

import asyncio
import logging

from sqlalchemy import select

from .config import get_settings
from .db import ParseJob, SessionLocal
from .pipeline.answer import run_answer_task
from .pipeline.run import run_job_safely, run_knowledge_task

log = logging.getLogger(__name__)


class Worker:
    def __init__(self) -> None:
        # (任务类型, job_id)：parse 解析试卷，answer 为缺答案的题生成答案
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []

    def enqueue(self, job_id: str) -> None:
        self.queue.put_nowait(("parse", job_id))

    def enqueue_answers(self, job_id: str) -> None:
        self.queue.put_nowait(("answer", job_id))

    def enqueue_knowledge(self, job_id: str) -> None:
        self.queue.put_nowait(("knowledge", job_id))

    async def _loop(self) -> None:
        while True:
            kind, job_id = await self.queue.get()
            try:
                if kind == "answer":
                    await run_answer_task(job_id)
                elif kind == "knowledge":
                    await run_knowledge_task(job_id)
                else:
                    await run_job_safely(job_id)
            except Exception:
                log.exception("任务 %s（%s）异常", job_id, kind)
            finally:
                self.queue.task_done()

    def start(self) -> None:
        # 队列绑定创建它的事件循环；每次启动（如测试中多次启动应用）都需要新建
        self.queue = asyncio.Queue()
        with SessionLocal() as s:
            pending = s.scalars(
                select(ParseJob.id).where(ParseJob.status.in_(["queued", "running"])).order_by(ParseJob.created_at)
            ).all()
            answering = [j.id for j in s.scalars(select(ParseJob).where(ParseJob.answer_task.is_not(None)))
                         if (j.answer_task or {}).get("status") in ("queued", "running")]
        for job_id in pending:
            log.info("重新排队未完成的任务 %s", job_id)
            self.enqueue(job_id)
        for job_id in answering:
            log.info("重新排队未完成的 AI 生成答案任务 %s", job_id)
            self.enqueue_answers(job_id)
        self.tasks = [asyncio.create_task(self._loop()) for _ in range(max(1, get_settings().worker_concurrency))]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


worker = Worker()
