"""Static bearer token for the MCP endpoint (local use; OAuth comes with deployment)."""

import hmac

from starlette.types import ASGIApp, Receive, Scope, Send


def bearer_matches(header: str | None, token: str) -> bool:
    return hmac.compare_digest((header or "").encode("utf-8"), f"Bearer {token}".encode("utf-8"))


class BearerGuard:
    """Rejects any HTTP request to the wrapped app without the expected bearer token."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            header = headers.get(b"authorization", b"").decode("latin-1")
            if not bearer_matches(header, self.token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"www-authenticate", b'Bearer realm="gemini-remote"')]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)
