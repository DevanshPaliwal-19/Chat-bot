from __future__ import annotations
import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from . import __version__
from .api.routes import root, v1
from .api.ws import voice_router
from .config import Settings, get_settings
from .container import Container, build_container
from .errors import AppError
from .logging_setup import configure_logging, log_event, request_id_var

logger = logging.getLogger("app")


def _error(status: int, code: str, message: str, request_id: str, headers: dict[str, str] | None = None, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": request_id}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers={**(headers or {}), "X-Request-ID": request_id})


def create_app(settings: Settings | None = None, container: Container | None = None, **overrides: Any) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or await asyncio.to_thread(build_container, settings, **overrides)
        app.state.container = c
        try:
            purged = await asyncio.to_thread(c.lead_store.purge_older_than, settings.lead_retention_days)
            if purged:
                log_event(logger, logging.INFO, "leads_purged", count=purged)
        except Exception as exc:
            log_event(logger, logging.ERROR, "lead_purge_failed", error=type(exc).__name__)
        if not c.retriever.ready:
            log_event(logger, logging.WARNING, "knowledge_base_not_loaded",
                      hint="run: python -m app.rag.cli ingest")
        if settings.llm_warmup:
            asyncio.create_task(c.llm.warmup())

        async def janitor() -> None:
            while True:
                await asyncio.sleep(60)
                c.sessions.purge_expired()

        task = asyncio.create_task(janitor())
        log_event(logger, logging.INFO, "startup_complete", model=settings.llm_model, provider=settings.llm_provider,
                  embedder=c.embedder.name, kb_ready=c.retriever.ready)
        try:
            yield
        finally:
            task.cancel()
            await c.engine.drain()
            await c.llm.aclose()
            close = getattr(c.lead_store, "close", None)
            if close:
                close()
            close_sess = getattr(c.sessions, "close", None)
            if close_sess:
                close_sess()

    app = FastAPI(
        title=f"{settings.company_name} AI Assistant API", version=__version__, lifespan=lifespan,
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None, openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Session-ID", "X-Admin-Key", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = uuid.uuid4().hex[:12]
        request.state.request_id = rid
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            is_upload = request.url.path.startswith("/api/v1/audio/")
            limit = settings.max_audio_bytes + 64 * 1024 if is_upload else settings.max_request_bytes
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > limit:
                return _error(413, "payload_too_large", "The request is too large.", rid)
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            return response
        finally:
            log_event(logger, logging.INFO, "request", method=request.method, path=request.url.path,
                      ms=round((time.perf_counter() - started) * 1000, 1))
            request_id_var.reset(token)

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        level = logging.ERROR if exc.status_code >= 500 else logging.INFO
        log_event(logger, level, "app_error", code=exc.code, status=exc.status_code, path=request.url.path)
        return _error(exc.status_code, exc.code, exc.message, request.state.request_id, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "problem": e["type"]} for e in exc.errors()]
        return _error(422, "validation_failed", "The request was not valid.", request.state.request_id, details=details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        msg = "That resource was not found." if exc.status_code == 404 else "The request could not be completed."
        return _error(exc.status_code, code, msg, getattr(request.state, "request_id", "-"))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        logger.exception("unhandled error")
        return _error(500, "internal_error", "Something went wrong on our side. Please try again.",
                      getattr(request.state, "request_id", "-"))

    app.include_router(root)
    app.include_router(v1)
    app.include_router(voice_router)   # M2 — continuous voice WebSocket
    return app 

#   app.include_router(audio)
#   app.include_router(channels)
#   app.include_router(contacts)
#   app.include_router(leads)
#   app.include_router(messages)
#   app.include_router(sessions)
#   app.include_router(sync)


_default: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Lazy `app` so importing this module has no side effects; `uvicorn app.main:app` still works."""
    global _default
    if name == "app":
        if _default is None:
            _default = create_app()
        return _default
    raise AttributeError(name)
