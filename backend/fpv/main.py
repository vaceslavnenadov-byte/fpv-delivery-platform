"""Фабрика приложения FastAPI."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__, db, runtime
from .config import Settings, load_settings
from .errors import AppError
from .services import directory as directory_mod
from .services import storage

log = logging.getLogger("fpv")


def create_app(settings: Optional[Settings] = None, *, init_db: bool = True,
               directory: Optional[directory_mod.DirectoryProvider] = None,
               store: Optional[storage.ObjectStore] = None) -> FastAPI:
    settings = settings or load_settings()
    settings.validate()
    runtime.runtime.settings = settings
    runtime.runtime.directory = directory or directory_mod.build_provider(settings)
    runtime.runtime.store = store or storage.build_store(settings)
    if init_db:
        db.init_engine(settings.database_url)

    # Регистрация обработчиков outbox (импорт модулей с декораторами @events.handler).
    from .services import notifications, video  # noqa: F401
    from .api import admin, auth, deps, drone_link, drones, fleets, maps, misc, orders, realtime

    deps.init_limiters(settings.login_rate_per_min, settings.mutation_rate_per_min)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if init_db:
            realtime.hub.start(settings.database_url)
        if settings.run_background_in_app:
            import asyncio
            from .worker import run_loop_async
            asyncio.get_event_loop().create_task(run_loop_async())
        yield
        realtime.hub.stop()

    app = FastAPI(title="FPV Delivery Platform API", version=__version__, lifespan=lifespan,
                  docs_url=None if settings.is_production else "/api/docs",
                  redoc_url=None, openapi_url=None if settings.is_production else "/api/openapi.json")

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        errors = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_input", "message": "Некорректные входные данные", "details": {"errors": errors}}})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    for module in (auth, orders, drones, admin, drone_link, misc, realtime, maps, fleets):
        app.include_router(module.router)

    return app


def app_from_env() -> FastAPI:   # для uvicorn --factory
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
