# 前端镜像：Node 构建静态资源，Nginx 提供 SPA 与 API/WS 反代。

# 基础镜像默认走国内加速站；CI（海外 runner）用 --build-arg 换官方源
ARG NODE_IMAGE=docker.1ms.run/library/node:20-alpine
ARG NGINX_IMAGE=docker.1ms.run/library/nginx:1.27-alpine
ARG NPM_REGISTRY=https://registry.npmmirror.com

FROM ${NODE_IMAGE} AS build

WORKDIR /web

RUN npm config set registry ${NPM_REGISTRY}

# 依赖锁定：package-lock.json 由 npm 生成，npm ci 保证每次构建版本一致
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

FROM ${NGINX_IMAGE} AS runtime

RUN rm -f /etc/nginx/conf.d/default.conf
COPY deploy/nginx.conf /etc/nginx/conf.d/quant.conf
COPY --from=build /web/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
