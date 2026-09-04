# Shopkeeper Brain（掌柜大脑）

面向电商场景的 RAG 知识库系统：将商品文档（PDF / Markdown）导入知识库并向量化，再结合多路检索与重排序为商品咨询提供智能问答。

## 核心能力

- **文档导入流水线**：PDF → Markdown → 图片理解 → 切片 → 商品名识别 → 向量化 → 入库
- **智能问答流水线**：商品名确认 → 多路并行检索（向量 / HyDE / 联网）→ RRF 融合 → Rerank → 流式回答
- **图片语义化**：用视觉大模型（VLM）为文档中的图片生成摘要，替换为对象存储链接

## 架构

基于 [LangGraph](https://langchain-ai.github.io/langgraph/) 构建两条状态图流水线。

### 导入流程（`knowledge/processor/import_process/`）

```
entry_node
   ├── (PDF) ──> pdf_to_md_node ──┐
   │                              ├──> md_img_node ──> document_split_node
   └── (MD) ──────────────────────┘        │
                                           v
                        import_milvus_node <── bge_embedding_node <── item_name_rec_node
```

| 节点                  | 职责                                                  |
| --------------------- | ----------------------------------------------------- |
| `entry_node`          | 校验文件、按后缀路由（.pdf / .md）                    |
| `pdf_to_md_node`      | 调用 MinerU 将 PDF 转为 Markdown                      |
| `md_img_node`         | 扫描图片上下文 → VLM 生成摘要 → 上传 MinIO 并替换链接 |
| `document_split_node` | Markdown 切片                                         |
| `item_name_rec_node`  | LLM 识别切片所属商品名                                |
| `bge_embedding_node`  | BGE-M3 向量化                                         |
| `import_milvus_node`  | 写入 Milvus 向量库                                    |

### 查询流程（`knowledge/processor/query_process/`）

```
item_name_confirm ──(无答案)──> multi_search ──┬──> search_embedding ────┐
      │                                        ├──> search_embedding_hyde ├──> join ──> rrf ──> rerank ──> answer_output
      └────────────(有答案)────────────────────┴──> web_search_mcp ──────┘
```

## 目录结构

```
knowledge/
├── api/            # FastAPI 路由（开发中）
├── core/           # 路径、依赖等基础设施
├── front/          # 前端页面（chat.html / import.html）
├── processor/
│   ├── import_process/   # 导入流水线（LangGraph）
│   └── query_process/    # 查询流水线（LangGraph）
├── prompt/         # 提示词模板
├── schema/         # 请求/响应数据模型
├── test/           # 按学习阶段组织的测试脚本
└── utils/          # 客户端封装与工具（Milvus / MinIO / Embedding / SSE 等）
```

## 技术栈

| 类别     | 选型                                            |
| -------- | ----------------------------------------------- |
| 流程编排 | LangGraph                                       |
| PDF 解析 | MinerU                                          |
| 大模型   | 阿里云百炼 DashScope（Qwen 系列 + VL 视觉模型） |
| 向量模型 | BGE-M3（sentence-transformers）                 |
| 向量库   | Milvus                                          |
| 对象存储 | MinIO（图片）                                   |
| 对话历史 | MongoDB                                         |
| 联网搜索 | MCP（DashScope WebSearch）                      |
| Web 框架 | FastAPI（规划中）                               |

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

### 4. 运行导入流水线

```powershell
python processor/import_process/main_graph.py
```

修改文件末尾 `import_file_path` / `file_dir` 为你的文档路径，即可看到逐节点流式执行日志。

## 开发状态

- ✅ 导入流水线：entry / pdf_to_md 已可用；md_img（图片 VLM 摘要 + MinIO 替换）开发中
- ✅ 查询流水线：多路检索 / RRF / Rerank / 回答输出
- 🚧 API 层（`api/`）：路由骨架已建，待实现
- 🚧 前端页面：`front/` 原型

## 后续计划

- [ ] 完成 `md_img_node`：图片上下文扫描、VLM 摘要、MinIO 上传与链接替换、文档备份
- [ ] FastAPI 接口：文件上传导入 + SSE 任务进度推送 + 问答接口
- [ ] 前端联调与部署脚本
