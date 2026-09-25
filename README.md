# 福格云上题库 · 前端

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
