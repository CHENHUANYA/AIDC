FROM python:3.11-slim@sha256:be1575ed968de893bd54f4c56315ff7c4736ce522c1bca08fd521731aafc0d76

WORKDIR /app

ARG RAG_PRELOAD_MODELS=0
ARG TORCH_VERSION=2.5.1+cpu
ARG PYTORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV HF_HOME=/app/hf_cache
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV RAG_HF_LOCAL_ONLY=true

COPY requirements.txt requirements-postgresql.txt ./
RUN python -m pip --isolated install --default-timeout=1000 --no-cache-dir \
      --index-url ${PYTORCH_CPU_INDEX_URL} torch==${TORCH_VERSION}
RUN python -m pip --isolated install --default-timeout=1000 --no-cache-dir -r requirements-postgresql.txt

RUN groupadd --system --gid 10001 alarm-rag \
    && useradd --system --uid 10001 --gid alarm-rag --home-dir /app alarm-rag

COPY --chown=alarm-rag:alarm-rag . .

RUN if [ "$RAG_PRELOAD_MODELS" = "1" ]; then \
      python scripts/model_cache.py --hf-home /app/hf_cache --online preload ; \
    fi

RUN chown -R alarm-rag:alarm-rag /app
USER alarm-rag

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
