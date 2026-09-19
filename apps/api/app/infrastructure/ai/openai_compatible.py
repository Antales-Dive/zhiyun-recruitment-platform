"""OpenAI-compatible Embedding Adapter；业务域只依赖 EmbeddingPort。"""
import httpx

from app.config import settings
from app.infrastructure.ai.ports import AiNotConfiguredError, AiProviderError, EmbeddingPort
from app.infrastructure.model_gateway import _validate_provider_url


class OpenAICompatibleEmbedding(EmbeddingPort):
    def __init__(self) -> None:
        self.base_url = settings.model_base_url
        self.api_key = settings.model_api_key
        self.model = settings.model_embedding_name
        if self.base_url:
            self.base_url = _validate_provider_url(self.base_url)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.base_url or not self.api_key or not self.model:
            raise AiNotConfiguredError("Embedding Provider 未配置（PD-003）")
        try:
            response = httpx.post(
                f"{self.base_url.rstrip('/')}/embeddings",
                json={"model": self.model, "input": texts},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
                follow_redirects=False,
            )
            response.raise_for_status()
            items = response.json()["data"]
            vectors = [item["embedding"] for item in sorted(items, key=lambda item: item["index"])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AiProviderError("Embedding Provider 返回无效响应") from exc
        if len(vectors) != len(texts) or not all(isinstance(vector, list) for vector in vectors):
            raise AiProviderError("Embedding Provider 返回向量数量不匹配")
        return vectors
