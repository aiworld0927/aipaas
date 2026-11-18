FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY rag_ai_api ./rag_ai_api
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/healthz')" || exit 1
CMD ["uvicorn", "rag_ai_api.main:app", "--host", "0.0.0.0", "--port", "8100"]
