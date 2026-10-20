"""按试卷标注重算已解析试卷的分值：python -m app.rescore [--job ID ...] [--apply]。

规则与新解析相同（见 pipeline/segment.py 的 assign_scores）：题号后标注、大题标题中的分值优先，
其余按大题总分、整卷满分推算。默认只统计将改动的题，--apply 才写入草稿题与正式题库；
正式题库中分值已被修改（与草稿不同）的题不覆盖。每份独立提交，可重复运行。
"""
import argparse
from collections import Counter

from sqlalchemy import select

from .db import BankQuestion, DraftQuestion, ParseBlock, ParseJob, SessionLocal
from .pipeline import segment as seg
from .pipeline.ir import Block


def rescore(blocks: list[ParseBlock], drafts: list[DraftQuestion]) -> list[float]:
    """草稿题（按题号顺序）重算后的分值。"""
    rule = seg.rule_segment(seg.units_from_blocks(
        [Block(b.seq, b.page, tuple(b.bbox), b.type, b.content) for b in blocks],  # type: ignore[arg-type]
        [b.id for b in blocks]), with_answer=False)
    owner = {u: q for q in rule.questions for u in q.unit_ids}
    # 与解析时一致：所属大题取首个能对应到规则结果的单元
    refs = [next((owner[u] for u in d.block_ids if u in owner), None) for d in drafts]
    counts = Counter(r.section if r else 0 for r in refs)
    qs = []
    for d, ref in zip(drafts, refs):
        q = seg.Question(ref.printed_no if ref else None, d.type, d.score, "", [], None, None, list(d.block_ids), 1.0,
                         ref.section if ref else 0, score_source="llm")
        # 题号后标注的分值：取与草稿主要重合的规则题
        candidates = {id(owner[u]): owner[u] for u in d.block_ids if u in owner}.values()
        same = [r for r in candidates if len(set(r.unit_ids) & set(d.block_ids)) * 2 >= len(r.unit_ids)]
        info = rule.sections.get(q.section)
        if same and same[0].score_source == "question":
            q.score, q.score_source = same[0].score, "question"
        elif info and q.printed_no in info.special:
            q.score, q.score_source = info.special[q.printed_no], "section"
        elif info and info.type == d.type and seg._reliable(info, counts[q.section]) \
                and (each := info.score_for(q.printed_no)):
            q.score, q.score_source = each, "section"
        qs.append(q)
    seg.assign_scores(qs, rule.sections, rule.paper_total)
    return [q.score for q in qs]


def rescore_job(job_id: str, apply: bool) -> tuple[float, float, int, int]:
    """返回（原总分，新总分，改动的草稿题数，改动的正式题库题数）。"""
    with SessionLocal() as s:
        blocks = list(s.scalars(select(ParseBlock).where(ParseBlock.job_id == job_id).order_by(ParseBlock.seq)))
        drafts = list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id)
                                .order_by(DraftQuestion.no).with_for_update()))
        scores = rescore(blocks, drafts)
        bank = {b.source_draft_id: b for b in s.scalars(
            select(BankQuestion).where(BankQuestion.source_draft_id.in_([d.id for d in drafts])).with_for_update())}
        old_total = sum(d.score for d in drafts)
        changed = banked = 0
        for d, score in zip(drafts, scores):
            if score == d.score:
                continue
            changed += 1
            b = bank.get(d.id)
            if b is not None and b.score == d.score:  # 入库后改过分值的不覆盖
                banked += 1
                if apply:
                    b.score = score
            if apply:
                d.score = score
        if apply:
            s.commit()
    return old_total, sum(scores), changed, banked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", action="append", help="只处理指定任务，可重复")
    parser.add_argument("--apply", action="store_true", help="写入数据库（默认只统计）")
    args = parser.parse_args()
    with SessionLocal() as s:
        stmt = select(ParseJob.id).where(ParseJob.status == "done").order_by(ParseJob.created_at)
        if args.job:
            stmt = stmt.where(ParseJob.id.in_(args.job))
        ids = list(s.scalars(stmt))
    papers = questions = bank = 0
    for job_id in ids:
        old, new, changed, banked = rescore_job(job_id, args.apply)
        if changed:
            papers, questions, bank = papers + 1, questions + changed, bank + banked
            print(f"{job_id} 总分 {old:g} → {new:g}，{changed} 题（正式题库 {banked} 题）")
    verb = "已更新" if args.apply else "将更新（加 --apply 写入）"
    print(f"{verb} {papers}/{len(ids)} 份试卷、{questions} 道草稿题、{bank} 道正式题库题")


if __name__ == "__main__":
    main()
