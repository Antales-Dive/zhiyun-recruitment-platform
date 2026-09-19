"""邮件端口：Provider Adapter 边界；生产未配置时明确错误（PD-001）。"""
from abc import ABC, abstractmethod


class EmailProviderError(Exception):
    pass


class EmailNotConfiguredError(EmailProviderError):
    pass


class EmailPort(ABC):
    @abstractmethod
    def send(self, *, to: str, subject: str, body: str, idempotency_key: str) -> str:
        """发送邮件并返回 Provider 消息 ID；同幂等键不重复产生外部副作用。"""
        raise NotImplementedError
