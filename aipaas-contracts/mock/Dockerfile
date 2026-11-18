# ===========================================================================
# AIPaaS P0 契约 Mock Server 镜像（零第三方依赖，仅 Python 标准库）
# ---------------------------------------------------------------------------
# 构建：docker build -t aipaas/contracts-mock:latest aipaas-contracts/mock
# 运行：docker run -d -p 4010:4010 aipaas/contracts-mock:latest
# ===========================================================================
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY server.py smoke_upload_qa.py ./

EXPOSE 4010

HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=10 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:4010/healthz')" || exit 1

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "4010"]
