# AI 内容运营助手

一个带人工审核节点的 AI 内容生产工作台：后端使用 LangGraph 编排选题、撰稿、审核、配图流程，前端提供可视化操作界面。文本和图片能力通过火山引擎 Ark 的 OpenAI 兼容接口接入。

## 功能

- 根据内容方向生成候选选题，并由运营人员确认后继续。
- 基于选题生成文章草稿；支持填写审核意见后重新生成。
- 审核通过后提炼配图要点并生成图片。
- 按 `thread_id` 持久化工作流状态与历史快照，支持刷新后继续处理。
- 提供单独的图片生成接口，以及 Vue 3 前端工作台。

## 技术栈

- 后端：Python、FastAPI、LangGraph、SQLAlchemy、PostgreSQL
- AI 服务：火山引擎 Ark（文本与图像生成）
- 前端：Vue 3、TypeScript、Vite

## 项目结构

```text
.
├── backend/                 # FastAPI、LangGraph 工作流与测试
│   ├── app/api/v1/          # 工作流、图片生成 HTTP 接口
│   ├── app/graph/           # 内容工作流节点和状态定义
│   └── .env.example         # 环境变量模板（不含密钥）
└── frontend/                # Vue 3 操作界面
```

## 快速开始

### 1. 准备依赖

需要本地安装：

- Python 3.10 或更高版本
- Node.js 20 或更高版本
- PostgreSQL（创建一个可连接的数据库）

创建数据库示例：

```sql
CREATE DATABASE aicontent;
```

### 2. 配置后端

在项目根目录执行：

```powershell
cd backend
Copy-Item .env.example .env
```

编辑 `backend/.env`，至少填写 `VOLCENGINE_API_KEY`；并按实际 PostgreSQL 账号修改 `DATABASE_URL`。`ARK_API_KEY` 为空时会复用 `VOLCENGINE_API_KEY`。

```env
VOLCENGINE_API_KEY=你的火山引擎_Ark_API_Key
VOLCENGINE_MODEL=doubao-seed-2-1-pro-260915
VOLCENGINE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

# 可选：不填时复用 VOLCENGINE_API_KEY
ARK_API_KEY=
ARK_IMAGE_MODEL=doubao-seedream-5-0-260128

DATABASE_URL=postgresql+asyncpg://用户名:密码@localhost:5432/aicontent
```

> `.env` 已被 Git 忽略。请不要将真实 API Key 或数据库密码提交到仓库。

安装并启动后端：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app/main.py
```

服务默认监听 `http://127.0.0.1:8001`，交互式接口文档位于 [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)。

### 3. 启动前端

另开一个终端，在项目根目录执行：

```powershell
cd frontend
npm install
npm run dev
```

打开 Vite 输出的地址（默认是 [http://127.0.0.1:5173](http://127.0.0.1:5173)）。开发服务器会把 `/api` 请求代理到后端 `8001` 端口。

构建生产前端资源：

```powershell
npm run build
```

若生产环境前后端不在同一域名下，设置 `VITE_API_BASE_URL`，例如：

```env
VITE_API_BASE_URL=https://your-api.example.com/api/v1
```

## 工作流

```text
输入内容方向
  → 生成候选选题
  → 人工选择选题
  → 生成文章草稿
  → 人工审核 ── 驳回 → 根据意见重写
       │
       └── 通过 → 提炼配图要点 → 生成图片 → 完成
```

人工选择和审核时，工作流会在 PostgreSQL 检查点中暂停；后续请求用同一个 `thread_id` 恢复，所以可以安全地获取状态和历史记录。

## API 概览

所有业务接口前缀为 `/api/v1`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/workflow/start` | 输入内容方向，创建工作流并返回候选选题 |
| `POST` | `/workflow/resume/{thread_id}` | 提交选题、通过审核或驳回意见 |
| `GET` | `/workflow/state/{thread_id}` | 获取当前状态 |
| `GET` | `/workflow/history/{thread_id}` | 获取状态快照历史 |
| `POST` | `/images/generate` | 按提示词直接生成一张图片 |
| `GET` | `/health` | 健康检查 |

启动工作流：

```bash
curl -X POST http://127.0.0.1:8001/api/v1/workflow/start \
  -H "Content-Type: application/json" \
  -d '{"topic_direction":"面向职场新人的 AI 效率工具"}'
```

选择接口返回的 `generated_topics` 中某一项：

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

驳回时将 `action` 设为 `reject`，并在 `data.review_feedback` 中提供修改意见。

## 测试

后端接口测试会替换数据库和 AI 服务，不需要真实密钥或 PostgreSQL：

```powershell
cd backend
pytest
```

## 安全说明

- `backend/.env` 仅供本地使用，已在根目录 `.gitignore` 中排除。
- 使用 `backend/.env.example` 共享配置字段，不要填入真实密钥。
- 如果曾意外提交密钥，请立即在服务商控制台轮换该密钥；单纯删除文件无法消除 Git 历史中的泄露风险。
