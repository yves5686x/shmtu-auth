# 基座必须是 Debian slim，不能用 alpine。
# 门户已强制要求图形验证码，无头环境没有弹窗兜底，只能靠 OCR
# （ddddocr → onnxruntime + opencv），而 onnxruntime 官方只提供
# manylinux(glibc) wheel，alpine 的 musl 上装不了。
FROM python:3.12-slim

LABEL maintainer="Haomin Kong" \
      author="Haomin Kong" \
      version="3" \
      description="SHMTU Campus Network Auto Auth - Full"

ENV DOCKER_MODE="Haomin Kong" \
    BASE_PATH="/usr/local/shmtu/shmtu-auth/shmtu_auth" \
    LOGS_PATH="/usr/local/shmtu/shmtu-auth/shmtu_auth/logs"

WORKDIR "$BASE_PATH"

# Copy source code
COPY src/shmtu_auth/ "$BASE_PATH/"
COPY requirements.txt $BASE_PATH/requirements.txt

# Setup
RUN sed -i 's/\r$//' $BASE_PATH/*.sh \
    && sed -i 's/\r$//' $BASE_PATH/scripts/*.sh \
    && sed -i 's/\r$//' $BASE_PATH/config/*.sh \
    && rm -f $BASE_PATH/config.toml \
    && rm -f $BASE_PATH/config/config.toml \
    && DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bash tzdata libgomp1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 --no-cache-dir install -r $BASE_PATH/requirements.txt \
    && pip3 uninstall setuptools -y \
    && pip3 uninstall pip -y \
    && rm -f $BASE_PATH/requirements.txt \
    && rm -rf ~/.cache/pip /tmp/* \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && mkdir -p $BASE_PATH/logs \
    && chmod +x "$BASE_PATH/start.sh"

ENTRYPOINT ["/usr/local/shmtu/shmtu-auth/shmtu_auth/start.sh"]
