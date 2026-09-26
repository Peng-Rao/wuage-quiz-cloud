import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api import bank, batches, eval, files, jobs, knowledge, questions, similar, usage
from .config import get_settings
from .db import SessionLocal, init_db
from .knowledge_tree import seed_builtin
from .services import current_school
from .worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as s:
        # 同步内置知识树（P1 未接入账号体系，归属演示学校）
        changed, removed = seed_builtin(s, current_school())
        if changed or removed:
            logging.getLogger(__name__).info("内置知识树：更新 %d 棵，移除 %d 棵", changed, removed)
    worker.start()
    yield
    await worker.stop()


app = FastAPI(title="福格云上题库 · 解析服务", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 错误统一为 { "message": "..." }，与前端约定一致
@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"message": exc.detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:])
    return JSONResponse({"message": f"参数错误：{field} {first.get('msg', '')}".strip()}, status_code=422)


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    return {"ok": True, "parsers": s.parser_chain, "mineru": bool(s.mineru_token), "llm": s.llm_enabled}


app.include_router(files.router)
app.include_router(jobs.router)
app.include_router(questions.router)
app.include_router(usage.router)
app.include_router(batches.router)
app.include_router(similar.router)
app.include_router(knowledge.router)
app.include_router(eval.router)
app.include_router(bank.router)


# ---------------- 前端页面（生产部署） ----------------

_static = get_settings().static_dir
if _static and (_static / "index.html").is_file():
    _static = _static.resolve()

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        """托管前端：存在的静态文件直接返回，其余路径返回 index.html 交给前端路由。"""
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        target = (_static / full_path).resolve()
        if full_path and target.is_file() and _static in target.parents:
            # 构建产物文件名带内容哈希，可长期缓存
            cache = "public, max-age=31536000, immutable" if full_path.startswith("assets/") else "public, max-age=3600"
            return FileResponse(target, headers={"Cache-Control": cache})
        return FileResponse(_static / "index.html", headers={"Cache-Control": "no-cache"})
