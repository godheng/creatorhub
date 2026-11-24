"""CreatorHub ASGI 服务入口：负责中间件、生命周期、静态资源与子路由装配。"""
from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import licensing, runtime
from .api import register_routes
from .brand import PRODUCT_NAME
from .local_access import LocalAccessMiddleware
from .logging_setup import RequestLoggingMiddleware
from .services.lifecycle import lifespan

WEB_DIR = runtime.WEB_DIR


def create_app() -> FastAPI:
    """装配本地 FastAPI 应用实例（导入阶段不启动浏览器或后台协程）。"""
    application = FastAPI(title=PRODUCT_NAME, lifespan=lifespan)

    @application.exception_handler(licensing.LicenseRequired)
    async def commercial_license_error(request: Request, exc: licensing.LicenseRequired):
        return JSONResponse(
            status_code=403,
            content={"detail": "授权不可用：" + str(exc), "code": "license_required"},
        )

    @application.middleware("http")
    async def commercial_license_gate(request: Request, call_next):
        path = request.url.path
        local_action = (path.endswith(("/cancel", "/reveal", "/open", "/mark-read"))
                        or path == "/api/task-queue/batch-stop")
        remote_read = path.endswith("/xhs/favorite-boards") or path in {
            "/api/publish/published", "/api/publish/note-media", "/api/publish/note-comments", "/api/dm/stream",
        }
        protected = (path.startswith("/api/") and not path.startswith("/api/license")
                     and not local_action and (request.method not in {"GET", "HEAD", "OPTIONS"} or remote_read))
        if protected and not licensing.is_active():
            return JSONResponse(
                status_code=403,
                content={"detail": "授权未激活或已到期，请激活或续期", "code": "license_required"},
            )
        return await call_next(request)

    @application.get("/api/license")
    def commercial_license_status():
        return licensing.status()

    @application.post("/api/license/activate")
    def commercial_license_activate(payload: dict = Body(...)):
        try:
            return licensing.activate(payload.get("code", ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/license/revalidate")
    def commercial_license_revalidate():
        return licensing.revalidate()

    application.add_middleware(LocalAccessMiddleware)
    application.add_middleware(RequestLoggingMiddleware, cfg_getter=lambda: runtime.cfg)
    register_routes(application)
    application.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    return application


app = create_app()
