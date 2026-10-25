FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --create-home appuser
USER appuser

# Cloud Run sets PORT (8080 by default).
CMD exec gunicorn --bind ":${PORT:-8080}" --workers 1 --threads 8 --timeout 120 app:app
