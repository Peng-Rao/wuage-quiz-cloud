# 福格云上题库

依据 `福格云上题库前端设计方案/福格云上题库.dc.html` 实现。技术栈：Vue 3 + TypeScript + Vite + Vue Router + Pinia（与设计方案中的「技术选型」一致）。

```bash
npm install
npm run dev      # http://localhost:5173；需同时启动 server 后端
npm run build    # 类型检查 + 生产构建
```

## 登录与三级权限

首次使用先在服务端创建管理员（没有默认账号或密码）：

```bash
cd server
uv sync
uv run python -m app.create_admin admin
uv run uvicorn app.main:app --port 8000 --reload
```

Docker 部署后执行 `docker compose exec app python -m app.create_admin admin`。
密码交互输入，不写入代码或命令历史。前端访问 `/login` 登录；管理员在 `/users` 创建账号、分配学科、修改角色、停用账号或重置密码。修改账号后，其已有会话立即失效。

| 角色 | 可用功能 | 数据范围 |
| --- | --- | --- |
| 管理员 | 全部功能、账号管理、评测与全校 AI 用量 | 全部学科 |
| 组长 | 上传解析、AI 解答/知识点标注/辅助组卷、审核分配、手动组卷与下载 | 授权学科 |
| 普通用户 | 选题、知识点查看、手动组卷、Word 下载与 PDF 打印 | 分配给本人且审核通过的题目，及其关联知识点 |

上传前先在顶栏选择学科。入库后进入试卷详情，展开“审核与分配题目”，选择题目及归属用户，审核通过后普通用户即可查看。每道题目前归属一位用户；重新分配会替换原归属。撤回审核后立即停止后续读取。重新提交入库或变更学科会清除审核状态，需重新审核。历史题目默认未分配、未审核，由管理员或对应学科组长处理后再向普通用户开放。

AI 组卷位于 `/compose`（仅管理员、组长），见下方「目前是演示实现的部分」。试题篮仅保存在当前页面内存中，刷新或退出后清空，避免共用浏览器时泄露上一个账号的题目。

会话使用 HttpOnly、SameSite Cookie，服务端只保存会话令牌摘要；密码使用带独立盐的 scrypt 哈希。会话默认 12 小时。HTTPS 部署设置 `COOKIE_SECURE=true`，并将实际站点来源加入 `CORS_ORIGINS`；推荐前后端同源部署。生产启用前备份数据库；启动时自动补充用户/会话表和题目归属/审核字段，不改动已有题目内容。

## 页面

| 路由 | 页面 |
| --- | --- |
| `/login` | 账号密码登录 |
| `/users` | 账号管理（仅管理员） |
| `/` | 首页：学段学科切换、搜题、入口、最新试卷、我的组卷 |
| `/chapter` | 章节选题：教材版本与册、章节目录（含各章节题数），场景 / 题型 / 难度 / 年份 / 地区 / 年级 / 学期筛选，同步套卷 |
| `/knowledge` | 知识点选题：知识树（含题数）、知识点查询与多选，筛选同章节选题 |
| `/papers` | 试卷选题：同步教学 / 阶段测试 / 升学备考 / 竞赛分类，按学段、年级、学科、版本、类型浏览已入库的整卷；`/papers/:id` 查看整卷，整卷加入试题篮或以原卷组卷 |
| `/compose` | AI 组卷（Demo，仅管理员、组长）：用文字描述学生情况与要求，AI 整理成组卷蓝图，程序从题库选题并赋分；可多轮修改，确认后加入试题篮 |
| `/paper` | 试卷编辑：调整大题与题目顺序（拖动 / ▲▼）、逐题或按大题赋分、排版设置、下载 Word / 导出 PDF / 答题卡 |
| `/upload` | 试卷解析：上传 → 解析进度 → 核对入库 |

## 目录

- `src/styles/base.css` 设计令牌（颜色、字体、圆角）与通用样式
- `src/data/mock.ts` 演示数据（首页列表、章节目录等）
- `src/api/` 接口层：`parse/` 试卷解析、`bank/` 校本题库与试卷库，统一连接后端；mock 文件仅保留为参考，不作为登录后的数据源
- `src/stores/` Pinia：学段学科、试题篮（题目快照、题序、分值，仅当前页面内存保存）
- `src/components/` 顶栏、页脚、开关、难度分布条
- `src/views/` 四个页面

## 目前是演示实现的部分

- `npm run dev` 与 `npm run dev:api` 均连接真实后端，所有题库接口需要登录。
- 章节选题使用厦门市小学、初中、高中现用版本的教材目录（`server/app/data/chapters/`，由 `server/scripts/crawl_textbooks.py`
  从国家中小学智慧教育平台抓取）：每节对应若干知识点，按题目已标注的知识点归入章节。「最热」「分类（典型题、压轴题等）」「解题方法」暂无数据支持。
- 首页展示授权范围内的真实试卷和当前试题篮；试卷编辑页的标题、考试时间为固定文案。
- 数学公式：题目中以 `$…$` / `$$…$$` 包裹的 LaTeX 用 KaTeX 显示；编辑题目时可用可视化公式编辑器（MathLive，按需加载）插入或修改公式。
- 「下载 Word」在浏览器中生成 `.docx`（`src/utils/docx.ts`）：公式转换为 Word 原生公式（LaTeX → KaTeX MathML → OMML，`src/utils/omml.ts`），
  可在 Word 中直接编辑；题目配图嵌入文档；KaTeX 无法解析的公式保留原文。装订线以装订边距（gutter）表示。
- 「导出 PDF」调用浏览器打印（已写好打印样式）。
- AI 组卷为 Demo（方案见 `docs/ai-paper-assembly.md`）：需 `npm run dev:api` 连接后端，演示数据模式下不可用。
  大模型只负责理解需求（每次生成调用 1 次，按知识点编号选择重点、给出各题型题量与难度），选题与赋分由程序完成（`server/app/compose.py`），
  题目全部来自题库、总分准确；未配置大模型时按关键词匹配知识点。只做了一个学科的效果验证；学生情况不保存，刷新页面后对话清空；
  题库中的题需已标注知识点才能侧重具体知识点。加入试题篮后试卷标题仍为试卷编辑页的固定文案。
- 收藏、纠错、智能补题、编辑题目等按钮暂未接功能。

## Docker 部署

前端页面与解析服务（`server/`）打包为一个镜像，服务同时提供页面和 `/api`。

```bash
docker compose up -d --build        # 访问 http://localhost:8000
docker compose exec app python -m app.create_admin admin   # 首次部署：创建管理员
```

compose 包含四个服务：`app`（页面与接口）、`worker`（执行解析、生成答案等后台任务）、`db`（PostgreSQL 17）、`redis`（任务队列）。
数据库与 Redis 端口不对外开放；正式部署前在仓库根目录新建 `.env`，
设置 `POSTGRES_PASSWORD=<只含字母、数字、-、_ 的强密码>`（首次启动时生效，之后修改需同时在数据库中改密码）。
改用已有的外部 PostgreSQL 时，在根目录 `.env` 中设置 `DATABASE_URL=postgresql+psycopg://…`，`db` 服务可不用。

或直接使用镜像：

```bash
docker build -t fg-quiz-cloud .
docker run -d -p 8000:8000 --env-file server/.env -v fg-quiz-data:/data fg-quiz-cloud
```

- 配置：MinerU、大模型、单价等通过环境变量提供，见 `server/.env.example`；compose 默认读取 `server/.env`（不存在时只用规则拆题与轻量解析）。Key 不会打进镜像。
- 数据：数据库在卷 `fg-quiz-pg`，上传文件和页面图在卷 `fg-quiz-data`（`/data`），重建容器不会丢失；`docker compose down -v` 会删除数据卷。
- 备份：`docker compose exec db pg_dump -U fg_quiz fg_quiz > backup.sql`，另备份 `fg-quiz-data` 卷中的文件。
- 单独 `docker run` 镜像时没有 `db` 服务：设置 `DATABASE_URL` 连接已有的 PostgreSQL，不设置则使用 `/data/app.db`（SQLite，适合试用）。
- 批量处理：任务放在 Redis 队列中，由 `worker` 执行。增加 Worker 即可同时解析更多试卷：
  `docker compose up -d --scale worker=3`，总并发 = Worker 数 × `WORKER_CONCURRENCY`（默认 2）。
  同一任务不会重复执行；Worker 崩溃或被强制停止时，其手上的任务约 2 分钟后由其他 Worker 接手重做，正常停止时立即交还。
  `GET /api/health` 的 `worker` 字段显示在线 Worker 数、排队和执行中的任务数。
- 上传文件与页面图保存在本机的 `fg-quiz-data` 卷中，`app` 与各 `worker` 共用，因此所有容器需在同一台机器上；
  跨机器部署需先把存储换成对象存储。
- 服务以非 root 用户（uid 10001）运行，自带健康检查（`GET /api/health`）。

### 从 SQLite 迁移到 PostgreSQL

早期版本的数据库是 `/data/app.db`（SQLite）。升级后先只启动数据库、导入数据，再启动应用：

```bash
docker compose stop app                                    # 旧版本在运行时先停止
docker compose up -d --build db
docker compose run --rm --no-deps app python -m app.migrate_to_pg    # 读取数据卷中的 /data/app.db
docker compose up -d
```

- 目标库已有数据时会拒绝执行（例如应用已在新库上启动过、自动写入了内置知识树），确认用 SQLite 的数据覆盖时加 `--replace`。
- 在一个事务中复制全部表，失败时目标库不变；`app.db` 不会被修改或删除，确认无误后可自行删除。
- 本地开发同理：`DATABASE_URL=postgresql+psycopg://… uv run python -m app.migrate_to_pg data/app.db`。

### 不同芯片架构

镜像支持 `linux/amd64`（常见 x86 服务器、Intel / AMD 电脑）与 `linux/arm64`（Apple 芯片、鲲鹏、树莓派等 ARM 服务器）。
`docker build` 只构建**本机架构**；在 Mac（Apple 芯片）上构建的镜像不能直接在 x86 服务器上运行，需要指定目标架构：

```bash
# 只构建 x86 服务器用的镜像
docker buildx build --platform linux/amd64 -t fg-quiz-cloud:amd64 --load .

# 同时构建两种架构，推送到镜像仓库后，各机器拉取时自动选择对应架构
docker buildx build --platform linux/amd64,linux/arm64 -t <仓库地址>/fg-quiz-cloud:<版本> --push .
```

没有镜像仓库时，可以导出为文件拷贝到服务器：

```bash
docker save --platform linux/amd64 fg-quiz-cloud:amd64 -o fg-quiz-cloud-amd64.tar   # 约 90 MB（压缩前 370 MB）
docker load -i fg-quiz-cloud-amd64.tar                                              # 在服务器上执行
```

前端构建阶段固定在构建机的原生架构上运行（产物与架构无关），交叉构建只有 Python 依赖安装一步经过模拟器，通常在一分钟内完成。
