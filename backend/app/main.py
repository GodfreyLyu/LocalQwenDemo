import hashlib
import json
import logging
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import (
    PASSWORDS,
    RateLimiter,
    check_origin,
    cookie_name,
    new_session,
    require_session,
    verify_password,
)
from app.config import Settings
from app.coordinator import Coordinator
from app.errors import AppError, ValidationReason
from app.model import ReviewModel, TransformersModel
from app.startup import safe_diagnostic_fields
from app.storage import Store
from app.users import DynamoUsers

SERVICE_NAME = "review-backend"
SAFE_ERROR_CODES = frozenset(
    {
        "none",
        "authentication_required",
        "authentication_unavailable",
        "csrf_rejected",
        "empty_input",
        "empty_model_response",
        "history_unavailable",
        "idempotency_conflict",
        "inference_draining",
        "inference_failed",
        "inference_stuck",
        "inference_timeout",
        "input_too_large",
        "internal_error",
        "invalid_credentials",
        "invalid_cursor",
        "invalid_model_response",
        "login_unavailable",
        "method_not_allowed",
        "model_loading",
        "not_found",
        "queue_full",
        "rate_limited",
        "request_rejected",
        "review_active",
        "review_not_found",
        "session_expired",
        "startup_or_storage_failure",
        "model_cache_incomplete",
        "storage_unavailable",
        "token_limit",
        "unsupported_media_type",
        "validation_error",
    }
)
SAFE_OUTCOMES = SAFE_ERROR_CODES | frozenset({"accepted", "completed", "rejected"})
SAFE_VALIDATION_REASONS = frozenset(reason.value for reason in ValidationReason)
SAFE_ROUTES = frozenset(
    {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/reviews",
        "/api/v1/reviews/{review_id}",
        "/health/live",
        "/health/ready",
    }
)


class SafeFormatter(logging.Formatter):
    def __init__(self, *, environment="local", release_sha=None):
        super().__init__()
        self.environment = environment
        self.release_sha = release_sha

    def format(self, record):
        payload = {
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "environment": self.environment,
            "event": record.getMessage(),
        }
        if self.release_sha:
            payload["release_sha"] = self.release_sha
        for key in (
            "request_id",
            "status",
            "review_id",
            "method",
            "route",
            "queue_depth",
            "queue_wait_ms",
            "duration_ms",
            "generated_tokens",
            "output_limit_reached",
            "output_token_limit",
            "section_generated_tokens",
            "section_prepare_ms",
            "section_generation_ms",
            "section_first_token_ms",
            "section_input_tokens",
            "worker_intraop_threads",
            "worker_interop_threads",
            "section_limits_reached",
            "section_trailing_fragments_removed",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if hasattr(record, "error_code"):
            code = record.error_code
            payload["error_code"] = code if code in SAFE_ERROR_CODES else "internal_error"
        if hasattr(record, "outcome"):
            outcome = record.outcome
            payload["outcome"] = outcome if outcome in SAFE_OUTCOMES else "inference_failed"
        if hasattr(record, "validation_reason"):
            reason = record.validation_reason
            if reason in SAFE_VALIDATION_REASONS:
                payload["validation_reason"] = reason
        payload.update(safe_diagnostic_fields(record.__dict__))
        return json.dumps(payload, separators=(",", ":"))


class RequestGuard:
    """Bound request bodies before parsing; never log request URLs or payloads."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started_at = monotonic()
        started = False

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] += [
                    (b"x-request-id", request_id.encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
                route_object = scope.get("route")
                route = getattr(route_object, "path", None)
                route = route if route in SAFE_ROUTES else None
                status = message["status"]
                if not (route in {"/health/live", "/health/ready"} and status < 400):
                    error_code = scope["state"].get("error_code")
                    if error_code not in SAFE_ERROR_CODES:
                        error_code = {
                            404: "not_found",
                            405: "method_not_allowed",
                        }.get(status, "request_rejected" if status >= 400 else "none")
                    allowed_methods = {
                        "GET",
                        "HEAD",
                        "POST",
                        "PUT",
                        "PATCH",
                        "DELETE",
                        "OPTIONS",
                    }
                    fields = {
                        "request_id": request_id,
                        "status": status,
                        "method": scope["method"]
                        if scope["method"] in allowed_methods
                        else "OTHER",
                        "duration_ms": max(0, round((monotonic() - started_at) * 1000)),
                        "error_code": error_code,
                    }
                    if route is not None:
                        fields["route"] = route
                    logging.getLogger("review").info("http_request", extra=fields)
            await send(message)

        headers = dict(scope["headers"])
        body = bytearray()
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            if headers.get(b"content-type", b"").split(b";")[0].strip() != b"application/json":
                scope["state"]["error_code"] = "unsupported_media_type"
                response = JSONResponse(
                    status_code=415,
                    content={
                        "error": {
                            "code": "unsupported_media_type",
                            "message": "Use application/json.",
                            "request_id": request_id,
                        }
                    },
                )
                return await response(scope, receive, safe_send)
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 131072:
                    scope["state"]["error_code"] = "input_too_large"
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "input_too_large",
                                "message": "The request is too large.",
                                "request_id": request_id,
                            }
                        },
                    )
                    return await response(scope, receive, safe_send)
                if not message.get("more_body", False):
                    break
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed and scope["method"] in {"POST", "PUT", "PATCH"}:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        try:
            await self.app(scope, replay, safe_send)
        except Exception:
            # Do not let transport logging expose exception strings containing user input.
            logging.getLogger("review").error(
                "request_failed",
                extra={"request_id": request_id, "error_code": "internal_error"},
            )
            if not started:
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "internal_error",
                            "message": "The request could not be completed.",
                            "request_id": request_id,
                        }
                    },
                )
                await response(scope, replay, safe_send)
            else:
                await send({"type": "http.response.body", "body": b"", "more_body": False})


class Credentials(BaseModel):
    login_id: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("login_id", mode="before")
    @classmethod
    def normalize(cls, value):
        if not isinstance(value, str):
            raise ValueError("Login identifier must be text.")
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._@+\-]{2,99}", value):
            raise ValueError("Use 3–100 letters, digits, dots, underscores, @, + or hyphens.")
        return value


class ReviewInput(BaseModel):
    source_code: str = Field(min_length=1, max_length=32000)
    language: str = Field(
        default="auto", min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_+#. \-]+$"
    )
    client_request_id: UUID | None = None


Session = Annotated[dict, Depends(require_session)]


def create_app(
    settings: Settings | None = None, *, model: ReviewModel | None = None, users=None
) -> FastAPI:
    config = settings or Settings()
    handler = logging.StreamHandler()
    handler.setFormatter(
        SafeFormatter(environment=config.environment, release_sha=config.release_sha)
    )
    logger = logging.getLogger("review")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    @asynccontextmanager
    async def lifespan(application):
        coordinator.start()
        yield
        await coordinator.close()

    app = FastAPI(
        title="Local Code Review API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = config
    app.state.store = store = Store(config.data_dir)
    app.state.users = user_store = users or DynamoUsers(config)
    app.state.model = review_model = model or TransformersModel(config)
    app.state.coordinator = coordinator = Coordinator(store, user_store, review_model, config)
    app.state.limiter = RateLimiter()
    app.add_middleware(RequestGuard)

    def error_response(request, code, message, status):
        request.state.error_code = code if code in SAFE_ERROR_CODES else "internal_error"
        return JSONResponse(
            status_code=status,
            content={
                "error": {"code": code, "message": message, "request_id": request.state.request_id}
            },
            headers={"Retry-After": "60"} if status == 429 else {},
        )

    @app.exception_handler(AppError)
    async def application_error(request, exc):
        return error_response(request, exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors include input values; never return those to clients or logs.
        return error_response(
            request,
            "validation_error",
            "Invalid input. Check the identifier, password, code, or review ID.",
            422,
        )

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return error_response(
            request,
            "history_unavailable",
            "Review history could not be loaded or saved. Please retry.",
            503,
        )

    @app.exception_handler(BotoCoreError)
    @app.exception_handler(ClientError)
    async def users_error(request, exc):
        return error_response(
            request,
            "authentication_unavailable",
            "The account service is unavailable. Please retry.",
            503,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error(
            "request_failed",
            extra={"request_id": request.state.request_id, "error_code": "internal_error"},
        )
        return error_response(request, "internal_error", "The request could not be completed.", 500)

    def auth_limit(request, login_id):
        # Only the direct peer is trusted; caller-supplied forwarding headers grant no new quota.
        client = request.client.host if request.client else "unknown"
        limiter = request.app.state.limiter
        limiter.check("auth-ip:" + client, config.login_rate_limit)
        limiter.check(
            "auth-login:" + hashlib.sha256(login_id.encode()).hexdigest(), config.login_rate_limit
        )

    @app.post("/api/v1/auth/register", status_code=201)
    def register(payload: Credentials, request: Request, response: Response):
        check_origin(request)
        auth_limit(request, payload.login_id)
        user = user_store.create(payload.login_id, PASSWORDS.hash(payload.password))
        return new_session(request, response, user)

    @app.post("/api/v1/auth/login")
    def login(payload: Credentials, request: Request, response: Response):
        check_origin(request)
        auth_limit(request, payload.login_id)
        user = user_store.get(payload.login_id)
        verify_password(user, payload.password)
        return new_session(request, response, user)

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, session: Session):
        store.session_delete(request.cookies[cookie_name(config)])
        response.delete_cookie(
            cookie_name(config),
            path="/",
            secure=config.cookie_secure,
            httponly=True,
            samesite="strict",
        )

    @app.get("/api/v1/auth/me")
    def me(session: Session):
        return {key: session[key] for key in ("login_id", "csrf_token", "expires_at")} | {
            "source_max_chars": config.source_max_chars
        }

    @app.post("/api/v1/reviews", status_code=202)
    def submit(payload: ReviewInput, request: Request, session: Session):
        if not payload.source_code.strip():
            raise AppError("empty_input", "Enter source code before running a review.", 422)
        if len(payload.source_code) > config.source_max_chars:
            raise AppError(
                "input_too_large", f"Use at most {config.source_max_chars} characters.", 422
            )
        if not coordinator.ready:
            raise AppError(coordinator.state, "The model is not ready. Please retry shortly.", 503)
        if (
            review_model.count_tokens(payload.source_code, payload.language)
            > config.model_max_input_tokens
        ):
            raise AppError(
                "token_limit", "Source exceeds the model token limit. Try a smaller section.", 422
            )
        request.app.state.limiter.check(
            "submit:" + session["user_id"], config.submission_rate_limit
        )
        try:
            job = store.create_review(
                session["user_id"],
                str(payload.client_request_id or uuid4()),
                payload.language,
                payload.source_code,
                config.queue_capacity,
            )
        except AppError as exc:
            if exc.code == "queue_full":
                rejection_fields = {
                    "outcome": "rejected",
                    "error_code": "queue_full",
                }
                try:
                    rejection_fields["queue_depth"] = store.queue_depth()
                except sqlite3.Error:
                    # Preserve the original queue-full response if the diagnostic count fails.
                    pass
                logger.warning(
                    "queue_rejected",
                    extra=rejection_fields,
                )
            raise
        if job.pop("_created", False):
            queue_depth = job.pop("_queue_depth")
            logger.info(
                "review_submitted",
                extra={
                    "review_id": job["review_id"],
                    "outcome": "accepted",
                    "error_code": "none",
                    "queue_depth": queue_depth,
                },
            )
        return {
            "review_id": job["review_id"],
            "status": job["status"],
            "client_request_id": job["client_request_id"],
        }

    @app.get("/api/v1/reviews")
    def history(
        session: Session, limit: Annotated[int, Query(ge=1, le=50)] = 20, before: UUID | None = None
    ):
        return store.history(session["user_id"], limit, str(before) if before else None)

    @app.get("/api/v1/reviews/{review_id}")
    def detail(review_id: UUID, session: Session):
        row = store.get_review(session["user_id"], str(review_id))
        row.pop("user_id")
        return row

    @app.get("/health/live")
    def live(request: Request, response: Response):
        response.status_code = 200 if coordinator.live else 503
        if not coordinator.live:
            request.state.error_code = (
                coordinator.state
                if coordinator.state in SAFE_ERROR_CODES
                else "startup_or_storage_failure"
            )
        return {"status": "alive" if coordinator.live else coordinator.state}

    @app.get("/health/ready")
    def ready(request: Request, response: Response):
        if not coordinator.ready:
            response.status_code = 503
            request.state.error_code = (
                coordinator.state if coordinator.state in SAFE_ERROR_CODES else "request_rejected"
            )
            return {"status": coordinator.state}
        try:
            store.ping()
        except sqlite3.Error:
            response.status_code = 503
            request.state.error_code = "storage_unavailable"
            return {"status": "storage_unavailable"}
        return {"status": "ready"}

    return app
