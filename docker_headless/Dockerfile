# 多阶段构建 - 最终镜像更小
#
# 注意：基座是 Debian slim 而不是 alpine。门户现已强制要求图形验证码，
# 无头环境没有弹窗兜底，必须依赖 OCR（ddddocr → onnxruntime + opencv）。
# onnxruntime 官方只提供 manylinux(glibc) wheel，alpine 的 musl 上装不了。
FROM python:3.12-slim AS builder

WORKDIR /build

# onnxruntime 需要 OpenMP 运行时，opencv-python-headless 需要 glib
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 安装依赖到虚拟环境
COPY docker_headless/requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# 最终镜像
FROM python:3.12-slim

LABEL maintainer="Haomin Kong" \
      description="SHMTU Campus Network Auto Auth - Headless" \
      version="2.1"

# 运行时库（onnxruntime / opencv 的共享库依赖）
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 tzdata \
    && rm -rf /var/lib/apt/lists/*

# 环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# 复制虚拟环境和应用
COPY --from=builder /opt/venv /opt/venv
COPY docker_headless/app /app/app

CMD ["python", "-m", "app.main"]
