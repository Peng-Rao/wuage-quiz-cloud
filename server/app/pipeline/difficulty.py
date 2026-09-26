"""难度评估。难度系数 0–1，越高越难，1 为最难（约等于 1 − 预估得分率）。

最终系数 = 校准( AI_WEIGHT × 大模型评估 + (1 − AI_WEIGHT) × 基线 )：
- 基线：按题型与题位估算，同一题型中越靠后越难（国内试卷的普遍编排习惯），拆题时写入；
- 大模型评估：与知识点标注同一次调用给出；未启用知识点标注时单独评估；
- 校准：评测时用老师调整过的难度拟合 y = a·x + b（见 app/evaluation.py），保存在 app_meta。
"""

import json
import logging
from typing import Any

from sqlalchemy import select, text

from ..config import get_settings
from ..db import AppMeta, DraftQuestion, SessionLocal, engine
from .llm import LLMError, chat_json, describe
from .segment import Question

log = logging.getLogger(__name__)

AI_WEIGHT = 0.7
CALIBRATION_KEY = "difficulty_calibration"

# 各题型第一题与最后一题的难度系数
_RANGE = {"单选题": (0.12, 0.55), "多选题": (0.3, 0.65), "填空题": (0.2, 0.65), "解答题": (0.25, 0.75)}


def estimate(questions: list[Question]) -> list[float]:
    """基线估计。"""
    by_type: dict[str, list[int]] = {}
    for i, q in enumerate(questions):
        by_type.setdefault(q.type, []).append(i)
    coefs = [0.4] * len(questions)
    for qtype, idxs in by_type.items():
        lo, hi = _RANGE.get(qtype, (0.2, 0.65))
        n = len(idxs)
        for rank, i in enumerate(idxs):
            coefs[i] = round(lo + (hi - lo) * (rank / (n - 1) if n > 1 else 0.3), 2)
    return coefs


# ---------------- 校准 ----------------

def get_calibration() -> dict[str, Any] | None:
    with SessionLocal() as s:
        row = s.get(AppMeta, CALIBRATION_KEY)
    if not row:
        return None
    try:
        c = json.loads(row.value)
        return {"a": float(c["a"]), "b": float(c["b"]), "source": c.get("source", "")}
    except (ValueError, KeyError, TypeError):
        return None


def set_calibration(a: float | None, b: float | None = None, *, source: str = "") -> None:
    """保存校准参数；a 为 None 时清除校准。"""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_meta WHERE key = :k"), {"k": CALIBRATION_KEY})
        if a is not None:
            value = json.dumps({"a": round(a, 4), "b": round(b or 0.0, 4), "source": source[:120]}, ensure_ascii=False)
            conn.execute(text("INSERT INTO app_meta (key, value) VALUES (:k, :v)"), {"k": CALIBRATION_KEY, "v": value})


def combine(ai: float | None, baseline: float, calibration: dict[str, Any] | None = None) -> float:
    raw = baseline if ai is None else AI_WEIGHT * ai + (1 - AI_WEIGHT) * baseline
    if calibration:
        raw = calibration["a"] * raw + calibration["b"]
    return round(min(0.98, max(0.02, raw)), 2)


# ---------------- 仅评估难度（未启用知识点标注时） ----------------

DIFF_SYSTEM = """你是经验丰富的中国中小学{subject}教师。请评估下面每道{stage}{grade}试题的难度，只输出 JSON：
{{"questions": [{{"no": 1, "difficulty": 0.35}}]}}
每道输入的题都要输出，no 与输入一致。
"""


async def estimate_ai(job_id: str, meta: dict[str, Any]) -> dict[str, float]:
    from .knowledge import BATCH, DIFFICULTY_RUBRIC, _difficulty, _items, _payload

    settings = get_settings()
    with SessionLocal() as s:
        qs = list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id).order_by(DraftQuestion.no)))
    system = DIFF_SYSTEM.format(subject=meta.get("subject") or "学科", stage=meta.get("stage") or "",
                                grade=meta.get("grade") or "") + DIFFICULTY_RUBRIC
    out: dict[str, float] = {}
    for i in range(0, len(qs), BATCH):
        chunk = qs[i:i + BATCH]
        try:
            data = await chat_json(system, _payload(chunk), settings, purpose="difficulty")
        except LLMError as e:
            log.warning("难度评估失败（第 %d 批）：%s", i // BATCH + 1, describe(e))
            continue
        by_no = {q.no: q for q in chunk}
        for it in _items(data):
            q = by_no.get(it.get("no"))
            if q and (d := _difficulty(it.get("difficulty"))) is not None:
                out[q.id] = d
    return out


def apply(job_id: str, ai: dict[str, float]) -> int:
    """把大模型评估与基线（拆题时写入的 coef）合成为最终难度。老师调整过的不动。返回采用 AI 评估的题数。"""
    cal = get_calibration()
    n = 0
    with SessionLocal() as s:
        for q in s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id)):
            if q.difficulty_source == "manual":
                continue
            v = ai.get(q.id)
            q.coef = combine(v, q.coef, cal)
            q.difficulty_source = "ai" if v is not None else "baseline"
            n += v is not None
        s.commit()
    return n
