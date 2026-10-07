# AIPAAS 前端（React + Vite）

金融标准化知识库前端工程，仿 DeepSeek 对话风格重构。

## 快速开始

```bash
cd frontend
npm install
npm run dev      # 开发：http://localhost:5173
npm run build    # 构建：产物在 dist/
npm run preview  # 本地预览构建产物
```

## 工程目录结构

```
frontend/
├── index.html                  # SPA 入口
├── package.json
├── vite.config.js              # Vite 配置（含 /query-api、/import-api 开发代理）
├── .env.example                # 环境变量示例（API 地址）
└── src/
    ├── main.jsx                # React 挂载入口
    ├── App.jsx                 # 路由定义（登录页独立，业务页统一布局）
    ├── styles/
    │   └── global.css          # 全局样式 / 通用按钮 / 输入框
    ├── api/                    # API 层（与后端接口一一对应）
    │   ├── config.js           # API 地址环境配置
    │   ├── chat.js             # 查询服务（8001）：/query /stream /history /health
    │   └── importer.js         # 导入服务（8000）：/upload /status
    ├── utils/
    │   ├── auth.js             # 登录态管理（当前 Mock，后端就绪后替换）
    │   ├── sessions.js         # 会话列表管理（当前 localStorage，后端就绪后替换）
    │   └── answerParser.js     # 答案/图片解析（移植自旧版 chat.html）
    ├── components/
    │   ├── AppLayout.jsx       # 统一布局：顶部导航 + 左侧对话历史侧边栏
    │   ├── RequireAuth.jsx     # 登录路由守卫
    │   ├── ProgressPanel.jsx   # 阶段进度面板（时间轴 + 进度条 + 状态徽标）
    │   └── AnswerBody.jsx      # 答案正文（文本 + 参考图片）
    └── pages/
        ├── Login.jsx           # 登录页（成功后跳转 /chat）
        ├── Chat.jsx            # 对话页（流式 SSE / 非流式 / 历史）
        ├── Import.jsx          # 文件导入页（拖拽上传 + 任务轮询）
        └── UserCenter.jsx      # 个人中心
```

## 路由

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/login` | 登录页 | 未登录访问业务页会被重定向至此 |
| `/chat` | 对话页 | 新会话模式（首条消息自动建会话） |
| `/chat/:sessionId` | 对话页 | 打开指定历史会话 |
| `/import` | 文件导入页 | 侧边栏底部入口进入，页内可返回对话 |
| `/user` | 个人中心 | 顶栏用户头像入口 |

## 与后端的对接

- 开发环境通过 Vite 代理转发，避免跨域：
  - `/query-api/**` → `http://127.0.0.1:8001/**`
  - `/import-api/**` → `http://127.0.0.1:8000/**`
- 生产环境通过 `.env` 配置 `VITE_QUERY_API_BASE` / `VITE_IMPORT_API_BASE`。

## 已知限制（详见 docs/05_待办事项.md）

- 登录/个人中心为本地 Mock（后端缺少 `/api/auth/*`、`/api/user/*` 接口）。
- 对话历史列表暂存 localStorage（后端缺少 `/api/sessions` 系列接口）。
- 旧版页面 `pymodules/app/rag/pages/*.html` 仍保留，待联调稳定后下线。
