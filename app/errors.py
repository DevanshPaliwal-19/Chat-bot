"""Application errors. Messages here are safe to show to website visitors."""
from __future__ import annotations


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    default_message = "Something went wrong on our side. Please try again."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.headers = headers or {}
        super().__init__(self.message)


class BadRequest(AppError):
    status_code, code, default_message = 400, "bad_request", "The request could not be understood."


class Forbidden(AppError):
    status_code, code, default_message = 403, "forbidden", "You are not allowed to do that."


class NotFound(AppError):
    status_code, code, default_message = 404, "not_found", "That resource was not found."


class PayloadTooLarge(AppError):
    status_code, code, default_message = 413, "payload_too_large", "The request is too large."


class UnsupportedMedia(AppError):
    status_code, code, default_message = 415, "unsupported_media", "That file type is not supported."


class ValidationFailed(AppError):
    status_code, code, default_message = 422, "validation_failed", "Some of the input was not valid."


class RateLimited(AppError):
    status_code, code, default_message = 429, "rate_limited", "Too many requests. Please slow down."


class ServiceUnavailable(AppError):
    status_code, code, default_message = 503, "service_unavailable", "This service is temporarily unavailable."


class ServiceBusy(AppError):
    status_code, code, default_message = 503, "busy", "The assistant is busy right now. Please try again in a moment."


class GatewayTimeout(AppError):
    status_code, code, default_message = 504, "request_timeout", "The assistant took too long to respond."
