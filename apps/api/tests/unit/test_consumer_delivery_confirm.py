from app.infrastructure.messaging.consumer import ConsumerRuntime


class FakeChannel:
    def __init__(self, publish_result):
        self.publish_result = publish_result
        self.acks = []

    def basic_publish(self, **_kwargs):
        return self.publish_result

    def basic_ack(self, *, delivery_tag):
        self.acks.append(delivery_tag)


def test_retry_publish_returns_broker_confirmation():
    runtime = ConsumerRuntime("test", {})
    runtime.channel = FakeChannel(True)

    assert runtime._requeue_with_delay(b"event") is True

    runtime.channel = FakeChannel(False)
    assert runtime._requeue_with_delay(b"event") is False
