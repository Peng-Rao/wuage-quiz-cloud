"""解析评测：以老师核对后的结果为标准答案，重新解析原文件并逐项对比。

指标（均为 0–1）：
- 拆题：查准率 = 配对成功 / 解析出的题数，查全率 = 配对成功 / 标准题数（按题干字面相似度配对）
- 题型、选项数、答案准确率（答案只统计原卷或老师填写的标准答案）
- 知识点前 3 命中率：解析出的前 3 个知识点中至少有 1 个在标准知识点里的题目占比；另给出标准知识点的召回率
- 难度平均绝对误差：只统计老师调整过难度的题（difficultySource=manual），据此拟合校准直线 y = a·x + b
- 试卷分类准确率：学段、学科、年级、试卷类型
"""

import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import DraftQuestion, EvalRun, EvalSample, ParseJob, SessionLocal
from .pipeline import difficulty
from .pipeline.run import run_job_safely
from .schemas import PARSE_STAGES
from .similar import lexical, normalize

log = logging.getLogger(__name__)

MATCH_MIN = 0.5
META_FIELDS = ("stage", "subject", "grade", "paperType")
CHOICE = ("单选题", "多选题")


# ---------------- 样本 ----------------

def snapshot(s: Session, job: ParseJob) -> dict[str, Any]:
    qs = list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job.id).order_by(DraftQuestion.no)))
    return {
        "meta": job.meta or {},
        "questions": [{
            "no": q.no, "type": q.type, "score": q.score, "stem": q.stem, "options": q.options, "answer": q.answer,
            "answerSource": q.answer_source, "kps": [{"id": k["id"], "name": k["name"]} for k in q.knowledge_points],
            "coef": q.coef, "difficultySource": q.difficulty_source,
        } for q in qs],
    }


def save_sample(s: Session, job: ParseJob) -> EvalSample:
    """设为评测样本；已是样本时用当前核对结果覆盖。"""
    sample = s.scalar(select(EvalSample).where(EvalSample.job_id == job.id))
    if sample is None:
        sample = EvalSample(id="e" + uuid.uuid4().hex[:12], school_id=job.school_id, job_id=job.id,
                            file_name=job.file_name, file_keys=job.file_keys, gold={})
        s.add(sample)
    sample.gold = snapshot(s, job)
    s.flush()
    return sample


# ---------------- 对比 ----------------

def _answer_equal(qtype: str, gold: str, pred: str | None) -> bool:
    if not pred:
        return False
    if qtype in CHOICE:
        return re.sub(r"[^A-H]", "", gold.upper()) == re.sub(r"[^A-H]", "", pred.upper())
    return normalize(gold) == normalize(pred) or lexical(gold, pred) >= 0.9


def _kp_keys(kps: list[dict[str, Any]]) -> set[str]:
    """知识点比较键：id 与去编号的规范化名称都算。"""
    keys = set()
    for k in kps:
        keys.add(k.get("id") or "")
        keys.add(normalize(re.sub(r"^\d+(\.\d+)*\s*", "", k.get("name") or "")))
    keys.discard("")
    return keys


def match(gold: list[dict[str, Any]], pred: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    """按题干相似度贪心配对，返回 (标准题下标, 解析题下标, 相似度)。"""
    pairs = sorted(((lexical(g["stem"], p["stem"]), gi, pi) for gi, g in enumerate(gold) for pi, p in enumerate(pred)),
                   reverse=True)
    used_g, used_p, out = set(), set(), []
    for score, gi, pi in pairs:
        if score < MATCH_MIN:
            break
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        out.append((gi, pi, round(score, 3)))
    return sorted(out)


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def compare(gold: dict[str, Any], pred_meta: dict[str, Any], pred: list[dict[str, Any]], calibration: dict | None) -> dict[str, Any]:
    g_qs = gold["questions"]
    pairs = match(g_qs, pred)
    c: dict[str, int] = {k: 0 for k in ("type", "options_n", "options_ok", "answer_n", "answer_ok", "kp_n", "kp_hit",
                                        "kp_gold", "kp_found", "diff_n")}
    diff_err = 0.0
    diff_pairs: list[tuple[float, float]] = []
    rows = []
    for gi, pi, sim in pairs:
        g, p = g_qs[gi], pred[pi]
        row: dict[str, Any] = {"no": g["no"], "predNo": p["no"], "similarity": sim, "issues": []}
        if g["type"] == p["type"]:
            c["type"] += 1
        else:
            row["issues"].append(f"题型：{p['type']} → {g['type']}")
        if g["type"] in CHOICE:
            c["options_n"] += 1
            if len(g["options"]) == len(p["options"]):
                c["options_ok"] += 1
            else:
                row["issues"].append(f"选项数：{len(p['options'])} → {len(g['options'])}")
        if g.get("answer") and g.get("answerSource") in ("paper", "manual"):
            c["answer_n"] += 1
            if _answer_equal(g["type"], g["answer"], p.get("answer")):
                c["answer_ok"] += 1
            else:
                row["issues"].append("答案不一致")
        if g["kps"]:
            c["kp_n"] += 1
            gold_keys = [_kp_keys([k]) for k in g["kps"]]
            pred_keys = _kp_keys(p["kps"][:3])
            found = sum(1 for keys in gold_keys if keys & pred_keys)
            c["kp_gold"] += len(gold_keys)
            c["kp_found"] += found
            if found:
                c["kp_hit"] += 1
            else:
                row["issues"].append("知识点未命中：" + "、".join(k["name"] for k in g["kps"]))
        if g.get("difficultySource") == "manual":
            c["diff_n"] += 1
            diff_err += abs(g["coef"] - p["coef"])
            raw = p["coef"]
            if calibration and calibration.get("a"):
                raw = (raw - calibration["b"]) / calibration["a"]  # 去掉评测时已生效的校准，得到原始评估
            diff_pairs.append((raw, g["coef"]))
        rows.append(row)
    meta_ok = sum(1 for f in META_FIELDS if (gold["meta"].get(f) or "") == (pred_meta.get(f) or ""))
    return {
        "counts": {**c, "gold": len(g_qs), "pred": len(pred), "matched": len(pairs), "meta_ok": meta_ok,
                   "meta_n": len(META_FIELDS), "diff_err": round(diff_err, 4)},
        "diffPairs": diff_pairs,
        "unmatchedGold": [g_qs[i]["no"] for i in range(len(g_qs)) if i not in {gi for gi, _, _ in pairs}],
        "extraPred": [pred[i]["no"] for i in range(len(pred)) if i not in {pi for _, pi, _ in pairs}],
        "questions": rows,
    }


def metrics(counts: dict[str, int]) -> dict[str, float | None]:
    return {
        "splitPrecision": _ratio(counts["matched"], counts["pred"]),
        "splitRecall": _ratio(counts["matched"], counts["gold"]),
        "typeAccuracy": _ratio(counts["type"], counts["matched"]),
        "optionAccuracy": _ratio(counts["options_ok"], counts["options_n"]),
        "answerAccuracy": _ratio(counts["answer_ok"], counts["answer_n"]),
        "knowledgeTop3": _ratio(counts["kp_hit"], counts["kp_n"]),
        "knowledgeRecall": _ratio(counts["kp_found"], counts["kp_gold"]),
        "difficultyMae": round(counts["diff_err"] / counts["diff_n"], 4) if counts["diff_n"] else None,
        "metaAccuracy": _ratio(counts["meta_ok"], counts["meta_n"]),
    }


def fit_calibration(pairs: list[tuple[float, float]]) -> dict[str, float] | None:
    """最小二乘拟合 gold = a·raw + b；样本少于 5 个或 raw 无差异时不拟合。"""
    n = len(pairs)
    if n < 5:
        return None
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    if sxx < 1e-6:
        return None
    a = sum((x - mx) * (y - my) for x, y in pairs) / sxx
    b = my - a * mx
    before = sum(abs(y - difficulty.combine(None, x)) for x, y in pairs) / n
    after = sum(abs(y - min(0.98, max(0.02, a * x + b))) for x, y in pairs) / n
    return {"a": round(a, 4), "b": round(b, 4), "n": n, "maeBefore": round(before, 4), "maeAfter": round(after, 4)}


# ---------------- 运行 ----------------

def _pred(s: Session, job_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    job = s.get(ParseJob, job_id)
    qs = list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id).order_by(DraftQuestion.no)))
    return (job.meta or {}) if job else {}, [
        {"no": q.no, "type": q.type, "stem": q.stem, "options": q.options, "answer": q.answer,
         "kps": q.knowledge_points, "coef": q.coef} for q in qs]


async def run_eval(run_id: str) -> None:
    settings = get_settings()
    with SessionLocal() as s:
        run = s.get(EvalRun, run_id)
        if run is None:
            return
        samples = [x for x in (s.get(EvalSample, sid) for sid in run.sample_ids) if x is not None]
        run.status, run.done, run.details, run.error = "running", 0, [], None
        s.commit()
    calibration = difficulty.get_calibration()
    total = {k: 0 for k in ("type", "options_n", "options_ok", "answer_n", "answer_ok", "kp_n", "kp_hit", "kp_gold",
                            "kp_found", "diff_n", "gold", "pred", "matched", "meta_ok", "meta_n")}
    total["diff_err"] = 0.0  # type: ignore[assignment]
    all_pairs: list[tuple[float, float]] = []
    details = []
    try:
        for sample in samples:
            with SessionLocal() as s:
                job = ParseJob(
                    id=uuid.uuid4().hex[:16], school_id=sample.school_id, kind="eval", file_name=f"[评测] {sample.file_name}",
                    file_count=len(sample.file_keys), file_size=0, file_type=_file_type(s, sample),
                    file_keys=sample.file_keys, options={"ocr": True, "answer": True, "dedupe": False, "knowledge": True},
                    status="queued", progress=0, stages=[{"stage": st, "status": "pending", "note": None} for st in PARSE_STAGES],
                    warnings=[],
                )
                s.add(job)
                s.commit()
                job_id = job.id
            await run_job_safely(job_id)
            with SessionLocal() as s:
                job = s.get(ParseJob, job_id)
                if job is None or job.status != "done":
                    details.append({"sampleId": sample.id, "fileName": sample.file_name, "jobId": job_id,
                                    "error": (job.error if job else None) or "解析失败"})
                else:
                    meta, pred = _pred(s, job_id)
                    r = compare(sample.gold, meta, pred, calibration)
                    for k, v in r["counts"].items():
                        total[k] += v
                    all_pairs += r.pop("diffPairs")
                    details.append({"sampleId": sample.id, "fileName": sample.file_name, "jobId": job_id,
                                    "parser": job.parser, "metrics": metrics(r["counts"]), **r})
                run = s.get(EvalRun, run_id)
                run.done += 1
                run.details = list(details)
                s.commit()
        with SessionLocal() as s:
            run = s.get(EvalRun, run_id)
            run.metrics = {**metrics(total), "counts": total, "calibration": fit_calibration(all_pairs),
                           "currentCalibration": calibration}
            run.status = "done"
            s.commit()
    except Exception:
        log.exception("评测 %s 异常", run_id)
        with SessionLocal() as s:
            run = s.get(EvalRun, run_id)
            run.status, run.error = "failed", "评测过程中发生内部错误"
            s.commit()


def _file_type(s: Session, sample: EvalSample) -> str:
    src = s.get(ParseJob, sample.job_id)
    return src.file_type if src else "pdf"


def config_snapshot() -> dict[str, Any]:
    st = get_settings()
    return {"parserChain": st.parser_chain, "mineruModel": st.mineru_model_version if st.mineru_token else None,
            "llmModel": st.llm_model if st.llm_enabled else None, "embeddingModel": st.embedding_model or None,
            "calibration": difficulty.get_calibration()}
