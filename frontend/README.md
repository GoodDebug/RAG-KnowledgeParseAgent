# frontend/README.md —— 前端开发者快速上手

## 1. 前端简介

本前端是「**AI 智能客服 + 小说解构知识图谱**」双子系统的 Web 端：一个 **Vue 3 + Vite** 的单页应用（SPA），提供 **① RAG 对话**（对话问答、知识库管理、网页爬虫三大页面，SSE 逐字流式 + 引用卡片 + 反馈/追问闭环）与 **② 解构工作台**（`/deconstruct`，5 tab：解构 / 复核 / 图谱 / 百科 / 数据，展示小说解构任务、人工复核、知识图谱、实体百科与结构化数据浏览）。

技术栈：**Vue 3.5 + Vite 6 + vue-router 4.5 + Naive UI + relation-graph（图谱）+ lucide（图标）+ marked 15**（markdown 渲染）。项目使用 **JavaScript** 编写（笔试题建议 TypeScript，选型理由见项目根「项目说明」）。

---

## 2. 目录结构

```
frontend/
├── index.html                  # HTML 入口
├── vite.config.js              # Vite 配置（端口 5173 + /api 代理）
├── package.json                # 依赖与脚本
└── src/
    ├── main.js                 # 应用入口：路由定义 + 登录守卫
    ├── App.vue                 # 导航外壳
    ├── api/
    │   ├── index.js            # 统一 API 层：BASE / authHeaders / fetch + SSE / 全部接口
    │   └── novel.js            # 解构 API 封装（任务/解构/重试/SSE/浏览/Knowledge/复核 19 个函数）
    ├── views/
    │   ├── ChatView.vue        # 对话页（SSE 流式、引用、反馈、追问、会话列表）
    │   ├── IngestView.vue      # 知识库管理页（上传、书分组、删除）
    │   ├── CrawlerView.vue     # 爬虫页（通用抓取、章节发现与批量下载）
    │   ├── LoginView.vue       # 登录/注册页
    │   ├── NovelWorkspaceView.vue # 解构工作台容器（5 tab：解构/复核/图谱/百科/数据）
    │   ├── GraphView.vue       # 图谱 tab（1-hop 关系图 + 时态滑块）
    │   ├── EntityView.vue      # 百科 tab（实体卡 L0-L4 + 快照时间轴 + 热力图）
    │   ├── KnowledgeBrowserView.vue # 数据 tab（10 类结构化数据浏览）
    │   ├── ReviewView.vue      # 复核 tab（validation_issue 待办 + 裁决）
    │   └── …（NovelJobsView / NovelJobDetail / BookWorkbench 等）
    └── components/
        ├── MessageList.vue     # 消息列表（markdown/引用折叠/反馈/追问）
        ├── ChatInput.vue       # 输入框
        ├── SessionList.vue     # 会话侧边栏
        ├── FileSelector.vue    # 文件选择器（点击 + 拖拽）
        ├── EntityCardPanel.vue # 实体卡（L0-L4 五区 + 三态徽标 + 置信度弱视觉，二阶段 05）
        ├── SnapshotTimeline.vue # 快照演化时间轴（成长线）
        ├── EvidencePanel.vue   # 原文证据面板
        └── …（AppearanceHeatmap / ChapterSlider / TimelinePanel / DeconstructPanel / ThreeStateBadge 等）
```

---

## 3. 环境准备

- **Node.js 20+** 与 **npm**（随 Node 安装）。

检查版本：

```bash
node --version   # v20.x+
npm --version
```

---

## 4. 安装与运行

```bash
cd frontend
npm install        # 安装依赖（首次）
npm run dev        # 启动开发服务器
```

启动后访问 **http://localhost:5173** 。`npm run dev` 由 Vite 提供热更新。

其它脚本：

```bash
npm run build      # 生产构建，产物输出到 dist/
npm run preview    # 本地预览 dist/ 产物
```

---

## 5. Vite 代理与后端对接（BASE）

### 开发环境：Vite 代理

`vite.config.js` 配置了 `/api` 代理到后端，开发时前端请求 `http://localhost:5173/api/...` 会被转发到后端 `8000` 端口（超时 120 秒），避免跨域：

```js
server: {
  port: 5173,
  proxy: { '/api': { target: 'http://localhost:8000', timeout: 120000, proxyTimeout: 120000 } }
}
```

> 后端需先启动（`cd backend/src && uvicorn main_fastapi:app --host 0.0.0.0 --port 8000`，见 backend/README）。

### 对接方式（`src/api/index.js`）

- **`BASE = ''`**：所有接口用**相对路径** `/api/...`，由代理（开发）或 Nginx（生产）转发到后端。
- **`authHeaders()`**：从 `localStorage.getItem('token')` 读取令牌，加上 `Authorization: Bearer <token>`；未登录则不带。
- **原生 `fetch`**（不使用 axios）：上传走 `FormData`（不手动设 `Content-Type`，让浏览器带 boundary）。
- **SSE 用 `fetch` + `ReadableStream`** 实现（`chatStream`）：原生 `EventSource` 无法携带 Authorization 头，故改用 fetch 流式读取 `data:` 帧，并每帧让出 macrotask 以便浏览器逐帧渲染。

> `frontend/.env.example` 里的 `VITE_API_BASE` **未被代码使用**（`BASE` 恒为空串）；如需改后端地址，改 `vite.config.js` 的 `proxy.target`。

### 生产环境：Nginx 反向代理

构建产物由 Nginx 托管，`/api` 代理到后端容器（`proxy_buffering off` 以支持 SSE），见根 README 的 Docker 部署说明。

---

## 6. 登录与认证

- **登录守卫**（`src/main.js`）：未登录访问任意页 → 重定向到 `/login?redirect=<原路径>`；已登录访问 `/login` → 跳回 `/chat`。
- **令牌存储**：登录后 token 仅存 `localStorage['token']`（不落 URL / 代码 / 日志），由 `authHeaders()` 统一携带。
- **登录/注册**（`LoginView.vue`）：同一表单切换注册/登录，自动识别手机号（纯数字）或邮箱（含 `@`）。

**快速体验账号**（内置测试数据，登录即可试用示例知识库问答）：

```
手机号：12345678910
密码：  1234567
```

---

## 7. 页面与功能

| 路由 | 页面 | 主要功能 |
| --- | --- | --- |
| `/chat` | 对话 | SSE 逐字流式回答；思考/引用/意图/追问事件展示；引用卡片按文件折叠分组；赞/踩反馈（可附文字）；追问建议一键点击发送；历史会话列表与详情 |
| `/ingest` | 知识库管理 | 上传 `.txt/.md/.pdf`（异步入库，轮询 processing→ready）；按书分组展示；单文件删除与整书删除（同步清 Milvus 向量） |
| `/crawler` | 爬虫 | 通用单页/批量抓取（dynamic/intercept 双模式）；小说章节发现与批量下载 |
| `/deconstruct` | 解构工作台 | 左书列 + 右 5 tab：**解构**（任务列表/SSE 进度/失败章重试）、**复核**（validation_issue 待办 + 原文证据分屏 + confirm/ignore/fix 裁决）、**图谱**（1-hop 关系图 + 章节滑块时态回放）、**百科**（实体卡 L0-L4：基线/弧光/伏笔规则/明暗 + 快照时间轴 + 热力图）、**数据**（10 类结构化数据分页筛选） |

页面间通过顶部导航切换；`/` 自动重定向到 `/chat`；解构工作台旧路由（`/books/:book_id`、`/jobs/:job_id`）自动重定向到 `/deconstruct`。

---

## 8. 构建与部署（简版）

```bash
cd frontend
npm run build      # 产物输出到 dist/
```

Docker 部署：`DEPLOY/Dockerfile.frontend` 多阶段构建（Node 构建 → Nginx 托管），Nginx 配置 `/api` 反向代理到后端并关闭 SSE 缓冲。完整打包步骤见根 README 快速开始。

---

## 9. 常见问题（简版）

- **页面 API 请求 502**：后端未启动，或 `vite.config.js` 的代理目标不是 `localhost:8000`。
- **SSE 不逐字渲染**：确认后端返回 `text/event-stream`；前端 `chatStream` 已逐帧让出 macrotask，若仍卡顿检查网络层是否缓冲。
- **改了 `VITE_API_BASE` 没生效**：代码固定 `BASE=''`，改它无效；请改 `vite.config.js` 的 `proxy.target`。
