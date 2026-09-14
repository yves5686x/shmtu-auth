# OCR 依赖 onnxruntime，使用 glibc 基础镜像。
FROM python:3.12-slim

LABEL maintainer="Haomin Kong" \
      description="SHMTU Campus Network Auto Auth"

ENV DOCKER_MODE="1" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src" \
    TZ="Asia/Shanghai"

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata libgomp1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY src/shmtu_auth/ /app/src/shmtu_auth/
RUN rm -f /app/src/shmtu_auth/config.toml /app/src/shmtu_auth/config/config.toml \
    && mkdir -p /app/logs /app/data

# 直接启动主包，不加载含示例默认值的 shell 配置。
ENTRYPOINT ["python", "-m", "shmtu_auth"]
