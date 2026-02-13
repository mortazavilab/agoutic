"""
Request-logging / tracing middleware for AGOUTIC FastAPI servers.

Adds a unique ``request_id`` (UUID4) to every request, logs method, path,
status code, and duration, and returns the ID as an ``X-Request-ID``
response header so callers can correlate downstream.

Usage:
    from common.logging_middleware import RequestLoggingMiddleware
    app.add_middleware(RequestLoggingMiddleware)

The middleware should be added **first** (outermost) so it wraps all other
middleware including auth.
"""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger("agoutic.request")

# Paths that are logged at DEBUG level to reduce noise
_QUIET_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/favicon.ico"})


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that:

    1. Generates a ``request_id`` (UUID4) and binds it to structlog context.
    2. Attaches it to ``request.state.request_id``.
    3. Measures wall-clock duration.
    4. Logs a structured entry on completion.
    5. Adds ``X-Request-ID`` response header.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())

        # Bind request_id into structlog contextvars for the duration of
        # this request so *any* log emitted during handling carries it.
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Make available to downstream handlers / routes
        request.state.request_id = request_id

        start = time.perf_counter()
        status_code = 500  # default in case of unhandled crash

        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)

            log_data = {
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "duration_ms": duration_ms,
                "client": request.client.host if request.client else None,
            }

            is_quiet = any(request.url.path.startswith(p) for p in _QUIET_PATHS)

            if is_quiet:
                logger.debug("request", **log_data)
            elif status_code >= 500:
                logger.error("request", **log_data)
            elif status_code >= 400:
                logger.warning("request", **log_data)
            else:
                logger.info("request", **log_data)

            # Unbind request-scoped vars so they don't leak to the next
            # request (important for uvicorn's thread reuse).
            structlog.contextvars.unbind_contextvars("request_id")
