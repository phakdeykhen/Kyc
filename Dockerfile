FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
# Tesseract 5 with Khmer, Latin and the Khmer script model used for constrained digit re-reads.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-khm tesseract-ocr-eng tesseract-ocr-script-khmr \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --create-home --uid 10001 kyc \
    && install -d -o kyc -g kyc -m 0700 /var/lib/kyc/captures
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./
USER kyc
EXPOSE 8080
# Phase 18: uvicorn starts WEB_CONCURRENCY worker processes (about one per vCPU). Each worker has
# its own DB pool (DB_POOL_SIZE + DB_MAX_OVERFLOW) and admission limit; keep
# instances x workers x pool within the database's max_connections.
ENV WEB_CONCURRENCY=1
CMD ["sh", "-c", "exec uvicorn kyc.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
