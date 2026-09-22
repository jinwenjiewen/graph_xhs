# AI 内容运营助手

一个面向内容运营流程的 AI 工作台。它把选题、写作、人工审核和视觉素材生成组织为可暂停、可恢复的 LangGraph 工作流，并将工作流状态持久化到 PostgreSQL。Vue 前端提供会话管理、人工决策、历史回看和节点性能指标。

## 能力概览

- **人机协作工作流**：AI 生成候选选题和文章草稿；人工确认选题、审核通过或填写意见驳回重写。
- **持久化与恢复**：每个工作流使用独立的 `thread_id`，状态、历史快照与待执行节点均保存到 PostgreSQL；服务重启或页面关闭后仍可继续。
- **会话管理**：查看、切换和删除历史会话；删除会同时清理该会话的检查点数据。
- **视觉素材生成**：审核通过后提炼视觉提示词，再调用火山引擎 Ark 文生图服务生成图片 URL。
- **性能可观测性**：记录每个图节点的耗时、模型调用次数和上游返回的真实 Token 用量；缺少上游用量时明确标记为未知，不做估算。
- **前端控制台**：展示工作流进度、候选选题、草稿、审核操作、历史快照、生成图片和节点指标。

## 架构

```text
Vue 3 + Vite（:5173）
        │ /api 代理
        ▼
FastAPI（:8001）
        │
        ├── LangGraph 内容工作流 ───────────────┐
        │                                       │
        ├── 火山引擎 Ark：文本 / 图像生成        │
        │                                       ▼
        └── psycopg_pool ───────────────► PostgreSQL
                                      （业务连接池 + LangGraph 检查点）
```

## 工作流

```text
输入内容方向
  → 生成候选选题
  → 【人工】确认选题
  → 生成文章草稿
  → 【人工】审核内容 ── 驳回 → 按意见重写 → 再次审核
       │
       └── 通过 → 提炼视觉提示词 → 生成视觉素材 → 完成
```

两个人工节点会主动暂停工作流。其余自动节点可从已持久化的检查点继续执行，不会跳过人工决策。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite |
| 后端 | Python、FastAPI、Pydantic Settings |
| 工作流 | LangGraph 1.0、PostgreSQL Checkpointer |
| 数据库 | PostgreSQL、psycopg 3、psycopg_pool |
| 模型服务 | 火山引擎 Ark OpenAI 兼容接口 |

## 项目结构

```text
.
├── backend/
│   ├── app/api/v1/          # 工作流和图片 HTTP 接口
│   ├── app/core/            # 配置与 PostgreSQL 连接池
│   ├── app/graph/           # LangGraph、节点、状态与指标记录
│   ├── app/services/        # Ark 文本与图像服务
│   ├── tests/               # 单元与接口测试
│   └── .env.example         # 环境变量模板（不含密钥）
├── frontend/
│   └── src/                 # Vue 工作流控制台
└── README.md
```

## 快速开始

### 1. 前置条件

- Python 3.10+
- Node.js 20+
- 可访问的 PostgreSQL 实例
- 火山引擎 Ark 的文本生成和图像生成模型配置

先创建数据库（名称可自定义，但须与配置一致）：

```sql
CREATE DATABASE aicontent;
```

### 2. 配置后端

在项目根目录执行：

```powershell
cd backend
Copy-Item .env.example .env
```

编辑 `backend/.env`。文本模型与图像模型使用独立配置，不能互相复用；请填写你自己的凭据。

```env
# 文本生成
VOLCENGINE_API_KEY=你的文本模型_API_Key
VOLCENGINE_MODEL=doubao-seed-2-1-pro-260915
VOLCENGINE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

# 图像生成
ARK_API_KEY=你的图像模型_API_Key
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_IMAGE_MODEL=doubao-seedream-5-0-260128
ARK_IMAGE_SIZE=2K
ARK_IMAGE_WATERMARK=false

# PostgreSQL：使用 psycopg 连接串，不要使用 SQLAlchemy 的 asyncpg 方言
DATABASE_URL=postgresql://用户名:密码@localhost:5432/aicontent
DATABASE_POOL_MIN_SIZE=1
DATABASE_POOL_MAX_SIZE=5
```

`DATABASE_POOL_MIN_SIZE` 是启动时预热的最小连接数，`DATABASE_POOL_MAX_SIZE` 是连接池上限。LangGraph 检查点和数据库访问共用这个连接池。

> `backend/.env` 已被 Git 忽略。仓库只保留 [`.env.example`](backend/.env.example)，请勿提交真实 API Key、数据库密码或生产地址。

安装依赖并启动后端：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app/main.py
```

后端默认地址是 `http://127.0.0.1:8001`，接口文档为 [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)。

### 3. 启动前端

另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。开发服务器会将 `/api` 代理到后端 `8001` 端口。

构建生产前端：

```powershell
npm run build
```

若生产环境中前后端不同域，在前端环境变量中指定 API 前缀：

```env
VITE_API_BASE_URL=https://your-api.example.com/api/v1
```

## API

所有业务接口以 `/api/v1` 为前缀。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/workflow/start` | 新建工作流，生成候选选题并暂停 |
| `POST` | `/workflow/resume/{thread_id}` | 提交选题、审核通过或驳回意见 |
| `POST` | `/workflow/continue/{thread_id}` | 从自动节点的检查点继续，不会跳过人工节点 |
| `GET` | `/workflow/state/{thread_id}` | 获取会话当前状态 |
| `GET` | `/workflow/history/{thread_id}` | 获取会话历史快照 |
| `GET` | `/workflow/threads?limit=30` | 按最近更新时间列出可恢复会话 |
| `DELETE` | `/workflow/threads/{thread_id}` | 删除会话及全部检查点，操作不可恢复 |
| `POST` | `/images/generate` | 按提示词直接生成一张图片 |
| `GET` | `/health` | 健康检查 |

创建工作流：

```bash
curl -X POST http://127.0.0.1:8001/api/v1/workflow/start \
  -H "Content-Type: application/json" \
  -d '{"topic_direction":"面向职场新人的 AI 效率工具"}'
```

从返回结果取出 `thread_id`，并从 `generated_topics` 选择一个选题：

```bash
curl -X POST http://127.0.0.1:8001/api/v1/workflow/resume/<thread_id> \
  -H "Content-Type: application/json" \
  -d '{"action":"select_topic","data":{"selected_topic":"候选选题原文"}}'
```

审核通过：

```bash
curl -X POST http://127.0.0.1:8001/api/v1/workflow/resume/<thread_id> \
  -H "Content-Type: application/json" \
  -d '{"action":"approve","data":{}}'
```

审核驳回时使用 `action: "reject"`，并传入非空的 `data.review_feedback`。如果进程在自动节点中断，可调用以下接口恢复：

```bash
curl -X POST http://127.0.0.1:8001/api/v1/workflow/continue/<thread_id>
```

## 可观测性

每次图节点执行后，状态中的 `node_metrics` 会追加一条记录：节点名称、开始时间、耗时（毫秒）、模型调用次数、输入/输出/总 Token。Token 仅使用上游 SDK 的真实返回；上游未提供时字段为 `null`。

前端的“节点耗时与 Token”面板会展示这些数据。历史会话只有在升级后的节点再次执行时才会开始拥有指标记录。

## 测试

后端测试会注入内存工作流与假模型服务，不需要 PostgreSQL 或真实密钥：

```powershell
cd backend
pytest
```

前端类型检查与生产构建：

```powershell
cd frontend
npm run build
```

## 安全说明

- 不要提交 `backend/.env`；它已由根目录 `.gitignore` 排除。
- 只提交 `.env.example` 的字段模板，不要将示例替换为真实凭据。
- 若密钥曾提交到任何 Git 历史，请立即在服务商控制台轮换密钥；删除文件不能清除历史泄露。
