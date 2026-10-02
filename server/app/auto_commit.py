"""对已解析完成的试卷补做自动入库（新解析的试卷在解析结束时自动执行）。

    uv run python -m app.auto_commit --all            # 所有已完成的解析任务
    uv run python -m app.auto_commit JOB_ID ...       # 指定任务
    uv run python -m app.auto_commit --all --dry-run  # 只统计，不入库

确定的题存入校本题库，不确定的（置信度低、题干为空、公式未识别、缺少答案、疑似重复等）留作草稿由人工审核。
"""

import argparse
from collections import Counter

from sqlalchemy import select

from .bank import auto_commit, review_reasons
from .config import get_settings
from .db import DraftQuestion, ParseJob, SessionLocal


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
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
            if args.dry_run:
                for d in s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job.id,
                                                               DraftQuestion.status == "draft")):
                    r = review_reasons(d, s_.auto_commit_min_confidence, s_.auto_commit_require_answer)
                    total_pending += bool(r)
                    total_saved += not r
                    reasons.update(r)
                continue
            res = auto_commit(s, job, s_.auto_commit_min_confidence, s_.auto_commit_require_answer)
            if res.saved or res.duplicate_paper:
                job.warnings = [w for w in (job.warnings or []) if not w.startswith("自动入库")] + [res.note()]
            s.commit()
            total_saved += res.saved
            total_pending += res.pending_count
            reasons.update(res.pending)
            print(f"{job.id} {job.file_name[:40]}：{res.note()}", flush=True)
    verb = "可自动入库" if args.dry_run else "已自动入库"
    print(f"任务 {len(jobs)} 个；{verb} {total_saved} 道，待人工审核 {total_pending} 道"
          + (f"（{'、'.join(f'{k} {v}' for k, v in reasons.most_common())}）" if reasons else ""))


if __name__ == "__main__":
    main()
