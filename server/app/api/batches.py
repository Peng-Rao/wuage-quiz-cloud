import uuid
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import ParseBatch, ParseJob, get_session
from ..schemas import BatchOut, CreateBatchRequest
from ..services import current_school, list_item, new_job, not_found
from ..worker import worker

router = APIRouter(prefix="/api/parse-batches")


def _batch_out(s: Session, batch: ParseBatch) -> BatchOut:
    jobs = list(s.scalars(select(ParseJob).where(ParseJob.batch_id == batch.id).order_by(ParseJob.created_at)))
    return BatchOut(id=batch.id, total=batch.total, counts=dict(Counter(j.status for j in jobs)),
                    jobs=[list_item(s, j) for j in jobs], created_at=batch.created_at)


@router.post("", response_model=BatchOut)
def create_batch(req: CreateBatchRequest, s: Session = Depends(get_session)) -> BatchOut:
    """批量上传：每一项是一份试卷（1 个 PDF / Word，或同一份试卷的多张图片），各自创建解析任务后台排队。
    任一项校验失败则整批不创建。"""
    batch = ParseBatch(id=uuid.uuid4().hex[:16], school_id=current_school(), total=len(req.items))
    s.add(batch)
    jobs = [new_job(s, it.file_keys, it.file_names, req.options, batch_id=batch.id) for it in req.items]
    s.commit()
    for j in jobs:
        worker.enqueue(j.id)
    return _batch_out(s, batch)


@router.get("/{batch_id}", response_model=BatchOut)
def read_batch(batch_id: str, s: Session = Depends(get_session)) -> BatchOut:
    batch = s.get(ParseBatch, batch_id)
    if batch is None or batch.school_id != current_school():
        raise not_found("批量任务")
    return _batch_out(s, batch)
