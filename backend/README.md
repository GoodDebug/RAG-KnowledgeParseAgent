# backend/README.md —— 后端开发者快速上手

## 1. 后端简介

本后端是「**AI 智能客服 + 小说解构知识图谱**」双子系统的服务端：基于 **FastAPI + SSE** 实现 **① 企业级智能客服问答**（注册登录 JWT、多会话、知识库管理上传/去重/双删、RAG 两阶段检索 + 流式输出 + 引用溯源、意图识别与追问引导）与 **② 小说解构 / 知识图谱**（小说上传 → 章节切分 → LangGraph 图编排 + 8 个解构 Agent 抽取实体/快照/关系/时间线/地点/伏笔/冲突/规则 → 15 张解构表幂等入库 → 跨章一致性校验 + 人工复核 → 知识图谱/实体百科浏览）。

技术栈一句话：**FastAPI 0.140 + MySQL 8.0（19 表）+ Milvus 2.6（仅 RAG）+ BGE（Embedding/Reranker）+ DeepSeek（LLM）+ MCP 子进程 + LangGraph（解构图编排）**（LangChain 1.3 / FastMCP 3.4）。

---

## 2. 目录结构

```
backend/
├── src/                        # FastAPI 源码
│   ├── main_fastapi.py         #   入口：lifespan 启动（5 步）+ 健康检查
│   ├── config.py               #   集合注册表 + Milvus Schema/索引定义
│   ├── app_state.py            #   全局应用状态（单例）
│   ├── core/                   #   依赖注入 / JWT 安全 / 意图识别 / Prompt 优化 / 异常 / 日志 / prompts
│   ├── db/                     #   SQLAlchemy engine + ORM 模型（users/sessions/messages/documents）
│   ├── routers/                #   auth / chat / documents / sessions / crawler / novel（/api/novel/* 20 端点）
│   ├── LLM/                    #   LLM 适配器 + 记忆适配器（MysqlMemoryAdapter 等）
│   ├── MCP_SERVER/             #   FastMCP 子进程服务（RAG 检索/入库/删除 + 天气工具）
│   ├── RAG/                    #   文档加载 / 分块 / Embedding / 两阶段检索 / Milvus 操作
│   ├── novel/                  #   小说解构子系统：agents（8+1）/ prompts（10）/ persistence / pipeline / graph（LangGraph）/ orchestrator / llm_runner / events
│   ├── UTILS/                  #   WSL 适配 / 雪花 ID / 爬虫引擎
│   └── temp/                   #   运行时目录（uploads 上传暂存 / sessions）
├── scripts/                    #   init_db.sql（19 表）/ rollback.sql / migration_confidence.sql / 回填脚本
├── tests/                      #   pytest 用例（61 个测试文件，共 436 用例）
├── requirements.txt            #   依赖清单（pip freeze 生成，剔除 nvidia-* 平台包）
└── pytest.ini                  #   pythonpath=src，testpaths=tests
```

---

## 3. 环境变量（全量配置参数说明）

后端通过 `backend/src/.env` 读取配置（`main_fastapi.py` 启动时加载）。先复制模板再编辑：

```bash
cp backend/src/.env.example backend/src/.env
```

> **不要**把 `.env` 或真实 Key 提交到仓库（`.gitignore` 已忽略）。

全部参数按分组说明如下（默认值取自 `.env.example`；标 ⚠️ 为**必填**）：

### LLM

| 参数                      | 默认值                       | 说明                                                             |
| ------------------------- | ---------------------------- | ---------------------------------------------------------------- |
| `DeepSeek_API_KEY` ⚠️ | —                           | DeepSeek 调用密钥（OpenAI 兼容），不填则问答接口在调用时鉴权失败 |
| `DeepSeek_API_URL`      | `https://api.deepseek.com` | DeepSeek API 地址；缺失可能导致启动时地址解析异常                |

### Embedding

| 参数                     | 默认值                                     | 说明                                                        |
| ------------------------ | ------------------------------------------ | ----------------------------------------------------------- |
| `EMBEDDING_MODEL_NAME` | `BAAI/bge-small-zh-v1.5`                 | 中文 Embedding 模型；首启需联网下载到 HF 缓存               |
| `VEC_DIM`              | `512`                                    | 向量维度，**必须与模型输出维度及 Milvus schema 一致** |
| `EMBEDDING_DEVICE`     | `None`                                   | 推理设备；`None`=自动检测（有 GPU 用 cuda，否则 cpu）     |
| `QUERY_PREFIX`         | `为这个句子生成表示以用于检索相关文章：` | BGE 中文查询前缀，检索时自动加在问题前                      |

### HuggingFace

| 参数               | 默认值    | 说明                                                                             |
| ------------------ | --------- | -------------------------------------------------------------------------------- |
| `HF_HUB_OFFLINE` | `False` | 离线模式；**首启置 0** 联网下载模型，缓存到 `huggingface_cache` 后可置 1 |

### Milvus

| 参数                                  | 默认值                     | 说明                                                             |
| ------------------------------------- | -------------------------- | ---------------------------------------------------------------- |
| `AUTO_GET_WIN_HOST_IP`              | `True`                   | WSL2 下自动探测 Windows 宿主 IP 连 Milvus；非 WSL/容器设为 False |
| `MANUAL_MILVUS_HOST` ⚠️           | `xxxxxxxxxxxx`（占位符） | 自动探测失败时手动指定的 Milvus 地址                             |
| `MILVUS_PORT` ⚠️                  | `xxxx`（占位符）         | Milvus 端口（默认 19530）；Docker 部署由 compose 覆盖            |
| `MILVUS_USER` / `MILVUS_PASSWORD` | 空                         | 有鉴权时填写，否则留空                                           |

### 集合

| 参数                        | 默认值                | 说明                                         |
| --------------------------- | --------------------- | -------------------------------------------- |
| `CONTENT_COLLECTION_NAME` | `content_knowledge` | 内容知识库集合名                             |
| `CONTENT_COLLECTION_DESC` | `文档内容知识库`    | 集合描述（供 LLM 发现）                      |
| `ENABLE_DYNAMIC_FIELD`    | `False`             | 是否启用 Milvus 动态字段                     |
| `AUTO_ID`                 | `False`             | 是否由 Milvus 自动生成主键                   |
| `FORCE_REBUILD`           | `False`             | 设为 True 每次启动删除重建集合（仅测试期用） |

### MySQL

| 参数                         | 默认值                            | 说明                                              |
| ---------------------------- | --------------------------------- | ------------------------------------------------- |
| `MYSQL_HOST`               | `127.0.0.1`                     | MySQL 地址；Docker 部署由 compose 覆盖为`mysql` |
| `MYSQL_PORT`               | `3306`                          | MySQL 端口                                        |
| `MYSQL_USER`               | `ai_customer`                   | 业务库账号                                        |
| `MYSQL_PASSWORD` ⚠️      | `your_mysql_password`（占位符） | 业务库密码，必须修改                              |
| `MYSQL_DB`                 | `ai_customer_service`           | 业务库名                                          |
| `MYSQL_ROOT_PASSWORD` ⚠️ | `your_root_password`（占位符）  | root 密码（MySQL 容器初始化用），必须修改         |
| `MYSQL_DATABASE`           | `ai_customer_service`           | MySQL 容器自动建库名（与 MYSQL_DB 一致）          |

### JWT

| 参数                   | 默认值                                  | 说明                                                     |
| ---------------------- | --------------------------------------- | -------------------------------------------------------- |
| `JWT_SECRET` ⚠️    | `your_jwt_secret_32plus_chars_random` | 令牌签名密钥，**≥32 位随机串**，否则登录/注册 500 |
| `JWT_EXPIRE_MINUTES` | `1440`                                | 令牌有效期（分钟，默认 24 小时）                         |

### 问答链路

| 参数                        | 默认值   | 说明                                          |
| --------------------------- | -------- | --------------------------------------------- |
| `PROMPT_OPTIMIZE_ENABLED` | `1`    | 是否启用问题优化（改写用户输入）              |
| `TOP_K_RETRIEVE`          | `50`   | 向量召回条数（Bi-Encoder）                    |
| `TOP_K_RERANK`            | `5`    | 重排序返回条数（Cross-Encoder）               |
| `DAILY_QUOTA`             | `100`  | 每用户每日提问上限（超限返回 429）            |
| `MAX_QUESTION_LEN`        | `500`  | 单次提问最大字数                              |
| `CONTEXT_MAX_CHARS`       | `3000` | 多轮上下文全局字符预算                        |
| `HISTORY_RECENT_TURNS`    | `10`   | 保留最近 N 轮对话                             |
| `FALLBACK_COPY`           | 兜底话术 | 空检索兜底文案                                |
| `RAG_MIN_COSINE_SIM`      | `0.0`  | 检索质量门控阈值；0=关闭，>0 按余弦相似度过滤 |

### 小说解构（NOVEL / DECONSTRUCT）

| 参数                          | 默认值   | 说明                                                       |
| ----------------------------- | -------- | ---------------------------------------------------------- |
| `NOVEL_CHAPTER_MAX_CHARS`   | `10000` | 超长章节场景切分阈值（字符，≈8k token）                     |
| `NOVEL_AGENT_MAX_CONCURRENCY` | `5`    | LangGraph 图最大并发（LLM 限流）                            |
| `NOVEL_RECURSION_LIMIT`     | `200`   | 图最大 superstep 数（覆盖默认 25，防大书多章 Send 扇出误触发）|
| `NOVEL_AGENT_MAX_SHRINK`    | `2`     | 单个 Agent 单场景最大缩窗重试次数                           |
| `NOVEL_CHAPTER_LEASE_SECONDS` | `1800` | 僵死章节租约阈值（processing 超时 → 复位 pending 重认领）   |
| `NOVEL_DECONSTRUCT_ON_UPLOAD` | `0`   | 上传是否默认自动解构（0=否；前端显式 deconstruct=1）        |
| `NOVEL_VALIDATOR_ENABLED`   | `0`     | 是否启用 Layer 2 validator_agent 批处理                     |
| `NOVEL_VALIDATOR_BATCH`     | `50`    | validator 批处理大小                                        |
| `NOVEL_SCENE_MAX`           | `100`   | timeline global_sort 合成的场景上限基数                     |
| `DECONSTRUCT_LLM_TEMPERATURE` | `0.0` | 解构分析型 LLM 温度（低随机，保证结构化抽取稳定）           |
| `DECONSTRUCT_LLM_TIMEOUT`   | `120`   | 分析型 LLM 超时（秒）                                       |
| `DECONSTRUCT_LLM_MAX_TOKENS` | `4096` | 分析型 LLM 输出上限                                         |

### 加分项

| 参数                          | 默认值     | 说明                                                          |
| ----------------------------- | ---------- | ------------------------------------------------------------- |
| `INTENT_ENABLED`            | `1`      | 意图识别总开关                                                |
| `INTENT_MODE`               | `hybrid` | `rule`（零成本）/ `llm` / `hybrid`（规则优先+LLM 兜底） |
| `INTENT_TIMEOUT`            | `8`      | 意图 LLM 兜底超时（秒）                                       |
| `FOLLOWUP_ENABLED`          | `1`      | 追问建议总开关                                                |
| `FOLLOWUP_SUGGESTION_COUNT` | `3`      | 追问建议条数                                                  |
| `FOLLOWUP_TIMEOUT`          | `15`     | 追问生成超时（秒）                                            |
| `SESSION_AUTO_TITLE`        | `1`      | 会话自动命名（取首条消息前 20 字）                            |

### 天气（MCP 工具）

| 参数                | 默认值                     | 说明                                              |
| ------------------- | -------------------------- | ------------------------------------------------- |
| `WEATHER_API_KEY` | `your_api_key`（占位符） | 和风天气 API Key（MCP`get_weather` 工具调用用） |
| `WEATHER_API_URL` | `your_api_url`（占位符） | 和风天气 API 地址                                 |

### 日志

| 参数           | 默认值                                        | 说明     |
| -------------- | --------------------------------------------- | -------- |
| `LOG_LEVEL`  | `INFO`                                      | 日志级别 |
| `LOG_FORMAT` | `%(asctime)s - %(levelname)s - %(message)s` | 日志格式 |

### 文档编码

| 参数                         | 默认值                                     | 说明             |
| ---------------------------- | ------------------------------------------ | ---------------- |
| `ENCODING_TRY_ORDER`       | `["utf-8","gb18030","utf-16","latin-1"]` | 编码兜底探测顺序 |
| `ENCODING_DETECTION_BYTES` | `4096`                                   | 探测采样字节数   |
| `MIN_CONTENT_LENGTH`       | `10`                                     | 最小内容长度     |
| `ENCODING_CONFIDENCE`      | `0.7`                                    | 编码判定置信度   |
| `DEFAULT_ENCODING`         | `utf-8`                                  | 默认编码         |

### 内置测试数据

| 参数                  | 默认值                  | 说明                                                                   |
| --------------------- | ----------------------- | ---------------------------------------------------------------------- |
| `SEED_DOCS_ENABLED` | `1`                   | 启动时是否初始化内置测试数据（demo 账号 + 示例知识库），置 0 关闭      |
| `SEED_DOCS_DIR`     | `../docs/start_files` | 示例文档目录（相对`backend/src/`；Docker 下为 `/app/start_files`） |

> 完整部署角度的环境变量清单见仓库根「打包运行执行说明」。

---

## 4. 数据库初始化

初始化脚本位于 `backend/scripts/init_db.sql`，创建库 `ai_customer_service` 与 **19 张表**（4 张基础表 `users` / `sessions` / `messages` / `documents` + 15 张解构表 `novel_chapter` / `deconstruct_job` / `deconstruct_chapter_state` / `entity` / `entity_alias` / `location` / `entity_snapshot` / `entity_relation` / `timeline_event` / `timeline_event_entity` / `location_snapshot` / `foreshadowing` / `story_conflict` / `rule_check` / `validation_issue`），并**预置基础用户**（手机号 `12345678910`，密码 `1234567`，`INSERT IGNORE` 幂等，重复执行不覆盖）。旧库补 `confidence`/`review_status` 列见 `migration_confidence.sql`（幂等）。

- **Docker 方式**：MySQL 容器首次启动时经 `docker-entrypoint-initdb.d` 自动执行该脚本。
- **手动方式**：

```bash
mysql -u root -p ai_customer_service < backend/scripts/init_db.sql
```

---

## 5. 启动后端

### 前置：MySQL + Milvus

本地开发需要 MySQL（3306）与 Milvus（19530）可用，用仓库内单机编排快速拉起：

```bash
docker compose -f DEPLOY/docker-compose.mysql-only.yml up -d    # MySQL，127.0.0.1:3306
docker compose -f DEPLOY/docker-compose.milvus-only.yml up -d   # Milvus 三组件 + Attu，127.0.0.1:19530
```

### 启动命令

```bash
cd backend/src
uvicorn main_fastapi:app --host 0.0.0.0 --port 8000
```

启动（lifespan）按顺序完成 5 步：① 打开 MCP 子进程会话（RAG 工具就绪）→ ② 连接 Milvus 并自愈集合 → ③ 创建 LLM 客户端 → ④ 懒建数据表 → ⑤ **内置测试数据初始化**（`SEED_DOCS_ENABLED=1` 时异步把 `docs/start_files` 导入到基础用户）。

> **小说解构**：`/api/novel/*` 解构接口随后端一起就绪，**仅需 MySQL + LLM**（不依赖 Milvus/GPU，复用同一把 `DeepSeek_API_KEY`）。小说上传（`deconstruct=1`）或前端工作台一键解构触发 LangGraph 逐章解构。

启动后验证：

```bash
curl http://localhost:8000/api/health    # 返回 {"status":"ok"}
```

---

## 6. API Key 配置

`.env` 中至少填写以下必填项，否则对应功能不可用：

| 参数                                         | 影响                                |
| -------------------------------------------- | ----------------------------------- |
| `DeepSeek_API_KEY`                         | 不填则问答无法调用 LLM              |
| `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD` | 不填/占位符则数据库连接与初始化失败 |
| `JWT_SECRET`                               | 过短则注册/登录接口 500             |

其余参数保持默认即可跑通本地开发；`MILVUS_PORT` / `MANUAL_MILVUS_HOST` 等占位符请按实际环境填写。

---

## 7. 内置测试数据（demo 账号）

启动后系统会自动初始化一组**内置测试数据**（对应笔试题「数据初始化建议」：预置示例文档 + 自动向量化，启动后可直接测问答）：

- 基础用户：手机号 `12345678910` / 密码 `1234567`
- 示例知识：`docs/start_files/` 下 3 份文档（公司产品介绍.txt、常见问题FAQ.md、退换货政策.txt），启动时经**真实导入路径**（documents 状态机 → MCP `RAG_init_collection` → Milvus）入库到该用户，book_name 为「示例知识库」
- **幂等**：重启不会重复导入（已存在 ready 行即跳过；content_hash 去重兜底）
- **关闭**：`SEED_DOCS_ENABLED=0` 则不创建用户、不导入

用 demo 账号登录前端，即可在「知识库管理」看到示例文档，并直接在对话页提问测试 RAG 问答。

---

## 8. 测试

```bash
cd backend
pytest
```

- 环境：conda 环境 `env_agent001`（Python 3.11）。
- 测试库隔离：`tests/conftest.py` 自动创建并授权测试库 `ai_customer_service_test`，不污染业务库。
- 配置：`pytest.ini` 已设置 `pythonpath=src`、`testpaths=tests`。
- 当前 **436 个用例全部通过**（61 个测试文件，覆盖 RAG 全链路 + 小说解构 01-06 全链路；2026-08-19 实测）。

---

## 9. 常见问题（简版）

- **首次启动卡在模型下载**：Embedding / Reranker 需从 HuggingFace 下载，`HF_HUB_OFFLINE` 置 `0` 联网一次，缓存后置 `1`。
- **`.env` 占位符**：`MILVUS_PORT`、`MYSQL_PASSWORD` 等默认是占位符，务必替换为真实值。
- **WSL2 连不上 Milvus**：系统自动探测宿主 IP 并加入 `no_proxy` 白名单；失败请在 `.env` 手动设置 `MANUAL_MILVUS_HOST`。
- **爬虫用不到 Playwright**：动态抓取需 `playwright install chromium`（本后端文档不展开，见根 README 已知问题）。

---

## 10. 小说解构子系统速览

后端在 RAG 之上新增**小说解构 / 知识图谱**能力：小说上传 → 章节切分（`novel_chapter`）→ LangGraph 图编排（JobGraph → ChapterGraph → 8 个解构 Agent 并行抽取）→ 归并 → 15 张解构表幂等入库 → 跨章一致性校验 → 人工复核（`validation_issue`）→ Knowledge API（图谱/实体卡/时间线/证据/快照）。二阶段实体卡已升级为 L0-L4 五区（基线/弧光/伏笔规则/明暗 + 三态标注 + 状态累积回填）。

- 架构细节：见 `docs/AI架构设计.md` §17-26。
- 接口契约：见 `docs/API文档.md` §9（`/api/novel/*` 20 端点）。
- 数据库：见 `docs/数据库设计.md` §9（15 张解构表）。
- 二阶段演进：见 `docs/开发阶段文档/spec/小说解构/二阶段开发/`。
