# 部署指南

福格云上题库由三部分组成：

| 组件 | 说明 |
| --- | --- |
| 前端 | Vue 3 + Vite，构建后是纯静态文件 |
| 后端 `server/` | FastAPI，提供 `/api`；Docker 部署时同时提供前端页面 |
| 依赖服务 | PostgreSQL 17（必需）、Redis 8（可选，任务队列） |

后台任务（解析试卷、生成答案等）有两种运行方式：

- **未配置 `REDIS_URL`**：在网页服务进程内执行，只需启动一个进程，适合本地开发。
- **配置了 `REDIS_URL`**：网页服务只负责入队，由独立的 Worker 进程（`python -m app.worker`）执行，可启动多个。Docker Compose 部署默认使用这种方式。

三种部署方式：

- [本地开发部署](#本地开发部署)：前后端直接在本机运行，代码修改后自动重载。
- [Docker 开发模式](#docker-开发模式)：全部在容器中运行，代码修改后同样自动重载，本机只需 Docker。
- [Docker 部署](#docker-部署)：一条命令启动全部服务，用于测试环境和正式环境。

---

## 本地开发部署

### 1. 准备环境

| 工具 | 版本 | 用途 |
| --- | --- | --- |
| Node.js | 24 及以上 | 前端 |
| Python | 3.11 及以上（CI 与镜像使用 3.13） | 后端 |
| [uv](https://docs.astral.sh/uv/) | 最新版 | Python 依赖与虚拟环境 |
| Docker（macOS 推荐 OrbStack） | 支持 `docker compose` | 启动 PostgreSQL、Redis |

不想用 Docker 时，也可以使用本机安装的 PostgreSQL / Redis，在 `server/.env` 中改 `DATABASE_URL` / `REDIS_URL` 即可。

### 2. 启动数据库与 Redis

在仓库根目录执行：

```bash
docker compose up -d db redis
```

两者只绑定本机地址，端口与默认端口错开，避免和本机安装的服务冲突：

| 服务 | 本机地址 | 默认账号 |
| --- | --- | --- |
| PostgreSQL | `127.0.0.1:5433` | 库 `fg_quiz`，用户 `fg_quiz`，密码 `fg-quiz-local` |
| Redis | `127.0.0.1:6380` | 无密码 |

后端默认的 `DATABASE_URL` 已指向这个数据库，无需额外配置。

### 3. 配置并启动后端

```bash
cd server
uv sync                      # 安装依赖（含开发依赖），创建 server/.venv
cp .env.example .env         # 首次：复制配置模板
```

编辑 `server/.env`。所有项都可以先不填，此时只用规则拆题与轻量解析（PyMuPDF 读取文字层）；要完整体验解析效果，至少填写：

```ini
MINERU_TOKEN=...             # https://mineru.net/apiManage 创建
LLM_BASE_URL=...             # OpenAI 兼容接口，三项齐全才启用大模型
LLM_API_KEY=...
LLM_MODEL=...
```

创建管理员（系统没有默认账号，密码交互输入，不会进入命令历史）并启动服务：

```bash
uv run python -m app.create_admin admin
uv run uvicorn app.main:app --port 8000 --reload
```

启动时会自动建表并补充新增字段。访问 http://localhost:8000/api/health 确认服务正常。

**可选：使用 Redis 任务队列**（与 Docker 部署的行为一致，便于调试 Worker）。在 `server/.env` 中加入：

```ini
REDIS_URL=redis://localhost:6380/0
```

重启后端后，另开终端启动 Worker（可启动多个）：

```bash
cd server
uv run python -m app.worker
```

### 4. 启动前端

回到仓库根目录：

```bash
npm install
npm run dev                  # http://localhost:5173
```

Vite 把 `/api` 代理到 `127.0.0.1:8000`，因此先启动后端。打开 http://localhost:5173/login，用第 3 步创建的管理员登录。

> 前端开发服务器的来源 `http://localhost:5173`、`5174` 已在后端 `CORS_ORIGINS` 默认值中。改用其他端口或域名时，在 `server/.env` 中设置 `CORS_ORIGINS=["http://localhost:3000"]`。

### 5. 检查与测试

```bash
npm run typecheck            # 前端类型检查
npm run build                # 前端生产构建（含类型检查）

cd server
uv run ruff check .          # 后端代码检查
uv run pytest                # 后端测试
```

后端测试使用同一 PostgreSQL 服务器上的 `fg_quiz_test` 库（不存在时自动创建，不影响开发用的 `fg_quiz`），也可用 `TEST_DATABASE_URL` 指定，库名须以 `_test` 结尾。任务队列测试默认使用 fakeredis，设置 `TEST_REDIS_URL` 时连接真实 Redis。

### 常用命令

| 操作 | 命令 |
| --- | --- |
| 停止数据库与 Redis | `docker compose stop db redis` |
| 连接数据库 | `psql postgresql://fg_quiz:fg-quiz-local@127.0.0.1:5433/fg_quiz` |
| 清空本地数据（不可恢复） | `docker compose down -v`，并删除 `server/data/` |
| 上传文件位置 | `server/data/`（`DATA_DIR`） |

---

## Docker 开发模式

本机不想安装 Node.js、Python 时，可以在 Docker 中开发。`docker-compose.dev.yml` 叠加在 `docker-compose.yml` 之上：

| 服务 | 开发模式下的变化 |
| --- | --- |
| `app` | 挂载 `server/app`，以 `uvicorn --reload` 运行，修改 `.py` 文件后自动重载 |
| `worker` | 挂载 `server/app`，由 `watchfiles` 监听，修改 `.py` 文件后先交还手上的任务再重启 |
| `web`（新增） | Vite 开发服务器，挂载整个仓库，前端修改后浏览器热更新，`/api` 代理到 `app` |
| `db`、`redis` | 不变，与 Docker 部署共用数据卷 |

启动（首次需要构建镜像，`web` 首次启动还会安装前端依赖，需要一两分钟）：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

访问 http://localhost:5173 。首次使用同样需要创建管理员：`docker compose exec app python -m app.create_admin admin`。

> 8000 端口仍提供镜像构建时打包的前端页面，不会随代码更新，开发时请使用 5173。

每条命令都写两个 `-f` 比较繁琐，可以在**本机**仓库根目录的 `.env` 中加入下面一行，之后直接用 `docker compose up -d`、`docker compose logs` 等命令即可（**不要在正式环境的 `.env` 中设置**）：

```ini
COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml
```

| 修改内容 | 生效方式 |
| --- | --- |
| `server/app` 下的 `.py` 文件 | 自动重载 `app` 与 `worker` |
| `src/` 等前端代码 | 浏览器自动热更新 |
| `server/.env` | `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate app worker` |
| `pyproject.toml` / `uv.lock`（后端依赖） | 重新执行上面的启动命令（带 `--build`） |
| `package.json` / `package-lock.json`（前端依赖） | `docker compose -f docker-compose.yml -f docker-compose.dev.yml restart web` |

切回普通部署（`web` 服务会被停止删除）：

```bash
docker compose up -d --build --remove-orphans
```

说明：

- 容器内的前端依赖保存在卷 `fg-quiz-web-modules` 中，与本机的 `node_modules` 分开（macOS 上安装的原生依赖不能在 Linux 容器中使用）。
- 后端代码以只读方式使用，容器不会写入 `__pycache__`，不会在仓库中留下容器用户的文件。
- 文件变化由 OrbStack / Docker Desktop 的文件共享通知到容器。个别环境（如部分虚拟机、网络文件系统）收不到通知时，可在 `web` 服务中设置 `CHOKIDAR_USEPOLLING=true`，后端设置 `WATCHFILES_FORCE_POLLING=true` 改为轮询。

---

## Docker 部署

镜像把前端页面与后端打包在一起，服务同时提供页面和 `/api`，前后端同源。`docker-compose.yml` 包含四个服务：

| 服务 | 说明 | 端口 |
| --- | --- | --- |
| `app` | 页面与接口 | `8000`（对外） |
| `worker` | 从 Redis 队列取出解析、生成答案等任务执行 | 无 |
| `db` | PostgreSQL 17 | 只绑定 `127.0.0.1:5433` |
| `redis` | 任务队列（AOF 持久化，重启不丢任务） | 只绑定 `127.0.0.1:6380` |

### 1. 准备服务器

- 安装 Docker Engine 与 Compose 插件（`docker compose version` 能正常输出）。macOS 可使用 OrbStack 或 Docker Desktop。
- 至少 2 核 CPU、4 GB 内存；小内存机器参考下文「[资源占用](#资源占用)」调低并发。
- 获取代码：`git clone https://github.com/Peng-Rao/wuage-quiz-cloud.git && cd wuage-quiz-cloud`

### 2. 准备配置

有两个配置文件，都不会进入镜像，也已被 git 忽略：

| 文件 | 作用 | 是否必需 |
| --- | --- | --- |
| 仓库根目录 `.env` | 供 `docker-compose.yml` 使用：数据库密码，或外部数据库 / Redis 地址 | 正式环境必需 |
| `server/.env` | 应用配置：MinerU、大模型、存储、单价、会话等，模板见 `server/.env.example` | 可选，不存在时只用规则拆题与轻量解析 |

正式环境在**仓库根目录**创建 `.env`，设置数据库密码：

```ini
# 只用字母、数字、- 和 _（密码会写进连接地址）
POSTGRES_PASSWORD=<强密码>
```

> `POSTGRES_PASSWORD` 只在数据库**首次初始化**时生效。之后要改密码，需先在数据库中执行 `ALTER USER fg_quiz PASSWORD '…'`，再修改 `.env` 并重启。

再准备应用配置：

```bash
cp server/.env.example server/.env
```

按需填写 MinerU、大模型等配置。注意：

- Compose 部署时 `DATABASE_URL`、`REDIS_URL` 由 `docker-compose.yml` 自动指向 `db`、`redis` 服务，`server/.env` 中的这两项**不生效**。要改用外部 PostgreSQL / Redis，在**根目录** `.env` 中设置 `DATABASE_URL=postgresql+psycopg://…` / `REDIS_URL=redis://…`。
- 不要在 `server/.env` 中设置 `PGSSLMODE` 等 libpq 变量，它们会进入容器环境并影响数据库连接。
- 通过 HTTPS 对外服务时设置 `COOKIE_SECURE=true`（见下文「[HTTPS 与反向代理](#https-与反向代理)」）。

### 3. 构建并启动

```bash
docker compose up -d --build
```

首次构建需要几分钟（下载基础镜像、安装依赖、构建前端）。服务以非 root 用户（uid 10001）运行，镜像自带健康检查（`GET /api/health`）。启动顺序为 `db`、`redis` → `app`（完成数据库初始化）→ `worker`。查看状态：

```bash
docker compose ps                        # 四个服务都应为 healthy
curl http://localhost:8000/api/health
```

`/api/health` 返回示例：

```json
{"ok": true, "parsers": ["mineru_cloud", "lite"], "mineru": true, "llm": true,
 "worker": {"queue": "redis", "workers": 1, "slots": 2, "busy": 0, "waiting": 0, "running": 0}}
```

`mineru`、`llm` 为 `false` 表示对应配置未填写或不完整；`worker.workers` 为在线 Worker 数，为 0 时任务会一直排队。

### 4. 创建管理员

首次部署执行一次，密码交互输入：

```bash
docker compose exec app python -m app.create_admin admin
```

访问 `http://<服务器地址>:8000/login` 登录，之后在 `/users` 中创建其他账号。

### 5. 更新版本

```bash
git pull
docker compose up -d --build
```

只有 `app`、`worker` 会重建，`db`、`redis` 保持运行，数据不受影响。启动时自动给已有表补充新增字段（只加列，不改动已有数据）。**升级前建议先备份**（见下文）。

只修改了 `server/.env` 时不需要重新构建，重建容器即可让配置生效：

```bash
docker compose up -d --force-recreate app worker
```

### 常用运维命令

| 操作 | 命令 |
| --- | --- |
| 查看日志 | `docker compose logs -f app worker` |
| 重启服务 | `docker compose restart app worker` |
| 停止全部服务（保留数据） | `docker compose down` |
| 删除全部服务**和数据**（不可恢复） | `docker compose down -v` |
| 进入数据库 | `docker compose exec db psql -U fg_quiz fg_quiz` |
| 增加 Worker | `docker compose up -d --scale worker=3` |

### 数据与备份

| 数据 | 位置 |
| --- | --- |
| 数据库 | 卷 `fg-quiz-pg` |
| 上传文件、页面图、题目配图（本地存储时） | 卷 `fg-quiz-data`，容器内 `/data` |
| Redis 队列 | 卷 `fg-quiz-redis` |

重建容器不会丢失数据；`docker compose down -v` 会删除这些卷。

备份：

```bash
# 数据库
docker compose exec -T db pg_dump -U fg_quiz fg_quiz > backup-$(date +%F).sql

# 上传文件（本地存储时）
docker run --rm -v wuage-quiz-cloud_fg-quiz-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/data-$(date +%F).tar.gz -C /data .
```

卷名带 Compose 项目名前缀（默认为目录名），可用 `docker volume ls` 确认。

恢复数据库（会覆盖现有数据）：

```bash
docker compose stop app worker
docker compose exec -T db psql -U fg_quiz -d postgres -c "DROP DATABASE fg_quiz" -c "CREATE DATABASE fg_quiz OWNER fg_quiz"
docker compose exec -T db psql -U fg_quiz fg_quiz < backup-2026-10-01.sql
docker compose start app worker
```

### HTTPS 与反向代理

正式环境建议在 `app` 前放置 Nginx / Caddy 等反向代理处理 HTTPS，并：

1. 在 `server/.env` 中设置 `COOKIE_SECURE=true`（否则浏览器在 HTTPS 下仍会接受非 Secure Cookie，存在会话泄露风险）。
2. 前后端同源部署时不需要配置 `CORS_ORIGINS`；如果前端另有域名，把该来源加入 `CORS_ORIGINS`。
3. 上传大小上限需不小于 `MAX_FILE_MB`（默认 50 MB）。
4. 解析进度使用 SSE（`/api/parse-jobs/{id}/events`），代理不能缓冲响应。服务已返回 `X-Accel-Buffering: no`，Nginx 默认会遵守。
5. 只把 `app` 暴露给反向代理：可把 `docker-compose.yml` 中的 `"8000:8000"` 改为 `"127.0.0.1:8000:8000"`。

Nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name quiz.example.com;
    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    client_max_body_size 60m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

服务以 `--proxy-headers` 启动，登录限流按来源 IP 计算。uvicorn 默认只信任来自 `127.0.0.1` 的 `X-Forwarded-*` 头；反向代理通过 Docker 网络访问时，需在 `server/.env` 中设置 `FORWARDED_ALLOW_IPS` 为代理地址，否则所有请求会被视为同一来源。

### 扩容与并发

- 总并发 = Worker 数 × `WORKER_CONCURRENCY`（默认 2，范围 1–10）。增加 Worker：`docker compose up -d --scale worker=3`。
- 同一任务不会重复执行；Worker 崩溃或被强制停止时，其手上的任务约 2 分钟后由其他 Worker 接手重做，正常停止时立即交还。
- 实际吞吐还受 MinerU 与大模型接口限流的限制。
- 默认本地存储（`STORAGE_BACKEND=local`）时，`app` 与各 `worker` 共用 `fg-quiz-data` 卷，必须部署在同一台机器上。跨机器部署需改用对象存储。

### 对象存储（腾讯云 COS / 阿里云 OSS）

在 `server/.env` 中设置 `STORAGE_BACKEND=cos` 及 `COS_*`（阿里云为 `STORAGE_BACKEND=oss` 及 `OSS_*`，见 `server/.env.example`）后，上传的试卷、页面图、题目配图都存入对象存储：

- 浏览器用预签名地址直传对象存储（不经过本服务）；页面上的配图仍请求 `/api/files/…`，校验登录与题目权限后跳转到 10 分钟有效的签名地址。存储桶保持**私有读写**。
- 存储桶需配置跨域（CORS）规则：来源 `*`，方法 `GET`、`PUT`、`HEAD`，允许头 `*`，暴露头 `ETag`。下载 Word 时浏览器经重定向读取配图，跨域跳转后的来源为 `null`，因此来源须为 `*`；访问仍受签名保护。
- 从本地存储切换时，先上传已有文件，再修改配置并重启：

  ```bash
  docker compose exec app python -m app.migrate_storage --to cos --dry-run   # 先预览
  docker compose exec app python -m app.migrate_storage --to cos
  ```

- 腾讯云 COS：存储桶名称带 APPID（如 `fg-quiz-1250000000`），地域如 `ap-guangzhou`；默认域名在同地域腾讯云服务器上自动走内网。密钥建议使用只授权该存储桶读写的 CAM 子用户。
- 阿里云 OSS：同地域 ECS 可把 `OSS_ENDPOINT` 设为内网地址（不收流量费），同时设置公网的 `OSS_PUBLIC_ENDPOINT` 供签名地址使用。

### 大模型看图

设置 `VISION_MODEL`（如通义千问 `qwen3-vl-plus`）后，AI 生成答案时含配图的题改用看图模型，并按顺序附上题目配图（每题最多 `VISION_MAX_IMAGES` 张）。使用对象存储时传图片签名地址，由模型服务下载；本地存储时以 base64 内嵌。未设置时大模型看不到图片，含图题的答案会标注「AI 未看到图片」。看图模型的用量按模型名单独统计，单价在 `LLM_PRICES` 中按模型名配置。

### 不使用 Compose：单独运行镜像

```bash
docker build -t fg-quiz-cloud .
docker run -d --name fg-quiz -p 8000:8000 \
  --env-file server/.env \
  -e DATABASE_URL='postgresql+psycopg://user:pass@host:5432/fg_quiz' \
  -v fg-quiz-data:/data \
  fg-quiz-cloud
docker exec -it fg-quiz python -m app.create_admin admin
```

- 必须用 `DATABASE_URL` 指定已有的 PostgreSQL（远程库建议加 `?sslmode=require`）。
- 不设置 `REDIS_URL` 时任务在网页服务进程内执行；设置了 `REDIS_URL` 但没有独立 Worker 时，设置 `RUN_WORKER=true` 让网页服务进程同时执行任务。

### 不同芯片架构

镜像支持 `linux/amd64`（常见 x86 服务器）与 `linux/arm64`（Apple 芯片、鲲鹏等 ARM 服务器）。`docker build` 只构建**本机架构**，在 Apple 芯片的 Mac 上构建的镜像不能直接在 x86 服务器上运行，需要指定目标架构：

```bash
# 只构建 x86 服务器用的镜像
docker buildx build --platform linux/amd64 -t fg-quiz-cloud:amd64 --load .

# 同时构建两种架构并推送到镜像仓库，各机器拉取时自动选择对应架构
docker buildx build --platform linux/amd64,linux/arm64 -t <仓库地址>/fg-quiz-cloud:<版本> --push .
```

没有镜像仓库时，导出为文件拷贝到服务器：

```bash
docker save --platform linux/amd64 fg-quiz-cloud:amd64 -o fg-quiz-cloud-amd64.tar   # 约 90 MB（压缩前 370 MB）
docker load -i fg-quiz-cloud-amd64.tar                                              # 在服务器上执行
docker tag fg-quiz-cloud:amd64 fg-quiz-cloud:latest                                 # compose 使用 latest 标签
docker compose up -d --no-build
```

前端构建阶段固定在构建机的原生架构上运行（产物与架构无关），交叉构建只有 Python 依赖安装一步经过模拟器，通常在一分钟内完成。

---

## 资源占用

小内存机器可在 `server/.env` 中调低：

```ini
WORKER_CONCURRENCY=1
ANSWER_CONCURRENCY=1
PAGE_DPI=80
MAX_FILE_MB=20
```

这会降低并行处理量、原图清晰度和上传大小上限。每个 Worker 的答案请求并发可达 `WORKER_CONCURRENCY × ANSWER_CONCURRENCY`，增加 Worker 数也会按比例增加占用。修改后重建 `app`、`worker` 容器生效。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 登录后立即退出 / 一直跳回登录页 | HTTP 访问却设置了 `COOKIE_SECURE=true`；或前后端跨源但未加入 `CORS_ORIGINS` |
| 上传后一直「排队中」 | `/api/health` 中 `worker.workers` 为 0：检查 `docker compose ps worker` 与 `docker compose logs worker` |
| `mineru` / `llm` 为 `false` | 检查 `server/.env` 是否填写完整，修改后需重建容器 |
| `app` 一直 unhealthy | `docker compose logs app`，常见原因是数据库密码与已初始化的数据库不一致 |
| 本地开发连不上数据库 | 确认 `docker compose up -d db` 已启动，端口为 `5433` 而不是 `5432` |
| 端口 8000 被占用 | 修改 `docker-compose.yml` 中 `app` 的端口映射，如 `"8080:8000"` |
| 进度条不动但任务完成了 | 反向代理缓冲了 SSE 响应，关闭代理缓冲 |
| 开发模式下修改代码没有重载 | `docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f app worker web` 查看是否检测到变化；收不到文件通知时改为轮询（见「Docker 开发模式」） |

完整配置项见 `server/.env.example` 与 [server/README.md](../server/README.md#配置)。
