"""上传签名与文件读取。本地存储时上传和读取都经过这里；使用对象存储（OSS / COS）时浏览器直传，读取校验权限后重定向到签名地址。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..auth import staff
from ..db import BankQuestion, UploadOwner, User, get_session
from ..services import get_job
from fastapi.responses import FileResponse, RedirectResponse, Response

from ..config import get_settings
from ..pipeline.files import kind_of
from ..schemas import UploadRequest, UploadTicket
from ..storage import LocalStore, get_store, new_upload_key

router = APIRouter()


@router.post("/api/uploads", response_model=UploadTicket)
def create_upload(req: UploadRequest, user: User = Depends(staff), s: Session = Depends(get_session)) -> UploadTicket:
    if kind_of(req.file_name) is None:
        raise HTTPException(400, "仅支持 Word（.docx）、PDF、JPG / PNG 图片")
    if req.file_size > get_settings().max_file_mb * 1024 * 1024:
        raise HTTPException(400, f"单个文件不超过 {get_settings().max_file_mb} MB")
    key = new_upload_key(req.file_name)
    url, headers = get_store().upload_ticket(key, req.content_type)
    s.add(UploadOwner(key=key, user_id=user.id))
    s.commit()
    return UploadTicket(upload_url=url, upload_headers=headers, file_key=key)


@router.put("/api/files/{key:path}", status_code=204)
async def put_file(key: str, request: Request, user: User = Depends(staff), s: Session = Depends(get_session)) -> Response:
    owner = s.get(UploadOwner, key)
    if not owner or (user.role != "admin" and owner.user_id != user.id):
        raise HTTPException(403, "无权使用该上传凭证")
    if not key.startswith("uploads/"):
        raise HTTPException(403, "不允许写入该路径")
    store = get_store()
    if not isinstance(store, LocalStore):
        raise HTTPException(404, "请使用上传凭证中的地址直传对象存储")
    try:
        exists = store.exists(key)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if exists:
        raise HTTPException(409, "文件已上传")
    limit = get_settings().max_file_mb * 1024 * 1024
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise HTTPException(413, f"单个文件不超过 {get_settings().max_file_mb} MB")
    if not data:
        raise HTTPException(400, "文件为空")
    store.put(key, bytes(data))
    return Response(status_code=204)


@router.get("/api/files/{key:path}")
def get_file(key: str, s: Session = Depends(get_session)) -> Response:
    # 只开放解析产物（页面图、题图），不开放原始上传文件
    if not key.startswith("jobs/"):
        raise HTTPException(403, "不允许读取该路径")
    # Reject non-canonical keys before checking record ownership.
    if any(x in {"", ".", ".."} for x in key.split("/")) or "\\" in key:
        raise HTTPException(400, "无效文件路径")
    get_job(s, key.split("/")[1])
    if s.info["user"].role == "member":
        if not any(key in (q.images or []) for q in s.scalars(select(BankQuestion))):
            raise HTTPException(403, "只能读取已授权题目的配图")
    store = get_store()
    if isinstance(store, LocalStore):
        if not store.exists(key):
            raise HTTPException(404, "文件不存在")
        return FileResponse(store.path(key), headers={"Cache-Control": "private, no-store"})
    # 已通过权限校验：跳转到短时有效的签名地址，由对象存储直接返回文件
    return RedirectResponse(store.signed_url(key, get_settings().storage_url_expires), status_code=302)
