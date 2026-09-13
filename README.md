# Shopkeeper Brain（掌柜大脑）

面向电商场景的 RAG 知识库系统：将商品文档（PDF / Markdown）导入知识库并向量化，再结合多路检索与重排序为商品咨询提供智能问答。

## 核心能力

- **文档导入流水线**：PDF → Markdown → 图片理解 → 切片 → 商品名识别 → 向量化 → 入库
- **智能问答流水线**：商品名确认 → 多路并行检索（向量 / HyDE / 联网）→ RRF 融合 → Rerank → 流式回答
- **图片语义化**：用视觉大模型（VLM）为文档中的图片生成摘要，替换为对象存储链接
- **任务追踪**：每次上传生成唯一 `task_id`，实时查询任务状态、运行中/已完成节点与各节点耗时

## 架构

基于 [LangGraph](https://langchain-ai.github.io/langgraph/) 构建两条状态图流水线。

### 导入流程（`knowledge/processor/import_process/`）

```
entry_node
   ├── (PDF) ──> pdf_to_md_node ──┐
   │                              ├──> md_img_node ──> document_split_node ──> item_name_recognition_node
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
| `item_name_recognition_node` | LLM 识别商品名 → BGE-M3 双向量写入独立商品名集合（`kb_item_names_v1`）→ 回填每个切片 |
| `bge_embedding_node`         | BGE-M3 对切片向量化（dense + sparse 双路）                                           |
| `import_milvus_node`         | 切片向量写入 Milvus 知识库集合                                                       |

### 查询流程（`knowledge/processor/query_process/`）

```
item_name_confirm ──(无答案)──> multi_search ──┬──> search_embedding ────┐
      │                                        ├──> search_embedding_hyde ├──> join ──> rrf ──> rerank ──> answer_output
      └────────────(有答案)────────────────────┴──> web_search_mcp ──────┘
```

## 目录结构

```
knowledge/
├── api/            # FastAPI 路由（import_router 已实现，query_router 占位）
├── core/           # 路径、依赖等基础设施
├── front/          # 前端页面原型（chat.html / import.html）
├── processor/
│   ├── import_process/   # 导入流水线（LangGraph）
│   └── query_process/    # 查询流水线（LangGraph，图骨架已建，节点待实现）
├── prompt/         # 提示词模板
├── schema/         # 请求/响应数据模型（上传 / 查询 / 任务状态）
├── service/        # 业务层（文件上传、启动导入流程）
├── test/           # 按学习阶段组织的测试脚本
└── utils/          # 客户端封装与工具（Milvus / MinIO / Embedding / SSE / 任务追踪等）
```

## 技术栈

| 类别     | 选型                                            |
| -------- | ----------------------------------------------- |
| 流程编排 | LangGraph                                       |
| Web 框架 | FastAPI + Uvicorn                               |
| PDF 解析 | MinerU                                          |
| 大模型   | 阿里云百炼 DashScope（Qwen 系列 + VL 视觉模型） |
| 向量模型 | BGE-M3（sentence-transformers）                 |
| 向量库   | Milvus                                          |
| 对象存储 | MinIO（图片）                                   |
| 对话历史 | MongoDB                                         |
| 联网搜索 | MCP（DashScope WebSearch）                      |

## 快速开始

### 1. 环境准备

要求 Python 3.10+，Windows / Linux 均可。

```powershell
cd knowledge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> MinerU 首次运行需要下载模型，可通过 `.env` 中的 `MINERU_MODEL_SOURCE` / `MODELSCOPE_CACHE` / `HF_HOME` 配置模型源与缓存目录。

### 2. 配置密钥

复制模板并填写各项密钥与地址：

```powershell
copy .env.example .env
```

必填项：`DASHSCOPE_API_KEY`（大模型）、`MINIO_*`（图片存储）、`MILVUS_URL`（向量库）、`MONGO_URL`（对话历史）。

> ⚠️ `.env` 已被 `.gitignore` 忽略，切勿提交真实密钥。

### 3. 依赖服务

本地或远程部署以下服务：Milvus、MinIO、MongoDB。

连通性自检：

```powershell
python test/import_process/day01_env/01_test_connections.py
```

### 4. 启动 API 服务（推荐）

在项目根目录（`shopkeeper_brain/`）执行：

```powershell
uvicorn knowledge.api.import_router:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

启动后访问 `http://localhost:8000/docs` 查看自动生成的 Swagger 接口文档。

| 方法 | 路径                | 说明                                                                     |
| ---- | ------------------- | ------------------------------------------------------------------------ |
| GET  | `/import`           | 导入页面前端页面                                                         |
| POST | `/upload`           | multipart 上传文件（.pdf / .md），返回 `task_id`，后台异步执行导入流水线 |
| GET  | `/status/{task_id}` | 查询任务状态：`status` / `done_list` / `running_list` / `durations`      |

任务状态取值：`processing`（处理中）/ `completed`（已完成）/ `failed`（失败）。

> ⚠️ 任务状态保存在内存字典中，服务重启后历史 `task_id` 会失效，需重新上传。

### 5. 命令行运行导入流水线（调试用）

```powershell
python processor/import_process/main_graph.py
```

修改文件末尾 `import_file_path` / `file_dir` 为你的文档路径，即可看到逐节点流式执行日志。

## 上传处理流程

`POST /upload` 后的完整链路：

1. 业务层生成 8 位 `task_id`，文件**双写**：保存本地 `temp_data/{日期}/{task_id}/` + 上传 MinIO（任一失败不影响主流程，降级继续）
2. `BackgroundTasks` 异步启动导入流水线，逐节点执行并更新任务状态与耗时
3. 前端轮询 `GET /status/{task_id}` 展示进度

## 项目现状

- **导入流水线**：7 个节点均已实现并可通过 API 端到端跑通（上传 → 后台导入 → 状态查询）
- **查询流水线**：图骨架已搭建（状态定义 / 基类 / 主图结构 / 异常体系），7 个节点实现为空文件，待开发；`query_router.py` 亦为占位
- **API 层**：`import_router.py` 已实现 `/import`、`/upload`、`/status/{task_id}`；任务状态内存追踪（`utils/task_util.py`），含节点中文名映射
- **前端**：`front/` 下 `chat.html`（问答）与 `import.html`（导入）两个页面原型
