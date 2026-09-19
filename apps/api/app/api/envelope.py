"""统一响应 envelope（04-contracts-and-data.md §2）。"""
from fastapi import Request


def success(request: Request, data):
    return {
        "request_id": request.state.request_id,
        "trace_id": request.state.trace_id,
        "data": data,
        "error": None,
    }


def error_payload(
    request: Request,
    *,
    code: str,
    message: str,
    details: list | None = None,
    retryable: bool = False,
) -> dict:
    return {
        "request_id": request.state.request_id,
        "trace_id": request.state.trace_id,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "retryable": retryable,
        },
    }
