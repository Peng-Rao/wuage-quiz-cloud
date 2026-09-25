"""上传签名与本地存储读写（LocalStore 专用；换成 OSS / S3 后前端直传云存储，读取走 CDN）。"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..config import get_settings
from ..pipeline.files import kind_of
from ..schemas import UploadRequest, UploadTicket
from ..storage import get_store

router = APIRouter()


@router.post("/api/uploads", response_model=UploadTicket)
def create_upload(req: UploadRequest) -> UploadTicket:
    if kind_of(req.file_name) is None:
        raise HTTPException(400, "仅支持 Word（.docx）、PDF、JPG / PNG 图片")
    if req.file_size > get_settings().max_file_mb * 1024 * 1024:
        raise HTTPException(400, f"单个文件不超过 {get_settings().max_file_mb} MB")
    store = get_store()
    key = store.new_upload_key(req.file_name)
    return UploadTicket(upload_url=store.upload_url(key), file_key=key)


@router.put("/api/files/{key:path}", status_code=204)
async def put_file(key: str, request: Request) -> Response:
    if not key.startswith("uploads/"):
        raise HTTPException(403, "不允许写入该路径")
    store = get_store()
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
def get_file(key: str) -> FileResponse:
    # 只开放解析产物（页面图、题图），不开放原始上传文件
    if not key.startswith("jobs/"):
        raise HTTPException(403, "不允许读取该路径")
    store = get_store()
    if not store.exists(key):
        raise HTTPException(404, "文件不存在")
    return FileResponse(store.path(key), headers={"Cache-Control": "private, max-age=3600"})
