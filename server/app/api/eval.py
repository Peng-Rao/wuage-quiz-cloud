import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import EvalRun, EvalSample, get_session
from ..evaluation import config_snapshot, save_sample
from ..pipeline import difficulty
from ..schemas import EvalRunCreate, EvalRunOut, EvalSampleOut
from ..services import current_school, get_job, not_found
from ..worker import worker

router = APIRouter()


def _sample_out(x: EvalSample) -> EvalSampleOut:
    return EvalSampleOut(id=x.id, job_id=x.job_id, file_name=x.file_name,
                         question_count=len(x.gold.get("questions", [])), created_at=x.created_at)


def _run_out(r: EvalRun) -> EvalRunOut:
    return EvalRunOut(id=r.id, status=r.status, total=len(r.sample_ids), done=r.done, config=r.config,
                      metrics=r.metrics, details=r.details, error=r.error, created_at=r.created_at)


@router.post("/api/parse-jobs/{job_id}/eval-sample", response_model=EvalSampleOut)
def mark_sample(job_id: str, s: Session = Depends(get_session)) -> EvalSampleOut:
    """把老师核对后的结果保存为评测样本（再次调用会用当前结果覆盖）。"""
    job = get_job(s, job_id)
    if job.status != "done" or job.kind == "eval":
        raise HTTPException(400, "只能把已完成解析的试卷设为评测样本")
    sample = save_sample(s, job)
    s.commit()
    return _sample_out(sample)


@router.get("/api/eval-samples", response_model=list[EvalSampleOut])
def list_samples(s: Session = Depends(get_session)) -> list[EvalSampleOut]:
    rows = s.scalars(select(EvalSample).where(EvalSample.school_id == current_school()).order_by(EvalSample.created_at.desc()))
    return [_sample_out(x) for x in rows]


@router.delete("/api/eval-samples/{sample_id}", status_code=204)
def remove_sample(sample_id: str, s: Session = Depends(get_session)) -> None:
    x = s.get(EvalSample, sample_id)
    if x is None or x.school_id != current_school():
        raise not_found("评测样本")
    s.delete(x)
    s.commit()


@router.post("/api/eval-runs", response_model=EvalRunOut, status_code=202)
def create_run(req: EvalRunCreate, s: Session = Depends(get_session)) -> EvalRunOut:
    q = select(EvalSample.id).where(EvalSample.school_id == current_school())
    if req.sample_ids:
        q = q.where(EvalSample.id.in_(req.sample_ids))
    ids = list(s.scalars(q.order_by(EvalSample.created_at)))
    if not ids:
        raise HTTPException(400, "还没有评测样本：请先在核对页把核对好的试卷设为评测样本")
    running = s.scalar(select(func.count()).where(EvalRun.school_id == current_school(), EvalRun.status.in_(["queued", "running"])))
    if running:
        raise HTTPException(409, "已有评测在运行，请等待完成")
    run = EvalRun(id="r" + uuid.uuid4().hex[:12], school_id=current_school(), status="queued", sample_ids=ids,
                  config=config_snapshot(), details=[])
    s.add(run)
    s.commit()
    worker.enqueue_eval(run.id)
    return _run_out(run)


@router.get("/api/eval-runs", response_model=list[EvalRunOut])
def list_runs(s: Session = Depends(get_session)) -> list[EvalRunOut]:
    rows = s.scalars(select(EvalRun).where(EvalRun.school_id == current_school()).order_by(EvalRun.created_at.desc()).limit(20))
    return [_run_out(r) for r in rows]


@router.get("/api/eval-runs/{run_id}", response_model=EvalRunOut)
def read_run(run_id: str, s: Session = Depends(get_session)) -> EvalRunOut:
    r = s.get(EvalRun, run_id)
    if r is None or r.school_id != current_school():
        raise not_found("评测")
    return _run_out(r)


@router.post("/api/eval-runs/{run_id}/apply-calibration", response_model=dict)
def apply_calibration(run_id: str, s: Session = Depends(get_session)) -> dict:
    """采用评测拟合出的难度校准，之后解析的试卷生效。"""
    r = s.get(EvalRun, run_id)
    if r is None or r.school_id != current_school():
        raise not_found("评测")
    cal = (r.metrics or {}).get("calibration")
    if not cal:
        raise HTTPException(400, "本次评测没有足够的难度样本（至少 5 道老师调整过难度的题）")
    difficulty.set_calibration(cal["a"], cal["b"], source=f"评测 {r.id}")
    return difficulty.get_calibration() or {}


@router.delete("/api/difficulty-calibration", status_code=204)
def clear_calibration() -> None:
    difficulty.set_calibration(None)


@router.get("/api/difficulty-calibration", response_model=dict | None)
def read_calibration() -> dict | None:
    return difficulty.get_calibration()
