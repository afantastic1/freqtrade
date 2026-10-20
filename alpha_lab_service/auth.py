"""Small constant-time bearer-token guard around MCP's HTTP application."""

import hmac


class BearerAuthMiddleware:
    def __init__(self, app, token: str):
        if not token or len(token) < 24:
            raise ValueError("MCP bearer token must contain at least 24 characters")
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"authorization", b"").decode("ascii", errors="ignore")
        expected = f"Bearer {self.token}"
        if not hmac.compare_digest(supplied, expected):
            body = b"Unauthorized"
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
