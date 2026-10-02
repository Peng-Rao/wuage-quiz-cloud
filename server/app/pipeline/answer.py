"""AI 生成答案：为缺少答案的草稿题生成答案与解析。

- 每道题单独请求，便于逐题展示进度、单题失败不影响其他题；并发数由 ANSWER_CONCURRENCY 控制。
- 配置了看图模型（VISION_MODEL）时，含图的题改用该模型并附上配图；未配置时大模型看不到图片，
  含图的题会注明「答案可能不准确」，由老师核对。
- 结果写入 answer / analysis，并标记 answer_source=ai；老师修改后变为 manual。
"""

import asyncio
import logging
import re
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db import DraftQuestion, ParseJob, SessionLocal
from ..usage import current_job
from .llm import LLMError, chat_json, describe
from .vision import image_inputs

log = logging.getLogger(__name__)

LETTERS = "ABCDEFGH"

SYSTEM = """你是经验丰富的中国中小学{subject}教师，请为下面这道{stage}{grade}试题给出标准答案和解析，只输出 JSON：
{{"answer": "...", "analysis": "...", "uncertain": false, "reason": ""}}

要求：
1. 单选题 answer 只写一个选项字母，如 "B"；多选题写全部正确选项字母，按字母顺序，如 "ACD"。
2. 填空题 answer 按空的顺序写出每空答案，多个空用「；」分隔。
3. 解答题 answer 写各小问的最终结论，如「（1）…；（2）…」；analysis 写关键解题步骤，条理清楚、简洁。
4. 数学、物理、化学公式用 LaTeX，行内公式用 $...$ 包裹；化学式可直接写，如 H₂O 或 $\\mathrm{{H_2O}}$。
5. 题目的配图（如有）按顺序附在题目文字之后，请结合图片作答。题目依赖你看不到的图片、表格或材料，或题目文字不完整导致无法确定答案时，仍给出最可能的答案，
   并将 uncertain 设为 true，在 reason 中用一句话说明原因。
6. 不要编造题目中没有给出的条件。"""


def _question_payload(q: DraftQuestion, with_images: int = 0) -> str:
    lines = [f"题型：{q.type}", f"题干：{q.stem}"]
    if q.options:
        lines.append("选项：")
        lines += [f"{LETTERS[i]}．{o}" for i, o in enumerate(q.options)]
    if with_images:
        lines.append(f"（本题配图 {with_images} 张，按顺序附在后面）")
    elif q.images:
        lines.append(f"（本题含 {len(q.images)} 张配图，你无法看到图片内容）")
    return "\n".join(lines)


def _normalize(q: DraftQuestion, data: Any, saw_images: bool = False) -> tuple[str, str | None, str | None]:
    """校验并整理大模型输出，返回 (答案, 解析, 提示)。"""
    if not isinstance(data, dict) or not str(data.get("answer") or "").strip():
        raise ValueError("大模型未返回答案")
    answer = str(data["answer"]).strip()
    analysis = str(data.get("analysis") or "").strip() or None
    notes: list[str] = []
    if q.type in ("单选题", "多选题") and q.options:
        letters = "".join(sorted(set(re.findall(r"[A-H]", answer.upper()))))
        valid = LETTERS[: len(q.options)]
        if not letters or any(ch not in valid for ch in letters) or (q.type == "单选题" and len(letters) != 1):
            notes.append(f"AI 给出的选项「{answer}」不符合题型，请核对")
        else:
            answer = letters
    if data.get("uncertain"):
        notes.append(str(data.get("reason") or "").strip() or "AI 无法确定答案")
    elif q.images and not saw_images:
        notes.append("题目含图，AI 未看到图片，答案可能不准确")
    return answer, analysis, "；".join(notes) or None


def _task_update(job_id: str, **fields: Any) -> None:
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        if job is None:
            return
        job.answer_task = {**(job.answer_task or {}), **fields}
        s.commit()


def _task_incr(job_id: str, key: str) -> None:
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        if job is None:
            return
        task = dict(job.answer_task or {})
        task[key] = task.get(key, 0) + 1
        job.answer_task = task
        s.commit()


async def _answer_one(qid: str, meta: dict, overwrite: bool, sem: asyncio.Semaphore) -> bool:
    settings = get_settings()
    async with sem:
        with SessionLocal() as s:
            q = s.get(DraftQuestion, qid)
            if q is None or (q.answer and not overwrite):
                return True  # 已被删除（合并 / 拆分）或期间已有答案，跳过
            system = SYSTEM.format(subject=meta.get("subject") or "", stage=meta.get("stage") or "",
                                   grade=meta.get("grade") or "")
            keys = list(q.images) if settings.vision_enabled else []
        images: list[str] = []
        if keys:
            try:
                images = await asyncio.to_thread(image_inputs, keys, settings)
            except Exception as e:  # noqa: BLE001  读图失败时退回纯文字作答
                log.warning("第 %s 题配图读取失败，按纯文字作答：%s", qid, describe(e))
        payload = _question_payload(q, len(images))
        try:
            data = await chat_json(system, payload, settings, purpose="answer", images=images or None)
            answer, analysis, note = _normalize(q, data, saw_images=bool(images))
        except (LLMError, ValueError) as e:
            log.warning("AI 解答第 %s 题失败：%s", qid, describe(e))
            return False
        with SessionLocal() as s:
            q = s.get(DraftQuestion, qid)
            if q is None or (q.answer and not overwrite):
                return True
            q.answer, q.analysis = answer, analysis
            q.answer_source, q.answer_note = "ai", note
            q.status = "draft"
            s.commit()
        return True


async def run_answer_task(job_id: str) -> None:
    settings = get_settings()
    current_job.set(job_id)
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        if job is None or not job.answer_task:
            return
        task = dict(job.answer_task)
        meta = dict(job.meta or {})
    if not settings.llm_enabled:
        _task_update(job_id, status="failed", error="未配置大模型，无法生成答案")
        return
    qids: list[str] = task.get("questionIds") or []
    _task_update(job_id, status="running", done=0, failed=0, error=None)
    sem = asyncio.Semaphore(max(1, settings.answer_concurrency))

    async def one(qid: str) -> None:
        ok = await _answer_one(qid, meta, bool(task.get("overwrite")), sem)
        _task_incr(job_id, "done" if ok else "failed")

    try:
        await asyncio.gather(*(one(q) for q in qids))
    except Exception:
        log.exception("AI 生成答案任务 %s 异常", job_id)
        _task_update(job_id, status="failed", error="生成答案时发生内部错误")
        return
    with SessionLocal() as s:
        failed = (s.get(ParseJob, job_id).answer_task or {}).get("failed", 0)  # type: ignore[union-attr]
    _task_update(job_id, status="done", error=f"{failed} 题生成失败，可重试" if failed else None)


def pick_questions(job_id: str, question_ids: list[str] | None, overwrite: bool) -> list[str]:
    """要生成答案的题：指定的题，或本卷所有缺少答案的题；未开启覆盖时跳过已有答案的题。"""
    with SessionLocal() as s:
        qs = list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id).order_by(DraftQuestion.no)))
    wanted = set(question_ids) if question_ids else None
    return [q.id for q in qs if (wanted is None or q.id in wanted) and (overwrite or not q.answer)]
