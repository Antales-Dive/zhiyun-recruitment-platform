FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app

# 非 root 运行（TASK-013）
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --no-create-home app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Alembic 配置与迁移封装进 API 镜像；服务器不接收源码树（06 §7.1）
COPY alembic.ini .
COPY alembic ./alembic

RUN chown -R app:app /app && mkdir -p /data/uploads && chown -R app:app /data
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
