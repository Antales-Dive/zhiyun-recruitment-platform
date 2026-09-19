"""AI 端口：Embedding 与 Rerank（Provider Adapter 边界，PD-003）。"""
from abc import ABC, abstractmethod


class AiProviderError(Exception):
    pass


class AiNotConfiguredError(AiProviderError):
    pass


class EmbeddingPort(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class RerankPort(ABC):
    @abstractmethod
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """返回与 documents 等长的相关性分数（0~1）。"""
        raise NotImplementedError


class UnconfiguredEmbedding(EmbeddingPort):
    """生产未配置时按无 Dense 结果处理；绝不生成假向量。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AiNotConfiguredError("Embedding Provider 未配置（PD-003）")


class UnconfiguredRerank(RerankPort):
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise AiNotConfiguredError("Rerank Provider 未配置（PD-003）")
