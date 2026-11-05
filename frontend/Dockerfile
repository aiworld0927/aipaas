# ===========================================================================
# AIPaaS frontend（Vite + React）多阶段构建
# ---------------------------------------------------------------------------
# 阶段 1（build）：node:20-alpine 执行 npm ci + vite build，产出 dist/
# 阶段 2（runtime）：nginx:alpine 只携带 dist/ + 自定义 nginx.conf
#
# 构建：
#     docker build -t aipaas-frontend:latest .
#
# 运行（反代到本机后端）：
#     docker run -d --name aipaas-frontend \
#         -p 80:80 \
#         -e QUERY_API_UPSTREAM=http://host.docker.internal:8001 \
#         -e IMPORT_API_UPSTREAM=http://host.docker.internal:8000 \
#         aipaas-frontend:latest
#
# 运行（docker-compose 里反代到服务名）：
#     QUERY_API_UPSTREAM=http://python-service:8001
#     IMPORT_API_UPSTREAM=http://python-service:8000
# ===========================================================================

# ---- 阶段 1：构建 ----
FROM node:20-alpine AS build
WORKDIR /app

# 先只复制依赖清单 → npm ci 层可被 Docker 缓存命中，改 src 不会重装依赖
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# 可选：如果用户有 .env（VITE_QUERY_API_BASE 等），一起复制进去
COPY .env* ./

# 复制源码 + 构建
COPY . .
RUN npm run build

# ---- 阶段 2：运行 ----
FROM nginx:alpine AS runtime

# 把构建产物拷到 nginx 默认站点目录
COPY --from=build /app/dist /usr/share/nginx/html

# 自定义 nginx 配置（SPA fallback + /query-api /import-api 反代）
COPY nginx.conf.template /etc/nginx/nginx.conf.template

# 默认反代目标（会被运行时环境变量覆盖）
ENV QUERY_API_UPSTREAM=http://localhost:8001 \
    IMPORT_API_UPSTREAM=http://localhost:8000

EXPOSE 80

# 用 envsubst 把 nginx.conf.template 里的占位符替换成运行时 env，
# 然后前台启动 nginx（容器退出会自动停止）
CMD ["/bin/sh", "-c", \
     "envsubst '${QUERY_API_UPSTREAM} ${IMPORT_API_UPSTREAM}' \
      < /etc/nginx/nginx.conf.template \
      > /etc/nginx/nginx.conf \
      && nginx -g 'daemon off;'"]
