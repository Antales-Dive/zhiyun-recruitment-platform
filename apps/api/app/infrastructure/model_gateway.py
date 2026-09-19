"""Model Gateway：OpenAI-compatible Chat Adapter 与统一错误分类。

生产配置缺失时返回 MODEL_NOT_CONFIGURED 明确错误（DEC-011），
绝不返回演示结果、硬编码答案或随机分数。

【它是 Agent 链路的唯一出口】
matching/agents.py、assistant/service.py（RAG 生成）、orchestration/interview_graph.py
全部只依赖 chat(messages) -> ChatResult 这一个方法。换供应商（OpenAI 兼容协议族内）
只需换 base_url/model，业务层零改动；这也是测试里能用桩替换它的原因。

【为什么要自建一层错误分类】
各家 SDK 抛的异常类型、字段名、状态码语义都不同。业务层如果直接 catch httpx 异常，
"要不要转人工"这个业务判断就会被供应商实现细节绑住。这里把所有失败压成一个
ProviderError.code 分类，图节点只需按 code 决定 review_reason。

【不做什么】
- 不重试：重试/退避属于消息消费层（infrastructure/messaging/consumer.py + DLQ），
  在这里重试会让一次匹配变成不可预期的长耗时任务；
- 不缓存、不流式、不做成本控制：当前一次调用一条 ModelRun 留痕即可；
- 不解析业务语义：返回原始文本，结构化校验是调用方（Pydantic）的职责。

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
    """统一归一化错误：业务层只依赖类别，不依赖供应商字段。

    code 是落库与前端展示用的稳定标识（review_reason / error_code 直接取它），
    message 给人看，可能随供应商变化，调用方不应对它做匹配。
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# 下面五个子类区分的是"运维该做什么"，而不是 HTTP 状态码本身：
# 未配置 → 补配置；超时 → 看供应商延迟；不可用 → 看供应商可用性；
# 认证失败 → 轮换密钥；无效响应 → 查协议/提示词。
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
    """一次成功调用的返回值。frozen：调用结果不可被下游改写（审计一致性）。

    latency_ms 用 monotonic 计算，不受系统时钟调整影响；
    token 计数为 None 表示该供应商未返回 usage（不同兼容实现差异），
    因此统计侧必须按可空处理，不能假定成本数据总是存在。
    """

    content: str
    provider: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None


def _validate_provider_url(base_url: str) -> str:
    """Provider URL 配置防御（防 SSRF）：协议白名单、无内嵌凭据、
    解析后阻断私网/环回/链路本地地址。

    为什么要防：MODEL_BASE_URL 虽是运维写入的服务端配置，但它决定容器实际发起
    连接的目标；一旦它被错误地接入到"租户可配置模型地址"的路径上，
    就变成一条从应用打到云元数据接口/内网服务的路。这里按不可信输入对待。
    file://、gopher:// 之类协议直接拒绝；凭据必须走 Authorization 头而非 URL 内嵌。

    已知残余风险：本函数解析一次、httpx 连接时再解析一次，两次之间的 DNS 重绑定
    （rebinding）未被覆盖。当前依赖"配置来源受控 + 禁用重定向"缓解；若将来允许
    租户自定义模型地址，需改成在连接层固定已校验的 IP 再发起请求。
    """
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
    """把主机名解析成全部 A/AAAA 地址后逐个判定，任一为内网/本机即拒绝。

    逐地址而非只看第一个：公网 DNS 轮询常混排内外网地址，只看一条会漏放。
    解析失败也当作错误（而非放行），避免攻击者用慢/坏 DNS 拖过检查。
    """
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as exc:
        raise ProviderInvalidResponseError(f"Provider 主机无法解析：{hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ProviderInvalidResponseError(f"Provider 地址不允许访问内网/本机：{address}")


class ModelGateway:
    """OpenAI-compatible Chat Adapter；结构化输出由调用方用 Pydantic 校验。

    构造参数全部可注入，默认回落 settings：测试传桩 URL/密钥即可覆盖
    超时、限流、非法 JSON 等分支，不需要真实供应商。
    URL 校验放在构造期而非调用期：配置错误在进程启动/首次装配时即暴露。
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = base_url or settings.model_base_url
        self.api_key = api_key or settings.model_api_key
        self.model = model or settings.model_chat_name
        if self.base_url:
            self.base_url = _validate_provider_url(self.base_url)

    def is_configured(self) -> bool:
        """三项齐备才算已配置；RAG 侧用它决定走生成还是走"证据摘要 + 拒答"分支。"""
        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages: list[dict[str, str]], *, timeout_seconds: int = 60) -> ChatResult:
        """发起一次 /chat/completions 调用。

        Args:
            messages: OpenAI 格式消息列表（role/content），由调用方构造。
            timeout_seconds: 单次请求硬超时（不是总重试预算，本方法不重试）。
                与消费侧租约 settings.consumer_lease_seconds（默认 120s）一起估算：
                一次匹配最多三路调用，若单路 60s 串行叠加就会超过租约，
                导致消息被判定处理失败并重投，因此调大此值必须同时评估租约。

        Returns:
            ChatResult：content 为模型原始文本，未做任何业务解析。

        Raises:
            ModelNotConfiguredError / ProviderTimeoutError / ProviderUnavailableError /
            ProviderAuthError / ProviderInvalidResponseError：全部为 ProviderError 子类，
            调用方按 code 分流即可，不需要感知 httpx 或供应商响应结构。
        """
        # 未配置即抛错，绝不退化成"随机分数/演示回答"：错误结论比无结论危害更大。
        if not self.is_configured():
            raise ModelNotConfiguredError()

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        started = time.monotonic()
        try:
            # follow_redirects=False：3xx 会把请求带到校验之外的主机，等于绕过 SSRF 检查，
            # 因此在客户端层面直接禁掉自动跳转，并在下方显式拒绝重定向响应。
            with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
                response = client.post(
                    url,
                    json={"model": self.model, "messages": messages},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        # 异常映射顺序：超时是 httpx.HTTPError 的子类，必须排在前面才能给出 TIMEOUT。
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(str(exc)) from exc

        # 状态码判定顺序即优先级：先重定向（安全），再认证（换密钥），再限流/5xx（等一等），
        # 其余未覆盖的 4xx（如 400 请求体不合法）会落到下面的解析分支，
        # 因缺少 choices 字段而归为 INVALID_RESPONSE。
        if response.is_redirect:
            raise ProviderInvalidResponseError("Provider 不允许重定向")
        if response.status_code in {401, 403}:
            raise ProviderAuthError()
        if response.status_code == 429:
            raise ProviderUnavailableError("模型限流（RATE_LIMITED）")
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"Provider 服务错误（{response.status_code}）")

        try:
            # 只承认 OpenAI 协议的最小必要结构；用 KeyError/IndexError 等一次性捕获，
            # 任何结构偏差都归为 INVALID_RESPONSE，而不是让下游看到裸异常。
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

