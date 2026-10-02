"""对已解析完成的试卷补做自动入库（新解析的试卷在解析结束时自动执行）。

    uv run python -m app.auto_commit --all            # 所有已完成的解析任务
    uv run python -m app.auto_commit JOB_ID ...       # 指定任务
    uv run python -m app.auto_commit --all --dry-run  # 只统计，不入库
    uv run python -m app.auto_commit --all --recheck-confidence
        # 先按修正后的规则重算置信度：解析版题内的【答案】【解析】曾被误判为「规则与大模型切分不一致」

确定的题存入校本题库，不确定的（置信度低、题干为空、公式未识别、缺少答案、疑似重复等）留作草稿由人工审核。
"""

import argparse
from collections import Counter

from sqlalchemy import select

from .bank import auto_commit
from .config import get_settings
from .db import DraftQuestion, ParseBlock, ParseJob, SessionLocal
from .pipeline.ir import Block
from .pipeline.segment import _overlap, question_part, rule_segment, units_from_blocks

# 「规则与大模型切分不一致」扣 0.25；一致时加 0.03
MISMATCH_PENALTY, MATCH_BONUS = 0.25, 0.03


def recheck_confidence(s, job: ParseJob) -> int:  # noqa: ANN001
    """修正前解析的试卷：用保存的版面单元重跑规则切分，去掉题内答案后与大模型的题目单元一致的，补回误扣的置信度。
    拆题时有自动修正（分组问题）的试卷无法区分扣分原因，跳过。返回修正的题数。"""
    if any("分组问题" in w for w in (job.warnings or [])):
        return 0
    rows = list(s.scalars(select(ParseBlock).where(ParseBlock.job_id == job.id).order_by(ParseBlock.seq)))
    blocks = [Block(r.seq, r.page, tuple(r.bbox), r.type, r.content or "") for r in rows]
    units = units_from_blocks(blocks, [r.id for r in rows])
    text = {u.id: u.text for u in units}
    rule = rule_segment(units, with_answer=True)
    rule_by_unit = {uid: q for q in rule.questions for uid in q.unit_ids}
    fixed = 0
    for d in s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job.id, DraftQuestion.status == "draft")):
        ids = list(d.block_ids or [])
        ref = next((rule_by_unit[i] for i in ids if i in rule_by_unit), None)
        if ref is None:
            continue
        old = _overlap(ref.unit_ids, ids)
        new = _overlap(question_part(ref.unit_ids, text.get), ids)
        if old < 0.8 <= new:
            d.confidence = round(min(0.99, d.confidence + MISMATCH_PENALTY + MATCH_BONUS), 2)
            fixed += 1
    return fixed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck-confidence", action="store_true", help="入库前按修正后的规则重算置信度")
    args = ap.parse_args()
    if not args.jobs and not args.all:
        ap.error("请指定任务 id 或 --all")
    s_ = get_settings()
    with SessionLocal() as s:
        q = select(ParseJob).where(ParseJob.status == "done", ParseJob.kind.is_distinct_from("eval"))
        if args.jobs:
            q = q.where(ParseJob.id.in_(args.jobs))
        jobs = list(s.scalars(q.order_by(ParseJob.created_at)))
    total_saved, total_pending, reasons = 0, 0, Counter()
    for job in jobs:
        with SessionLocal() as s:
            job = s.get(ParseJob, job.id)
            fixed = recheck_confidence(s, job) if args.recheck_confidence else 0
            if fixed:
                s.flush()
            res = auto_commit(s, job, s_.auto_commit_min_confidence, s_.auto_commit_require_answer,
                              s_.auto_commit_rule_only)
            if args.dry_run:
                s.rollback()  # 只统计：重算的置信度与入库都不保存
            else:
                if res.saved or res.duplicate_paper:
                    job.warnings = [w for w in (job.warnings or []) if not w.startswith("自动入库")] + [res.note()]
                s.commit()
            total_saved += res.saved
            total_pending += res.pending_count
            reasons.update(res.pending)
            print(f"{job.id} {job.file_name[:40]}：" + (f"修正置信度 {fixed} 道；" if fixed else "") + res.note(),
                  flush=True)
    verb = "可自动入库" if args.dry_run else "已自动入库"
    print(f"任务 {len(jobs)} 个；{verb} {total_saved} 道，待人工审核 {total_pending} 道"
          + (f"（{'、'.join(f'{k} {v}' for k, v in reasons.most_common())}）" if reasons else ""))


if __name__ == "__main__":
    main()
