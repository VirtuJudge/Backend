import re
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id_ctx", default=None)

_CORRELATION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def get_correlation_id() -> str | None:
    return correlation_id_ctx.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_header = request.headers.get("X-Correlation-Id")
        if client_header and _CORRELATION_ID_REGEX.match(client_header):
            correlation_id = client_header
        else:
            correlation_id = str(uuid.uuid4())

        token = correlation_id_ctx.set(correlation_id)
        request.state.correlation_id = correlation_id
        try:
            response = await call_next(request)
        finally:
            correlation_id_ctx.reset(token)

        response.headers["X-Correlation-Id"] = correlation_id
        return response
