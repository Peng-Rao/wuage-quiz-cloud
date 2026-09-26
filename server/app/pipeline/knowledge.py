"""知识点标注：大模型按学科与教材为每道题标注 1–3 个知识点。

当前没有正式的知识树，知识点名称由大模型按教材常用说法生成；
接入知识树后，改为先检索候选再让大模型从候选中选择，保证名称统一、可挂到章节。
"""

import hashlib
import logging
import re

from sqlalchemy import select

from ..config import get_settings
from ..db import DraftQuestion, ParseJob, SessionLocal
from .llm import LLMError, chat_json, describe

log = logging.getLogger(__name__)

BATCH = 15
MAX_KPS = 3

SYSTEM = """你是中国中小学{subject}教研员，熟悉{textbook}教材的知识体系。请为下面每道{stage}{grade}试题标注考查的知识点，只输出 JSON：
{{"questions": [{{"no": 1, "kps": ["集合的基本运算", "一元二次不等式"]}}]}}

要求：
1. 每题 1–{max_kps} 个知识点，按考查的主次排序，最主要的放在第一个。
2. 使用教材章节中的规范名称，简洁（一般不超过 12 个字），不要写成句子，不要带编号。
3. 同一个知识点在不同题中写法保持一致。
4. 每道输入的题都要输出，no 与输入一致。"""


def kp_id(subject: str, name: str) -> str:
    """同一学科下同名知识点得到相同 id，便于统计与筛选。"""
    return "kp_" + hashlib.md5(f"{subject}:{name}".encode()).hexdigest()[:10]


def _clean(name: str) -> str:
    name = re.sub(r"^\s*[\d一二三四五六七八九十]+[.、．)）]\s*", "", str(name)).strip(" ，,；;。")
    return name[:30]


def _payload(qs: list[DraftQuestion]) -> str:
    rows = []
    for q in qs:
        opts = "；".join(f"{'ABCDEFGH'[i]}．{o}" for i, o in enumerate(q.options))
        rows.append(f"第 {q.no} 题（{q.type}）：{q.stem[:400]}" + (f"\n选项：{opts[:200]}" if opts else ""))
    return "\n\n".join(rows)


async def tag_job(job_id: str, *, only_missing: bool = False) -> tuple[int, int]:
    """为本卷题目标注知识点，返回 (标注成功的题数, 知识点种数)。失败的批次跳过，不影响其他批次。"""
    settings = get_settings()
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        meta = dict((job.meta if job else None) or {})
        qs = list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id).order_by(DraftQuestion.no)))
    if only_missing:
        qs = [q for q in qs if not q.knowledge_points]
    subject = meta.get("subject") or ""
    system = SYSTEM.format(subject=subject or "学科", textbook=meta.get("textbook") or "现行", stage=meta.get("stage") or "",
                           grade=meta.get("grade") or "", max_kps=MAX_KPS)
    tagged: dict[str, list[dict]] = {}
    for i in range(0, len(qs), BATCH):
        chunk = qs[i:i + BATCH]
        try:
            data = await chat_json(system, _payload(chunk), settings, purpose="knowledge")
        except LLMError as e:
            log.warning("知识点标注失败（第 %d 批）：%s", i // BATCH + 1, describe(e))
            continue
        by_no = {q.no: q for q in chunk}
        for item in (data or {}).get("questions", []) if isinstance(data, dict) else []:
            q = by_no.get(item.get("no")) if isinstance(item, dict) else None
            if q is None:
                continue
            names: list[str] = []
            for n in item.get("kps") or []:
                n = _clean(n)
                if n and n not in names:
                    names.append(n)
            if names:
                tagged[q.id] = [{"id": kp_id(subject, n), "name": n} for n in names[:MAX_KPS]]
    with SessionLocal() as s:
        for qid, kps in tagged.items():
            q = s.get(DraftQuestion, qid)
            if q is not None:
                q.knowledge_points = kps
        s.commit()
    return len(tagged), len({k["id"] for kps in tagged.values() for k in kps})
