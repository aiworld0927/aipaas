# ===========================================================================
# AIPaaS frontend（Vite + React）多阶段构建
# ---------------------------------------------------------------------------
# 阶段 1（build）：node:20-alpine 执行 npm ci + vite build，产出 dist/
# 阶段 2（runtime）：nginx:alpine 只携带 dist/ + 自定义 nginx.conf
#
# 构建（手动）：
#     cd /coding/aipaas/web
#     docker build -t aipaas/frontend:latest .
#     前端构建只跑 npm ci + vite build，从 npm registry 取包，
#     不需要 torch / NVIDIA CUDA wheel，因此也不需要 --network host。
#
# 运行（手动，反代到本机后端）：
#     docker run -d --name aipaas-frontend \
#         -p 80:80 \
#         -e QUERY_API_UPSTREAM=http://host.docker.internal:8001 \
#         -e IMPORT_API_UPSTREAM=http://host.docker.internal:8000 \
#         aipaas/frontend:latest
#
# 运行（docker-compose，反代到服务名）：
#     QUERY_API_UPSTREAM=http://svc-query:8002
#     IMPORT_API_UPSTREAM=http://svc-ingest:8001
# ===========================================================================

# ---- 阶段 1：构建 ----
FROM node:20-alpine AS build
WORKDIR /app

# 先只复制依赖清单 → npm ci 层可被 Docker 缓存命中，改 src 不会重装依赖
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# 关键：把容器自己的 node_modules 挪到隐藏目录，
# 这样后面 COPY . . 时（无论宿主有没有 node_modules、有没有 .dockerignore）
# 都不会覆盖/破坏容器已装的依赖
RUN mv /app/node_modules /app/.container-node_modules

# 可选：如果用户有 .env（VITE_QUERY_API_BASE 等），一起复制进去
COPY .env* ./

# 复制源码（宿主 node_modules / dist 等即使没 .dockerignore 也不会影响
# 因为容器的依赖已经挪去 .container-node_modules 了）
COPY . .

# 把容器依赖目录挪回来，然后构建
# 关键：先 rm -rf /app/node_modules（宿主可能带进来的坏版本），再 mv 回来
RUN rm -rf /app/node_modules \
 && mv /app/.container-node_modules /app/node_modules 2>/dev/null \
      || (echo "容器依赖丢失，重新 npm ci" && npm ci --no-audit --no-fund) \
 && npm run build

# ---- 阶段 2：运行 ----
FROM nginx:alpine AS runtime

RUN apk add --no-cache openssl

# 把构建产物拷到 nginx 默认站点目录
COPY --from=build /app/dist /usr/share/nginx/html

# 自定义 nginx 配置（SPA fallback + /query-api /import-api 反代）
COPY nginx.conf.template /etc/nginx/nginx.conf.template

# 默认反代目标（会被运行时环境变量覆盖）
# PLATFORM_API_UPSTREAM：P0 契约网关（platform-api），/api/v1/** 原样透传
ENV QUERY_API_UPSTREAM=http://localhost:8081 \
    IMPORT_API_UPSTREAM=http://localhost:8081 \
    PLATFORM_API_UPSTREAM=http://localhost:8081 \
    TLS_CERT_PATH=/etc/nginx/tls/tls.crt \
    TLS_KEY_PATH=/etc/nginx/tls/tls.key \
    TLS_GENERATE_SELF_SIGNED=true

EXPOSE 80 443

# 容器健康检查（docker-compose / k8s 探针共用）
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1
# 把启动逻辑写成 shell 脚本 → CMD 只 exec 它
# 好处：
#   1. 避免在 CMD 的 JSON 数组里塞带换行的长命令，转义清晰
#   2. 任何 shell 语法问题在构建期就能被 RUN 捕获（脚本会被写入 /entrypoint.sh）
#   3. 后续加启动前检查（比如上游 DNS 解析预演）只要改这一个脚本
#
# 脚本做三件事：
#   a) 把 UPSTREAM 末尾的 "/" 去掉（proxy_pass 用变量时，结尾 "/" 会覆盖 rewrite 后的 URI）
#   b) 用 envsubst 把 template 里的占位符替换成运行时 env，产出 /etc/nginx/conf.d/default.conf
#   c) exec 前台 nginx（容器退出会自动停止）
RUN printf '#!/bin/sh\n\
set -eu\n\
# a) 去掉末尾的 /（只去最后一个，避免 http://host/a/b 被破坏）\n\
QUERY_API_UPSTREAM="${QUERY_API_UPSTREAM%%/}"\n\
IMPORT_API_UPSTREAM="${IMPORT_API_UPSTREAM%%/}"\n\
PLATFORM_API_UPSTREAM="${PLATFORM_API_UPSTREAM%%/}"\n\
export QUERY_API_UPSTREAM IMPORT_API_UPSTREAM PLATFORM_API_UPSTREAM\n\
# b) 仅在显式允许且证书缺失时生成开发自签证书；生产应挂载正式证书并关闭生成。\n\
if [ ! -s "${TLS_CERT_PATH}" ] || [ ! -s "${TLS_KEY_PATH}" ]; then\n\
  if [ "${TLS_GENERATE_SELF_SIGNED}" != "true" ]; then\n\
    echo "TLS certificate or private key is missing" >&2\n\
    exit 1\n\
  fi\n\
  install -d -m 700 "$(dirname "${TLS_KEY_PATH}")"\n\
  openssl req -x509 -newkey rsa:3072 -sha256 -days 30 -nodes \\\n\
    -keyout "${TLS_KEY_PATH}" -out "${TLS_CERT_PATH}" \\\n\
    -subj "/CN=${TLS_SERVER_NAME:-aipaas.local}" \\\n\
    -addext "subjectAltName=DNS:${TLS_SERVER_NAME:-aipaas.local},DNS:localhost,IP:127.0.0.1" \\\n\
    >/dev/null 2>&1\n\
  chmod 600 "${TLS_KEY_PATH}"\n\
  chmod 644 "${TLS_CERT_PATH}"\n\
fi\n\
export TLS_CERT_PATH TLS_KEY_PATH\n\
# c) envsubst 替换 template 中的占位符 → nginx 主配置\n\
envsubst '"'"'${QUERY_API_UPSTREAM} ${IMPORT_API_UPSTREAM} ${PLATFORM_API_UPSTREAM} ${TLS_CERT_PATH} ${TLS_KEY_PATH}'"'"' \\\n\
  < /etc/nginx/nginx.conf.template \\\n\
  > /etc/nginx/conf.d/default.conf\n\
# d) 前台 nginx\n\
exec nginx -g '"'"'daemon off;'"'"'\n\
' > /entrypoint.sh \
 && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
