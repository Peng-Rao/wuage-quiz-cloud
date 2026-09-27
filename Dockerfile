# syntax=docker/dockerfile:1
# 福格云上题库：前端页面 + 解析服务打包为一个镜像，支持 linux/amd64 与 linux/arm64
#   docker build -t fg-quiz-cloud .                                   # 构建本机架构
#   docker buildx build --platform linux/amd64 -t fg-quiz-cloud --load .   # 指定架构
#   docker run -p 8000:8000 --env-file server/.env -v fg-quiz-data:/data fg-quiz-cloud

# ---------- 前端构建 ----------
# 产物是纯静态文件、与 CPU 架构无关：固定在构建机的原生架构上运行，交叉构建时不走模拟器
FROM --platform=$BUILDPLATFORM node:24-slim AS web
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html vite.config.ts env.d.ts tsconfig.json tsconfig.app.json tsconfig.node.json .env.api ./
COPY public ./public
COPY src ./src
# 前端模拟数据复用内置知识树与教材目录（构建时会一并编译）
COPY server/app/data/knowledge ./server/app/data/knowledge
COPY server/app/data/chapters ./server/app/data/chapters
# --mode api：页面连接同源的 /api，而不是内存模拟数据
RUN npm run build:api

# ---------- Python 依赖（按 uv.lock 锁定版本） ----------
FROM python:3.13-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
# 下载缓存放在构建缓存里，不进入镜像
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# ---------- 运行环境（不含 uv 与构建缓存） ----------
FROM python:3.13-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    DATA_DIR=/data \
    STATIC_DIR=/app/web
WORKDIR /app/server
COPY --from=deps /opt/venv /opt/venv
COPY server/app ./app
COPY --from=web /web/dist /app/web
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /app app && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
# 任务队列在进程内，只能运行 1 个 worker 进程；并发解析份数用 WORKER_CONCURRENCY 调整
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
