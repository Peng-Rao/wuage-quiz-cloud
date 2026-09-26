import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import batches, eval, files, jobs, knowledge, questions, similar, usage
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
        # 学科尚无知识树时载入内置示例（P1 未接入账号体系，归属演示学校）
        seed_builtin(s, current_school())
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
