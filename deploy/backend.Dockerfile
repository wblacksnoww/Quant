# 后端镜像：Python 3.12 + 量化引擎 + FastAPI。
# 国内拉取基础镜像走 daocloud 镜像站；如网络允许也可换回官方镜像名。

ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
# 构建层：需要 g++ 编译 quant._core（C++ 回测加速核心，pybind11）
ARG BUILDER_IMAGE=docker.m.daocloud.io/library/python:3.12

FROM ${BUILDER_IMAGE} AS builder

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app
COPY deploy/requirements.lock.txt /app/requirements.lock.txt
# setuptools 为 --no-build-isolation 构建所需；pybind11（lock 内）编译扩展所需
RUN pip install --no-cache-dir -r /app/requirements.lock.txt setuptools

COPY backend /app/backend
RUN pip install --no-cache-dir --no-deps --no-build-isolation /app/backend \
    && python -c "from quant import _core"  # 构建产物自检

# C++ 交易网关二进制（backend 以子进程启动）
COPY gateway /app/gateway
RUN rm -rf /app/gateway/build && apt-get update \
    && apt-get install -y --no-install-recommends cmake libboost-dev libssl-dev \
    && cmake -S /app/gateway -B /app/gateway/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /app/gateway/build -j \
    && cp /app/gateway/build/quant-gateway /usr/local/bin/

FROM ${PYTHON_IMAGE} AS runtime

# 默认走国内 PyPI 镜像（CI/海外构建用 --build-arg 换成官方源）
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    QUANT_ENV=production \
    QUANT_DATA_DIR=/app/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 先装锁定依赖：源码变动不会让这一层缓存失效（改码重建从分钟级降到秒级）
COPY deploy/requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -r /app/requirements.lock.txt

# C++ 网关二进制（builder 层编译产物）
COPY --from=builder /usr/local/bin/quant-gateway /usr/local/bin/quant-gateway

# 旧版 CSV 缓存作为离线行情种子（容器首次启动即可回测）
COPY data/cache /app/data/cache

# 应用源码
COPY backend /app/backend

# C++ 原生扩展：builder 里编译好的 _core*.so 放进源码包
COPY --from=builder /usr/local/lib/python3.12/site-packages/quant/_core*.so /app/backend/quant/

# 启动自检：原生扩展 + CLI 模块入口（CMD 用的是 python -m quant.cli）
RUN cd /app/backend \
    && python -c "from quant import core_native; assert core_native.HAS_NATIVE, core_native.NATIVE_ERROR" \
    && python -c "import quant.cli" \
    && quant-gateway 2>&1 | head -1

# 非 root 运行；数据目录归 quant 所有，命名卷首次挂载会继承该权限
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin quant \
    && mkdir -p /app/data \
    && chown -R quant:quant /app

USER quant

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# 用模块入口而不是 quant 控制台脚本：运行时阶段只拷源码树，未执行 pip install
CMD ["python", "-m", "quant.cli", "serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
