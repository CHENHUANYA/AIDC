FROM python:3.11-slim

WORKDIR /app

ENV HF_HOME=/app/hf_cache
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV RAG_HF_LOCAL_ONLY=true

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN python -m pip --isolated install --default-timeout=1000 --no-cache-dir -r requirements.txt

# Pre-download embedding + reranker models at build time
# This means startup is fast — models are baked into the image
RUN HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 RAG_HF_LOCAL_ONLY=false python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
print('Downloading embedder...'); \
SentenceTransformer('mixedbread-ai/mxbai-embed-large-v1', cache_folder='/app/hf_cache'); \
print('Downloading reranker...'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2'); \
print('Done.')"

# Copy application code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
