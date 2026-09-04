FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend:/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl fonts-dejavu-core tar gzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY project.tar.gz /tmp/project.tar.gz
RUN tar -xzf /tmp/project.tar.gz -C /app && rm /tmp/project.tar.gz
RUN pip install --upgrade pip && pip install -r /app/backend/requirements.txt

EXPOSE 8000
CMD ["sh","-c","python /app/scripts/apply_migrations.py --sql-dir /app/backend/sql && uvicorn app.main:app --app-dir /app/backend --host 0.0.0.0 --port ${PORT:-8000}"]
