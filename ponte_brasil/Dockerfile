# Ponte no Brasil (Cloud Run, southamerica-east1). Só biblioteca padrão: nada de pip install.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin ponte
WORKDIR /app
COPY app.py /app/app.py

USER 10001
# O Cloud Run informa a porta em $PORT (8080 por padrão); app.py lê a variável.
CMD ["python", "/app/app.py"]
