FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace/runbook/backend
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV ORGMEMORY_EMBEDDING_CACHE_DIR=/opt/orgmemory-models
RUN python -c "from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/orgmemory-models'); TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2', cache_dir='/opt/orgmemory-models')"
COPY . .
RUN mkdir -p /workspace/runbook/data /workspace/runbook/generated_runbooks
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
