FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Non-root runtime user with a writable data directory for SQLite.
RUN groupadd -r sentinel && useradd -r -g sentinel sentinel \
    && mkdir -p /app/data && chown sentinel:sentinel /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

USER sentinel

VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# Bind 0.0.0.0 INSIDE the container so published ports work.
# Host exposure stays localhost-only via docker-compose port mapping.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
