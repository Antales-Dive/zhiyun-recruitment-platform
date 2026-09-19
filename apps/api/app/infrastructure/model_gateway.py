"""Model Gateway：OpenAI-compatible Chat Adapter 与统一错误分类。

生产配置缺失时返回 MODEL_NOT_CONFIGURED 明确错误（DEC-011），
绝不返回演示结果、硬编码答案或随机分数。

SSRF 防御：Provider URL 来自服务端部署配置而非用户输入，仍做协议
白名单、内嵌凭据拒绝、解析后阻断私网/环回/链路本地地址，并禁止重定向。
"""
import ipaddress
import socket
import time
import urllib.parse
from dataclasses import dataclass

import httpx

from app.config import settings


class ProviderError(Exception):
    """统一归一化错误：业务层只依赖类别，不依赖供应商字段。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ModelNotConfiguredError(ProviderError):
    def __init__(self, message: str = "模型 Provider 未配置（PD-003）"):
        super().__init__("MODEL_NOT_CONFIGURED", message)


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str = "模型调用超时"):
        super().__init__("TIMEOUT", message)


class ProviderUnavailableError(ProviderError):
    def __init__(self, message: str = "模型 Provider 不可用"):
        super().__init__("UNAVAILABLE", message)


class ProviderAuthError(ProviderError):
    def __init__(self, message: str = "模型 Provider 认证失败"):
        super().__init__("AUTH_FAILED", message)


class ProviderInvalidResponseError(ProviderError):
    def __init__(self, message: str = "模型返回无法解析的结构化结果"):
        super().__init__("INVALID_RESPONSE", message)


@dataclass(frozen=True)
class ChatResult:
    content: str
    provider: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None


def _validate_provider_url(base_url: str) -> str:
    """Provider URL 配置防御（防 SSRF）：协议白名单、无内嵌凭据、
    解析后阻断私网/环回/链路本地地址。"""
    parsed = urllib.parse.urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ProviderInvalidResponseError(f"Provider URL 协议不受支持：{parsed.scheme}")
    if not parsed.hostname:
        raise ProviderInvalidResponseError("Provider URL 缺少主机名")
    if parsed.username or parsed.password:
        raise ProviderInvalidResponseError("Provider URL 禁止内嵌用户名/密码")
    _reject_private_host(parsed.hostname)
    return base_url.strip()


def _reject_private_host(hostname: str) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as exc:
        raise ProviderInvalidResponseError(f"Provider 主机无法解析：{hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ProviderInvalidResponseError(f"Provider 地址不允许访问内网/本机：{address}")


class ModelGateway:
    """OpenAI-compatible Chat Adapter；结构化输出由调用方用 Pydantic 校验。"""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = base_url or settings.model_base_url
        self.api_key = api_key or settings.model_api_key
        self.model = model or settings.model_chat_name
        if self.base_url:
            self.base_url = _validate_provider_url(self.base_url)

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages: list[dict[str, str]], *, timeout_seconds: int = 60) -> ChatResult:
        if not self.is_configured():
            raise ModelNotConfiguredError()

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        started = time.monotonic()
        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
                response = client.post(
                    url,
                    json={"model": self.model, "messages": messages},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(str(exc)) from exc

        if response.is_redirect:
            raise ProviderInvalidResponseError("Provider 不允许重定向")
        if response.status_code in {401, 403}:
            raise ProviderAuthError()
        if response.status_code == 429:
            raise ProviderUnavailableError("模型限流（RATE_LIMITED）")
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"Provider 服务错误（{response.status_code}）")

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponseError("响应缺少 choices/message/content") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return ChatResult(
            content=content,
            provider="openai-compatible",
            model=self.model,
            latency_ms=latency_ms,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )
