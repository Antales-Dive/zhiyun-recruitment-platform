"""任务域：消费端错误分类。"""
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.infrastructure.messaging.envelope import EventEnvelope


class RetryableTaskError(Exception):
    """可重试错误：超时、限流、暂时不可用等，按策略退避重试。"""


class PermanentTaskError(Exception):
    """永久错误：认证、配置、Schema 错误等，直接进入 DLQ 并告警。"""


# 事件处理器：同事务内执行业务写入与 Consumer Ledger 更新
type EventHandler = Callable[[Session, EventEnvelope], None]
