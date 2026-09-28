"""任务队列与后台 Worker。任务状态全部落库，队列里只放（任务类型, id）。

- 未配置 REDIS_URL：进程内队列，任务在网页服务进程中执行（开发、单机试用）；服务重启后按数据库状态重新排队。
- 配置 REDIS_URL：Redis Streams 消费组。网页服务只负责入队，由 `python -m app.worker` 启动的独立进程执行，
  可同时运行多个（docker compose up -d --scale worker=N），每个进程同时执行 WORKER_CONCURRENCY 个任务：
  - 同一任务在队列中只会有一条（入队标记 + Lua 原子写入），重复提交、重启恢复都不会重复执行；
  - 执行中的任务定期续约；Worker 崩溃或被强制停止后，超过 RECLAIM_IDLE_MS 未续约的任务由其他 Worker 接手，从头重做；
  - 正常停止时，把手上的任务立即交还给其他 Worker；
  - 每个 Worker 进程启动时按数据库补排未完成的任务（Redis 数据丢失、从进程内队列切换过来时）。
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from .config import get_settings
from .db import ParseJob, SessionLocal
from .pipeline.answer import run_answer_task
from .pipeline.run import run_job_safely, run_knowledge_task

log = logging.getLogger(__name__)

# 执行中的任务续约间隔；超过 RECLAIM_IDLE_MS 未续约视为 Worker 已失联
HEARTBEAT_S = 20.0
RECLAIM_IDLE_MS = 120_000
# 取新任务时的阻塞等待
BLOCK_MS = 5_000
# Worker 存活标记的有效期
ALIVE_TTL_S = 60
# 入队标记的兜底有效期（正常情况下随确认一起删除）
MARK_TTL_S = 7 * 24 * 3600
GROUP = "workers"

# 入队标记不存在时才写入 Stream，保证同一任务只排队一次
_ENQUEUE_LUA = """
if redis.call('SET', KEYS[1], ARGV[3], 'NX', 'EX', ARGV[4]) then
  return redis.call('XADD', KEYS[2], '*', 'kind', ARGV[1], 'id', ARGV[2])
end
return false
"""
# 确认、删除消息与删除入队标记一次完成，避免残留标记挡住之后的重新入队
_ACK_LUA = """
redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
redis.call('XDEL', KEYS[1], ARGV[2])
redis.call('DEL', KEYS[2])
return 1
"""


async def run_task(kind: str, job_id: str) -> None:
    if kind == "answer":
        await run_answer_task(job_id)
    elif kind == "knowledge":
        await run_knowledge_task(job_id)
    elif kind == "eval":
        from .evaluation import run_eval
        await run_eval(job_id)
    else:
        await run_job_safely(job_id)


def unfinished_tasks() -> list[tuple[str, str]]:
    """数据库中未完成的解析任务与 AI 生成答案任务，按创建时间排序。"""
    with SessionLocal() as s:
        pending = s.scalars(
            select(ParseJob.id).where(ParseJob.status.in_(["queued", "running"])).order_by(ParseJob.created_at)
        ).all()
        answering = [j.id for j in s.scalars(select(ParseJob).where(ParseJob.answer_task.is_not(None)))
                     if (j.answer_task or {}).get("status") in ("queued", "running")]
    return [("parse", j) for j in pending] + [("answer", j) for j in answering]


def async_client(url: str) -> Any:
    """Worker 用的异步客户端：读取超时须长于取任务时的阻塞等待，否则空闲等待会被当作超时。"""
    import redis.asyncio
    return redis.asyncio.Redis.from_url(url, decode_responses=True, socket_timeout=BLOCK_MS / 1000 + 10)


# ---------------- 进程内队列 ----------------

class LocalQueue:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []

    def put(self, kind: str, job_id: str) -> None:
        self.queue.put_nowait((kind, job_id))

    async def _loop(self) -> None:
        while True:
            kind, job_id = await self.queue.get()
            try:
                await run_task(kind, job_id)
            except Exception:
                log.exception("任务 %s（%s）异常", job_id, kind)
            finally:
                self.queue.task_done()

    def start(self, concurrency: int) -> None:
        for kind, job_id in unfinished_tasks():
            log.info("重新排队未完成的任务 %s（%s）", job_id, kind)
            self.put(kind, job_id)
        self.tasks = [asyncio.create_task(self._loop()) for _ in range(concurrency)]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


# ---------------- Redis ----------------

class RedisQueue:
    """入队用同步客户端（接口处理函数多为同步函数，在线程池中执行）。"""

    def __init__(self, sync_client: Any, prefix: str) -> None:
        self.r = sync_client
        self.prefix = prefix
        self.stream = f"{prefix}:tasks"
        self._enqueue = self.r.register_script(_ENQUEUE_LUA)

    @classmethod
    def from_url(cls, url: str, prefix: str) -> "RedisQueue":
        import redis
        return cls(redis.Redis.from_url(url, decode_responses=True, socket_timeout=10), prefix)

    def mark_key(self, kind: str, job_id: str) -> str:
        return f"{self.prefix}:queued:{kind}:{job_id}"

    def alive_pattern(self, host: str = "*") -> str:
        return f"{self.prefix}:worker:{host}"

    def put(self, kind: str, job_id: str) -> bool:
        """返回是否新入队；已在队列中（尚未执行完）时返回 False。"""
        return bool(self._enqueue(keys=[self.mark_key(kind, job_id), self.stream],
                                  args=[kind, job_id, str(time.time()), MARK_TTL_S]))

    def info(self) -> dict[str, Any]:
        workers = [json.loads(v) for k in self.r.scan_iter(self.alive_pattern(), count=100) if (v := self.r.get(k))]
        try:
            groups = {g["name"]: g for g in self.r.xinfo_groups(self.stream)}
        except Exception:  # Stream 尚未创建
            groups = {}
        g = groups.get(GROUP, {})
        return {"queue": "redis", "workers": len(workers), "slots": sum(w.get("concurrency", 0) for w in workers),
                "busy": sum(w.get("busy", 0) for w in workers), "waiting": g.get("lag") or 0,
                "running": g.get("pending", 0)}


class RedisConsumer:
    """一个 Worker 进程：concurrency 个执行槽共用一个消费者名称。"""

    def __init__(self, queue: RedisQueue, async_client: Any, concurrency: int, name: str | None = None,
                 task_runner: Callable[[str, str], Awaitable[None]] = run_task) -> None:
        self.q = queue
        self.r = async_client
        self.concurrency = concurrency
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self.run_task = task_runner
        self.in_flight: dict[str, tuple[str, str]] = {}
        self.stopping = False
        self.started = time.time()
        self.tasks: list[asyncio.Task] = []
        self._ack = self.r.register_script(_ACK_LUA)

    async def start(self) -> None:
        try:
            await self.r.xgroup_create(self.q.stream, GROUP, id="0", mkstream=True)
        except Exception as e:  # BUSYGROUP：消费组已存在
            if "BUSYGROUP" not in str(e):
                raise
        # 补排数据库中未完成、但不在队列里的任务（已在队列中的由入队标记去重）
        for kind, job_id in await asyncio.to_thread(unfinished_tasks):
            if await asyncio.to_thread(self.q.put, kind, job_id):
                log.info("补排未完成的任务 %s（%s）", job_id, kind)
        self.tasks = [asyncio.create_task(self._slot()) for _ in range(self.concurrency)]
        self.tasks.append(asyncio.create_task(self._alive()))
        log.info("Worker %s 已启动：%d 个执行槽", self.name, self.concurrency)

    async def _next(self) -> tuple[str, dict | None, bool] | None:
        # 先接手失联 Worker 留下的任务，再取新任务
        claimed = await self.r.xautoclaim(self.q.stream, GROUP, self.name, min_idle_time=RECLAIM_IDLE_MS, count=1)
        if claimed and claimed[1]:
            msg_id, fields = claimed[1][0]
            return msg_id, fields, True
        got = await self.r.xreadgroup(GROUP, self.name, {self.q.stream: ">"}, count=1, block=BLOCK_MS)
        if got and got[0][1]:
            msg_id, fields = got[0][1][0]
            return msg_id, fields, False
        return None

    async def _slot(self) -> None:
        while not self.stopping:
            try:
                nxt = await self._next()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("读取任务队列失败，稍后重试")
                await asyncio.sleep(2)
                continue
            if nxt is None:
                # 正常情况下取任务已阻塞等待过；个别客户端（如测试用的 fakeredis）会立即返回，短暂让出避免空转
                await asyncio.sleep(0.05)
                continue
            msg_id, fields, reclaimed = nxt
            if not fields:  # 消息已被删除
                await self.r.xack(self.q.stream, GROUP, msg_id)
                continue
            await self._handle(msg_id, fields["kind"], fields["id"], reclaimed)

    async def _handle(self, msg_id: str, kind: str, job_id: str, reclaimed: bool) -> None:
        self.in_flight[msg_id] = (kind, job_id)
        await self._publish()
        beat = asyncio.create_task(self._keep_alive(msg_id))
        log.info("%s任务 %s（%s）", "接手失联 Worker 的" if reclaimed else "开始", job_id, kind)
        try:
            await self.run_task(kind, job_id)
        except asyncio.CancelledError:
            raise  # 停止时交还任务，不确认
        except Exception:
            log.exception("任务 %s（%s）异常", job_id, kind)
        else:
            log.info("完成任务 %s（%s）", job_id, kind)
        finally:
            beat.cancel()
        # 执行完（含失败，失败状态已写入数据库）才确认；被取消时留在待确认列表中，由其他 Worker 接手
        await self._ack(keys=[self.q.stream, self.q.mark_key(kind, job_id)], args=[GROUP, msg_id])
        self.in_flight.pop(msg_id, None)
        await self._publish()

    async def _keep_alive(self, msg_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                # 重新认领给自己，空闲时间归零
                await self.r.xclaim(self.q.stream, GROUP, self.name, 0, [msg_id], justid=True)
            except Exception:
                log.warning("任务续约失败（%s）", msg_id, exc_info=True)

    async def _publish(self) -> None:
        """存活标记，带当前状态（执行槽数、执行中的任务数），供健康检查汇总。"""
        state = {"name": self.name, "concurrency": self.concurrency, "busy": len(self.in_flight), "started": self.started}
        try:
            await self.r.set(f"{self.q.prefix}:worker:{self.name}", json.dumps(state), ex=ALIVE_TTL_S)
        except Exception:
            log.warning("更新 Worker 存活标记失败", exc_info=True)

    async def _alive(self) -> None:
        while True:
            await self._publish()
            await asyncio.sleep(HEARTBEAT_S)

    async def stop(self) -> None:
        self.stopping = True
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        # 手上未完成的任务立即交还：把空闲时间设为已超时，其他 Worker 下次取任务时即可接手
        for msg_id in list(self.in_flight):
            try:
                await self.r.xclaim(self.q.stream, GROUP, self.name, 0, [msg_id], idle=RECLAIM_IDLE_MS, justid=True)
                log.info("交还未完成的任务 %s", self.in_flight[msg_id][1])
            except Exception:
                log.warning("交还任务失败（%s），将在 %d 秒后被接手", msg_id, RECLAIM_IDLE_MS // 1000)
        try:
            await self.r.delete(f"{self.q.prefix}:worker:{self.name}")
        except Exception:
            pass


# ---------------- 对外接口 ----------------

class Worker:
    """接口层使用的入队入口；按配置选择进程内队列或 Redis。"""

    def __init__(self) -> None:
        self.local: LocalQueue | None = None
        self.redis: RedisQueue | None = None
        self.consumer: RedisConsumer | None = None

    def _put(self, kind: str, job_id: str) -> None:
        if self.redis is not None:
            self.redis.put(kind, job_id)
        else:
            if self.local is None:
                self.local = LocalQueue()
            self.local.put(kind, job_id)

    def enqueue(self, job_id: str) -> None:
        self._put("parse", job_id)

    def enqueue_answers(self, job_id: str) -> None:
        self._put("answer", job_id)

    def enqueue_knowledge(self, job_id: str) -> None:
        self._put("knowledge", job_id)

    def enqueue_eval(self, run_id: str) -> None:
        self._put("eval", run_id)

    def start(self) -> None:
        """网页服务启动时调用：未配置 Redis 时在本进程执行任务；配置了 Redis 时默认只入队（RUN_WORKER=true 时也在本进程执行）。"""
        s = get_settings()
        concurrency = max(1, s.worker_concurrency)
        if not s.redis_url:
            # 队列绑定创建它的事件循环；每次启动（如测试中多次启动应用）都需要新建
            self.local = LocalQueue()
            self.local.start(concurrency)
            return
        self.redis = RedisQueue.from_url(s.redis_url, s.redis_prefix)
        if s.run_worker:
            self.consumer = RedisConsumer(self.redis, async_client(s.redis_url), concurrency)
            asyncio.get_running_loop().create_task(self.consumer.start())

    async def stop(self) -> None:
        if self.local is not None:
            await self.local.stop()
        if self.consumer is not None:
            await self.consumer.stop()

    def info(self) -> dict[str, Any]:
        if self.redis is None:
            return {"queue": "local", "slots": max(1, get_settings().worker_concurrency)}
        try:
            return self.redis.info()
        except Exception as e:
            return {"queue": "redis", "error": f"无法连接 Redis：{type(e).__name__}"}


worker = Worker()


# ---------------- 独立 Worker 进程 ----------------

async def _serve() -> None:
    s = get_settings()
    queue = RedisQueue.from_url(s.redis_url, s.redis_prefix)
    consumer = RedisConsumer(queue, async_client(s.redis_url), max(1, s.worker_concurrency))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await consumer.start()
    await stop.wait()
    log.info("正在停止 Worker %s", consumer.name)
    await consumer.stop()


def _check() -> int:
    """容器健康检查：本容器中的 Worker 存活标记存在。"""
    s = get_settings()
    queue = RedisQueue.from_url(s.redis_url, s.redis_prefix)
    return 0 if next(queue.r.scan_iter(queue.alive_pattern(f"{socket.gethostname()}-*"), count=100), None) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="后台 Worker：从 Redis 任务队列中取出解析、生成答案等任务执行")
    parser.add_argument("--check", action="store_true", help="检查本机 Worker 是否在运行（用于容器健康检查）")
    args = parser.parse_args()
    if not get_settings().redis_url:
        sys.exit("未配置 REDIS_URL：没有 Redis 时任务在网页服务进程中执行，无需单独启动 Worker")
    if args.check:
        sys.exit(_check())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .db import init_db
    init_db()
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
