"""请求上下文中间件（纯 ASGI）：注入 request_id / trace_id。

不使用 BaseHTTPMiddleware：它会缓冲 StreamingResponse 的响应体，
导致 SSE 长连接永远收不到首个分块。纯 ASGI 实现直接透传
receive/send，仅包装响应头与 scope 状态。
"""
from uuid import uuid4


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        request_id = headers.get("x-request-id") or str(uuid4())
        trace_id = headers.get("x-trace-id") or str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        scope["state"]["trace_id"] = trace_id

        request_id_bytes = request_id.encode("latin-1")
        trace_id_bytes = trace_id.encode("latin-1")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id_bytes),
                    (b"x-trace-id", trace_id_bytes),
                ]
            await send(message)

        await self.app(scope, receive, send_wrapper)
