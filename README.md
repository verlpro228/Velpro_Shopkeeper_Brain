# Velpro Brain

面向电商场景的 RAG 知识库系统：将商品文档（PDF / Markdown）导入知识库并向量化，再结合多路检索与重排序为商品咨询提供智能问答。前后端分离架构，Vue 3 前端 + FastAPI 后端（导入服务 / 查询服务两个独立进程）。

## 核心能力

- **文档导入流水线**：PDF → Markdown → 图片理解 → 切片 → 商品名识别 → 向量化 → 入库
- **智能问答流水线**：商品名确认 → 多路并行检索（向量 / HyDE / 联网）→ RRF 融合 → Rerank → 流式回答
- **流式问答**：SSE 推送节点进度（`progress`）、LLM 增量输出（`delta`）、最终答案（`final`），打字机效果
- **多轮对话**：会话历史存 MongoDB，支持按 `session_id` 查询与清空
- **图片语义化**：用视觉大模型（VLM）为文档中的图片生成摘要，替换为对象存储链接
- **任务追踪与取消**：每次上传生成唯一 `task_id`，实时查询任务状态、运行中/已完成节点与各节点耗时，支持中途取消导入
- **登录认证**：HMAC-SHA256 签名令牌（轻量自研，非标准 JWT），保护全部业务接口

## 系统架构

```
                 ┌─────────────────────────────┐
                 │   Vue 3 前端（web/ → dist）   │
                 │   /login   /chat   /import  │
                 └──────────┬──────────────────┘
              8001 │             │ 8000
        ┌──────────▼─────┐   ┌───▼────────────┐
        │ 查询服务         │   │ 导入服务        │
        │ query_router   │   │ import_router  │
        └──────────┬─────┘   └───┬────────────┘
                   │             │
        ┌──────────▼─────────────▼───────────┐
        │      LangGraph 状态图流水线          │
        └──────────┬─────────────┬───────────┘
                   │             │
     Milvus / MinIO / MongoDB / DashScope / MinerU
```

### 导入流程（`knowledge/processor/import_process/`）

```
entry_node
   ├── (PDF) ──> pdf_to_md_node ──┐
   │                              ├──> md_img_node ──> document_split_node ──> item_name_rec_node
   └── (MD) ──────────────────────┘                                        │
                                                                           v
                                                        bge_embedding_node ──> import_milvus_node
```

| 节点                         | 职责                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------ |
| `entry_node`                 | 校验文件、按后缀路由（.pdf / .md）                                                   |
| `pdf_to_md_node`             | 调用 MinerU 将 PDF 转为 Markdown                                                     |
| `md_img_node`                | 扫描图片上下文 → VLM 生成摘要 → 上传 MinIO 并替换链接                                |
| `document_split_node`        | 按标题层级切片，超长二次切分、过短贪心合并                                           |
| `item_name_rec_node`         | LLM 识别商品名 → 双向量写入独立商品名集合（`kb_item_names_v1`）→ 回填每个切片        |
| `bge_embedding_node`         | BGE-M3 对切片向量化（dense + sparse 双路）                                           |
| `import_milvus_node`         | 切片向量写入 Milvus 知识库集合（`kb_chunks_v1`）                                     |

### 查询流程（`knowledge/processor/query_process/`）

```
item_name_confirm ──(无答案)──> multi_search ──┬──> search_embedding ────┐
      │                                        ├──> search_embedding_hyde ├──> join ──> rrf ──> rerank ──> answer_output
      └────────────(有答案)────────────────────┴──> web_search_mcp ──────┘
```

| 节点                   | 职责                                                                       |
| ---------------------- | -------------------------------------------------------------------------- |
| `item_name_confirm`    | 确认问题主体商品名，能直接回答则短路直达输出                               |
| `search_embedding`     | 问题 BGE-M3 向量化 → Milvus 混合检索（dense + sparse，商品名过滤）         |
| `search_embedding_hyde`| HyDE 假设性文档检索：LLM 先生成假设答案再向量检索                          |
| `web_search_mcp`       | 通过 MCP 调用 DashScope WebSearch 联网补充                                 |
| `rrf`                  | 倒数排序融合（RRF）合并三路召回结果                                        |
| `rerank`               | bge-reranker-large 精排                                                   |
| `answer_output`        | LLM 结合历史上下文生成答案，流式增量推入 SSE 队列并写入 MongoDB            |

## 目录结构

```
knowledge/
├── api/            # FastAPI 服务入口（import_router / query_router / auth_router）
├── core/           # 基础设施：paths（路径常量）、deps（依赖注入单例）
├── processor/
│   ├── import_process/   # 导入流水线（LangGraph：main_graph + nodes/ + config + exceptions）
│   └── query_process/    # 查询流水线（LangGraph：main_graph + nodes/ + config + exceptions）
├── prompt/         # 提示词模板
├── schema/         # 请求/响应数据模型（认证 / 上传 / 查询）
├── service/        # 业务层（file_import_service / query_service）
├── test/           # 按学习阶段组织的测试脚本
├── utils/          # 客户端封装与工具（Milvus / MinIO / Embedding / SSE / 任务追踪 / 认证等）
├── web/            # Vue 3 + TypeScript 前端工程（Vite 构建，产物 dist/ 由后端托管）
└── temp_data/      # 上传文件本地暂存（按日期/任务ID组织，运行时自动创建）
```

## 技术栈

| 类别     | 选型                                            |
| -------- | ----------------------------------------------- |
| 流程编排 | LangGraph                                       |
| 后端     | FastAPI + Uvicorn                               |
| 前端     | Vue 3 + TypeScript + Vite + vue-router          |
| PDF 解析 | MinerU                                          |
| 大模型   | 阿里云百炼 DashScope（Qwen 系列 + VL 视觉模型） |
| 向量模型 | BGE-M3（本地，sentence-transformers / pymilvus）|
| 重排序   | bge-reranker-large（本地）                      |
| 向量库   | Milvus（dense + sparse 混合检索）               |
| 对象存储 | MinIO（原图 + 图片链接）                        |
| 对话历史 | MongoDB                                         |
| 联网搜索 | MCP（DashScope WebSearch）                      |
| 认证     | HMAC-SHA256 签名令牌（自研轻量实现）            |

## 快速开始

### 1. 环境准备

要求 Python 3.10+、Node.js 18+（包管理器 pnpm），Windows / Linux 均可。

```powershell
cd knowledge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> MinerU 首次运行需要下载模型，可通过 `.env` 中的 `MINERU_MODEL_SOURCE` / `MODELSCOPE_CACHE` / `HF_HOME` 配置模型源与缓存目录。

### 2. 配置密钥

在 `knowledge/` 目录创建 `.env` 并填写各项密钥与地址，关键配置项分组如下：

| 分组     | 配置项                                                                 |
| -------- | ---------------------------------------------------------------------- |
| 大模型   | `OPENAI_API_BASE` / `OPENAI_API_KEY`（DashScope 兼容模式）、`VL_MODEL`、`ITEM_MODEL` |
| 向量化   | `BGE_DEVICE`、`BGE_RERANKER_LARGE`、`BGE_RERANKER_DEVICE`              |
| Milvus   | `MILVUS_URL`、`CHUNKS_COLLECTION`、`ITEM_NAME_COLLECTION`、`MILVUS_MIN_COSINE_SCORE` |
| MinIO    | `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET_NAME` |
| MongoDB  | `MONGO_URL`、`MONGO_DB_NAME`                                           |
| 联网搜索 | `MCP_DASHSCOPE_BASE_URL`                                               |
| 认证     | `AUTH_USERNAME`、`AUTH_PASSWORD`、`AUTH_TOKEN_SECRET`、`AUTH_EXPIRES_SECONDS` |

> ⚠️ `.env` 已被 `.gitignore` 忽略，切勿提交真实密钥。

前端 API 地址有独立模板（仅开发模式需要）：

```powershell
cd web
copy .env.example .env   # 配置 VITE_QUERY_API_BASE / VITE_IMPORT_API_BASE
```

### 3. 依赖服务

本地或远程部署以下服务：Milvus、MinIO、MongoDB。

连通性自检：

```powershell
cd knowledge
python test/import_process/day01_env/01_test_connections.py
```

### 4. 构建前端

```powershell
cd knowledge/web
pnpm install
pnpm build        # 产物输出到 web/dist，由后端静态托管
```

### 5. 启动后端服务

在项目根目录执行，两个服务分别监听 8000 / 8001 端口：

```powershell
# 导入服务（含前端页面托管）
uvicorn knowledge.api.import_router:create_app --factory --host 0.0.0.0 --port 8000

# 查询服务（含前端页面托管）
uvicorn knowledge.api.query_router:create_app --factory --host 0.0.0.0 --port 8001
```

前端开发模式（可选）：`cd knowledge/web && pnpm dev`，Vite 启动在 5173 端口，通过 `.env` 中的 `VITE_*_API_BASE` 直连两个后端。

### 6. 访问系统

浏览器打开 `http://localhost:8000`（或 8001），默认账号 `admin` / `666666`（可通过 `AUTH_USERNAME` / `AUTH_PASSWORD` 覆盖）。

前端路由：`/login` 登录页、`/chat` 智能问答、`/import` 知识库导入，未登录访问受保护页面会自动跳转登录页。

Swagger 接口文档：`http://localhost:8000/docs` 与 `http://localhost:8001/docs`。

## API 概览

所有业务接口均需认证：请求头 `Authorization: Bearer <token>`；SSE 等无法设置请求头的场景支持 `?access_token=<token>` 查询参数。

### 导入服务（:8000）

| 方法 | 路径                | 说明                                                                     |
| ---- | ------------------- | ------------------------------------------------------------------------ |
| POST | `/auth/login`       | 登录，返回签名令牌（默认 7 天有效）                                      |
| GET  | `/auth/me`          | 获取当前登录用户                                                         |
| POST | `/upload`           | multipart 上传文件（.pdf / .md，≤50MB），返回 `task_id`，后台异步执行导入流水线 |
| GET  | `/status/{task_id}` | 查询任务状态：`status` / `done_list` / `running_list` / `durations`      |
| POST | `/cancel/{task_id}` | 取消进行中的导入任务                                                     |

任务状态取值：`processing`（处理中）/ `completed`（已完成）/ `failed`（失败）/ `cancelled`（已取消）。

上传前置校验：文件名安全（防目录穿越）→ 后缀白名单（.pdf / .md）→ 大小上限（50MB），校验失败直接返回 400，不产生任务。

### 查询服务（:8001）

| 方法   | 路径                    | 说明                                                                        |
| ------ | ----------------------- | --------------------------------------------------------------------------- |
| POST   | `/query`                | 提问。`is_stream=false` 同步返回答案（120s 超时）；`is_stream=true` 返回 `task_id` |
| GET    | `/stream/{task_id}`     | SSE 流式响应：`progress`（节点进度）→ `delta`（增量文本）→ `final`（完整答案） |
| GET    | `/history/{session_id}` | 查询会话历史（`limit` 上限 200）                                            |
| DELETE | `/history/{session_id}` | 清空会话历史                                                                |

流式时序：`POST /query`（`is_stream=true`）→ 拿到 `task_id` → `GET /stream/{task_id}` 建立 SSE 连接 → 依次消费 `progress` / `delta` / `final` 事件。

## 命令行调试

不经过 API 直接跑流水线（需先改脚本末尾的文件路径）：

```powershell
# 项目根目录下执行
python -m knowledge.processor.import_process.main_graph   # 导入流程，逐节点流式日志
python -m knowledge.processor.query_process.main_graph    # 查询流程，打印图结构 ASCII 图
```


