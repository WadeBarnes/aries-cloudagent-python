import string
import msgpack
import pytest

from asynctest import TestCase as AsyncTestCase, mock as async_mock

from .....core.in_memory.profile import InMemoryProfile

from ..base import OutboundQueueConfigurationError, OutboundQueueError
from ..kafka import KafkaOutboundQueue

ENDPOINT = "http://localhost:9000"
KEYNAME = "acapy.kafka_outbound_transport"


class TestKafkaOutbound(AsyncTestCase):
    def setUp(self):
        self.session = InMemoryProfile.test_session()
        self.profile = self.session.profile
        self.context = self.profile.context

    async def test_init(self):
        self.profile.settings["transport.outbound_queue"] = "connection"
        with async_mock.patch(
            "aiokafka.AIOKafkaProducer.start",
            async_mock.CoroutineMock(),
        ), async_mock.patch(
            "aiokafka.AIOKafkaProducer",
            async_mock.MagicMock(),
        ):
            queue = KafkaOutboundQueue(self.profile)
            queue.prefix == "acapy"
            queue.connection = "connection"
            assert str(queue)
            await queue.start()
            await queue.stop()

    async def test_close(self):
        self.profile.settings["transport.outbound_queue"] = "connection"
        with async_mock.patch(
            "aiokafka.AIOKafkaProducer.start",
            async_mock.CoroutineMock(),
        ), async_mock.patch(
            "aiokafka.AIOKafkaProducer",
            async_mock.MagicMock(),
        ):
            queue = KafkaOutboundQueue(self.profile)
            await queue.start()
            queue.producer._closed = True
            await queue.close()

    async def test_enqueue_message_x(self):
        self.profile.settings["transport.outbound_queue"] = "connection"
        with async_mock.patch(
            "aiokafka.AIOKafkaProducer.start",
            async_mock.CoroutineMock(),
        ), async_mock.patch(
            "aiokafka.AIOKafkaProducer",
            async_mock.MagicMock(),
        ):
            queue = KafkaOutboundQueue(self.profile)
            with self.assertRaises(OutboundQueueError):
                await queue.start()
                await queue.enqueue_message(
                    payload=string.ascii_letters + string.digits, endpoint=None
                )

    def test_init_x(self):
        with pytest.raises(OutboundQueueConfigurationError):
            KafkaOutboundQueue(self.profile)

    async def test_enqueue_message_str(self):
        self.profile.settings["transport.outbound_queue"] = "connection"
        with async_mock.patch(
            "aiokafka.AIOKafkaProducer.start",
            async_mock.CoroutineMock(),
        ), async_mock.patch(
            "aiokafka.AIOKafkaProducer.transaction",
            async_mock.MagicMock(),
        ), async_mock.patch(
            "aiokafka.AIOKafkaProducer.send",
            async_mock.CoroutineMock(),
        ) as mock_send, async_mock.patch(
            "aiokafka.AIOKafkaProducer",
            async_mock.MagicMock(),
        ):
            queue = KafkaOutboundQueue(self.profile)
            await queue.start()
            await queue.enqueue_message(
                payload=string.ascii_letters + string.digits,
                endpoint=ENDPOINT,
            )
            mock_send.assert_called_once()

    async def test_enqueue_message_bytes(self):
        self.profile.settings["plugin_config"] = {
            "kafka_outbound_queue": {
                "connection": "connection",
                "prefix": "acapy",
            }
        }
        with async_mock.patch(
            "aiokafka.AIOKafkaProducer.start",
            async_mock.CoroutineMock(),
        ), async_mock.patch(
            "aiokafka.AIOKafkaProducer.transaction",
            async_mock.MagicMock(),
        ), async_mock.patch(
            "aiokafka.AIOKafkaProducer.send",
            async_mock.CoroutineMock(),
        ) as mock_send, async_mock.patch(
            "aiokafka.AIOKafkaProducer",
            async_mock.MagicMock(),
        ):
            queue = KafkaOutboundQueue(self.profile)
            bytes_payload = bytes(range(0, 256))
            await queue.start()
            await queue.enqueue_message(
                payload=bytes_payload,
                endpoint=ENDPOINT,
            )
            mock_send.assert_called_once()
