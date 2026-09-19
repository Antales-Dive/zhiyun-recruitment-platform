"""OCR 端口：Provider Adapter 边界；生产未配置时返回明确错误（PD-003）。"""
from abc import ABC, abstractmethod


class OcrProviderError(Exception):
    pass


class OcrNotConfiguredError(OcrProviderError):
    pass


class OcrUnavailableError(OcrProviderError):
    pass


class OcrPort(ABC):
    """图片/扫描件文本识别；OCR 文本按低置信度数据处理，不视为 100% 可信。"""

    @abstractmethod
    def recognize(self, image_path: str, language: str = "chi_sim+eng") -> str:
        raise NotImplementedError
