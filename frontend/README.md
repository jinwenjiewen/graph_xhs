# 内容 Agent 工作台

Vue 3 前端，位于 `backend` 同级目录，通过 Vite 开发服务器代理调用后端。

## 运行

先在一个终端启动后端（默认 `http://127.0.0.1:8001`），再执行：

```bash
cd frontend
npm install
npm run dev
```

打开 Vite 显示的本地地址（默认 `http://127.0.0.1:5173`）。

## 生产构建

```bash
npm run build
```

如果部署时前后端不共域，可新建 `.env.production` 并设置 `VITE_API_BASE_URL`，例如：

```env
VITE_API_BASE_URL=https://your-api.example.com/api/v1
```
