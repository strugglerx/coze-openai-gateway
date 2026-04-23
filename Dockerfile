FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# 仅拷贝运行所需文件（不包含 .env）
COPY app.py config.py mapping.py upstream.py proxy_log.py ./

EXPOSE 38419

# 可通过 `docker run -e PORT=8080 ...` 覆盖端口
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-38419}"]
