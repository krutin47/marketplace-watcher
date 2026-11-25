# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=pip_ca \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 watcher \
    && useradd --uid 10001 --gid 10001 --no-create-home watcher \
    && mkdir /data && chown watcher:watcher /data
COPY watcher watcher
COPY web web
RUN chmod -R a+rX watcher web
USER watcher
ENV WATCHER_DB=/data/watcher.sqlite3
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "watcher.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
