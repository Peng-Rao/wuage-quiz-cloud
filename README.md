# 福格云上题库

依据 `福格云上题库前端设计方案/福格云上题库.dc.html` 实现。技术栈：Vue 3 + TypeScript + Vite + Vue Router + Pinia（与设计方案中的「技术选型」一致）。

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # 类型检查 + 生产构建
```

## 页面

| 路由 | 页面 |
| --- | --- |
| `/` | 首页：学段学科切换、搜题、入口、最新试卷、我的组卷 |
| `/pick` | 选题组卷：章节树、多维筛选、题目卡片、试题篮 |
| `/paper` | 试卷编辑：结构、排版设置、下载 Word / 导出 PDF / 答题卡 |
| `/upload` | 试卷解析：上传 → 解析进度 → 核对入库 |

## 目录

- `src/styles/base.css` 设计令牌（颜色、字体、圆角）与通用样式
- `src/data/mock.ts` 演示数据，接入后端时替换为接口请求
- `src/stores/` Pinia：学段学科、试题篮（持久化到 localStorage）
- `src/components/` 顶栏、页脚、开关、难度分布条
- `src/views/` 四个页面

## 目前是演示实现的部分

- 题目、试卷、解析结果均为静态演示数据；上传解析进度为前端模拟。
- 「下载 Word」导出的是 Word 可打开的 HTML 格式 `.doc`，正式版建议换成 docx.js 生成 `.docx`。
- 「导出 PDF」调用浏览器打印（已写好打印样式）。
- 收藏、纠错、智能补题、编辑题目等按钮暂未接功能。

## Docker 部署

前端页面与解析服务（`server/`）打包为一个镜像，服务同时提供页面和 `/api`。

```bash
docker compose up -d --build        # 访问 http://localhost:8000/upload
```

或直接使用镜像：

```bash
docker build -t fg-quiz-cloud .
docker run -d -p 8000:8000 --env-file server/.env -v fg-quiz-data:/data fg-quiz-cloud
```

- 配置：MinerU、大模型、单价等通过环境变量提供，见 `server/.env.example`；compose 默认读取 `server/.env`（不存在时只用规则拆题与轻量解析）。Key 不会打进镜像。
- 数据：数据库、上传文件和页面图保存在卷 `/data`，重建容器不会丢失。
- 进程：任务队列在进程内，只能运行 1 个服务进程；并发解析份数用 `WORKER_CONCURRENCY` 调整。多实例部署需先把队列换成 Redis、存储换成对象存储。
- 服务以非 root 用户（uid 10001）运行，自带健康检查（`GET /api/health`）。

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
