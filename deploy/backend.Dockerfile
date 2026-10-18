# 后端镜像：Python 3.12 + 量化引擎 + FastAPI。
# 国内拉取基础镜像走 daocloud 镜像站；如网络允许也可换回官方镜像名。

FROM docker.m.daocloud.io/library/python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    QUANT_ENV=production \
    QUANT_DATA_DIR=/app/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 先只复制依赖清单，充分利用构建缓存
COPY backend/pyproject.toml /app/backend/pyproject.toml

COPY backend /app/backend

# 旧版 CSV 缓存作为离线行情种子（容器首次启动即可回测）
COPY data/cache /app/data/cache

RUN pip install --no-cache-dir "/app/backend[postgres]"

# 非 root 运行；数据目录归 quant 所有，命名卷首次挂载会继承该权限
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin quant \
    && mkdir -p /app/data \
    && chown -R quant:quant /app

USER quant

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["quant", "serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
