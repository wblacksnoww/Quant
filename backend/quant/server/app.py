"""FastAPI 应用工厂。

中间件顺序（外 → 内）::

    RequestID → SecurityHeaders → RateLimit → CORS → 路由

生产部署时 TLS 由反向代理（Caddy/Nginx）终结，本应用始终以 HTTP 提供服务，
因此不需要自己处理证书；``X-Forwarded-*`` 头由代理注入，用于取真实客户端 IP。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__
from ..config import Settings, get_settings
from ..core.errors import QuantError
from ..core.types import utcnow
from .context import AppContext, build_context
from .routers import (
    admin,
    auth,
    backtest,
    credentials,
    funds,
    live,
    market,
    news,
    paper,
    risk,
    strategies,
    system,
)
from .schemas import ErrorBody
from .ws import EventHub

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 限流
# --------------------------------------------------------------------------- #


class RateLimiter:
    """进程内滑窗限流（每 IP 每分钟）。

    单实例部署足够；多副本时应当把计数放进 Redis，这里留了清晰的替换点：
    :meth:`allow` 是唯一的判断入口。
    """

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = max(1, limit)
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        if len(self._hits) > 10_000:  # 防止被随机 IP 打爆内存
            self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        stale = [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]
        for key in stale:
            self._hits.pop(key, None)


# --------------------------------------------------------------------------- #
# 中间件
# --------------------------------------------------------------------------- #


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, enable_hsts: bool = False) -> None:
        super().__init__(app)
        self.enable_hsts = enable_hsts

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("X-XSS-Protection", "0")  # 现代浏览器应依赖 CSP
        headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; "
                "img-src 'self' data: blob:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; "
                "connect-src 'self' ws: wss:; "
                "font-src 'self' data:; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'",
            )
        if self.enable_hsts:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


# --------------------------------------------------------------------------- #
# 应用
# --------------------------------------------------------------------------- #


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ctx = ctx or build_context(settings)
        app.state.ctx.hub.bind(asyncio.get_running_loop())
        if app.state.ctx.coordinator is not None:
            app.state.ctx.coordinator.start()
        if app.state.ctx.news_ingestion is not None:
            app.state.ctx.news_ingestion.start()
        log.info(
            "服务已启动 env=%s version=%s provider=%s",
            settings.env,
            __version__,
            app.state.ctx.provider_name,
        )
        try:
            yield
        finally:
            log.info("正在关闭服务...")
            try:
                if app.state.ctx.coordinator is not None:
                    app.state.ctx.coordinator.stop()
            except Exception:  # noqa: BLE001
                log.exception("停止会话协调器失败")
            try:
                if app.state.ctx.news_ingestion is not None:
                    app.state.ctx.news_ingestion.stop()
            except Exception:  # noqa: BLE001
                log.exception("停止新闻抓取失败")
            try:
                app.state.ctx.paper.stop_all(timeout=5)
            except Exception:  # noqa: BLE001
                log.exception("停止纸盘失败")
            try:
                app.state.ctx.live.stop_all(timeout=10)
            except Exception:  # noqa: BLE001
                log.exception("停止实盘失败")
            try:
                if app.state.ctx.backtests is not None:
                    app.state.ctx.backtests.shutdown(timeout=5)
            except Exception:  # noqa: BLE001
                log.exception("停止回测失败")
            app.state.ctx.db.dispose()
            log.info("服务已关闭")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "A 股量化交易系统：回测 / 纸盘 / 风控 / 加密。\n\n"
            "**认证**：`POST /api/auth/login` 获取 access token，"
            "之后在 `Authorization: Bearer <token>` 中携带。"
        ),
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.limiter = limiter

    # -- 中间件（后添加的更靠外） ------------------------------------------ #
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    class RateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith(
                settings.api_prefix
            ):
                ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
                    request.client.host if request.client else "unknown"
                )
                if not limiter.allow(ip):
                    return JSONResponse(
                        status_code=429,
                        content={"code": "rate_limited", "message": "请求过于频繁，请稍后再试", "detail": {}},
                        headers={"Retry-After": "60"},
                    )
            return await call_next(request)

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=settings.is_production)
    app.add_middleware(RequestContextMiddleware)

    # -- 异常处理 ---------------------------------------------------------- #
    @app.exception_handler(QuantError)
    async def quant_error_handler(request: Request, exc: QuantError):
        payload = ErrorBody(
            code=exc.code,
            message=exc.message,
            detail=exc.context,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(status_code=exc.http_status, content=payload.model_dump())

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):  # pragma: no cover
        log.exception("未处理异常 path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "服务器内部错误" if settings.is_production else f"{type(exc).__name__}: {exc}",
                "detail": {},
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    # -- 路由 -------------------------------------------------------------- #
    prefix = settings.api_prefix
    for module in (
        auth,
        market,
        strategies,
        backtest,
        paper,
        live,
        news,
        risk,
        funds,
        credentials,
        system,
        admin,
    ):
        app.include_router(module.router, prefix=prefix)

    # -- WebSocket --------------------------------------------------------- #
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(default=None)):
        ctx_: AppContext = websocket.app.state.ctx
        try:
            claims = ctx_.token_service.decode(token or "", expected_type="access")
        except Exception:  # noqa: BLE001
            await websocket.close(code=4401, reason="Unauthorized")
            return

        await websocket.accept()
        user_id = claims.user_id
        queue = ctx_.hub.subscribe(replay=30, user_id=user_id)
        await websocket.send_json(
            {
                "type": "hello",
                "payload": {
                    "user": claims.sub,
                    "role": claims.role,
                    "server_time": utcnow().isoformat(),
                    "paper_running": ctx_.paper.running(user_id),
                },
            }
        )

        async def drain() -> None:
            """消费客户端消息（忽略内容），用于感知断开。"""
            try:
                while True:
                    await websocket.receive_text()
            except Exception:  # noqa: BLE001
                return

        reader = asyncio.create_task(drain())
        try:
            async for event in EventHub.stream(queue):
                await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:  # noqa: BLE001
            log.debug("WebSocket 发送异常", exc_info=True)
        finally:
            reader.cancel()
            ctx_.hub.unsubscribe(queue)

    # -- 健康/根路径 ------------------------------------------------------- #
    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        ctx_: AppContext = app.state.ctx
        return {"status": "ok", "version": __version__, "db": ctx_.db.healthcheck()}

    # -- 前端静态资源 ------------------------------------------------------ #
    frontend = settings.frontend_path
    index_file = frontend / "index.html"
    if index_file.exists():
        assets = frontend / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith(("api/", "ws", "docs", "redoc", "openapi.json", "healthz")):
                return JSONResponse(status_code=404, content={"code": "not_found", "message": "接口不存在"})
            candidate = (frontend / full_path).resolve()
            if full_path and candidate.is_file() and frontend in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index_file)

    else:

        @app.get("/", include_in_schema=False)
        async def root():
            return {
                "name": settings.app_name,
                "version": __version__,
                "message": "前端尚未构建。开发请运行 `npm run dev`（frontend 目录），"
                "或使用 docker compose 构建镜像。",
                "docs": "/docs" if not settings.is_production else None,
                "api": settings.api_prefix,
            }

    return app


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    cfg = get_settings()
    uvicorn.run("quant.server.app:create_app", factory=True, host=cfg.host, port=cfg.port, reload=cfg.debug)
