FROM python:3.11-slim

WORKDIR /app

ARG RAG_PRELOAD_MODELS=0

ENV HF_HOME=/app/hf_cache
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV RAG_HF_LOCAL_ONLY=true

COPY requirements.txt .
RUN python -m pip --isolated install --default-timeout=1000 --no-cache-dir -r requirements.txt

COPY . .

RUN if [ "$RAG_PRELOAD_MODELS" = "1" ]; then \
      python scripts/model_cache.py --hf-home /app/hf_cache --online preload ; \
    fi

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
