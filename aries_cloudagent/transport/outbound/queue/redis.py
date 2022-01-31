"""Redis outbound transport."""
import aioredis
import msgpack

from typing import Union

from ....core.profile import Profile

from .base import BaseOutboundQueue, OutboundQueueConfigurationError, OutboundQueueError


class RedisOutboundQueue(BaseOutboundQueue):
    """Redis outbound transport class."""

    config_key = "redis_outbound_queue"

    def __init__(self, root_profile: Profile) -> None:
        """Set initial state."""
        self._profile = root_profile
        try:
            plugin_config = root_profile.settings.get("plugin_config", {})
            config = plugin_config.get(self.config_key, {})
            self.connection = (
                self._profile.settings.get("transport.outbound_queue")
                or config["connection"]
            )
        except KeyError as error:
            raise OutboundQueueConfigurationError(
                "Configuration missing for redis queue"
            ) from error

        self.prefix = self._profile.settings.get(
            "transport.outbound_queue_prefix"
        ) or config.get("prefix", "acapy")
        self.pool = aioredis.ConnectionPool.from_url(
            self.connection, max_connections=10
        )
        self.redis = aioredis.Redis(connection_pool=self.pool)

    def __str__(self):
        """Return string representation of the outbound queue."""
        return (
            f"RedisOutboundQueue("
            f"connection={self.connection}, "
            f"prefix={self.prefix}"
            f")"
        )

    async def start(self):
        """Start the transport."""
        # aioredis will lazily connect but we can eagerly trigger connection with:
        # await self.redis.ping()
        # Calling this on enter to `async with` just before another queue
        # operation is made does not make sense and we should just let aioredis
        # do lazy connection.

    async def stop(self):
        """Stop the transport."""
        # aioredis cleans up automatically but we can clean up manually with:
        # await self.pool.disconnect()
        # However, calling this on exit of `async with` does not make sense and
        # we should just let aioredis handle the connection lifecycle.

    async def push(self, key: bytes, message: bytes):
        """Push a ``message`` to redis on ``key``."""
        try:
            return await self.redis.rpush(key, message)
        except aioredis.RedisError as error:
            raise OutboundQueueError("Unexpected redis client exception") from error

    async def enqueue_message(
        self,
        payload: Union[str, bytes],
        endpoint: str,
    ):
        """Prepare and send message to external redis.

        Args:
            payload: message payload in string or byte format
            endpoint: URI endpoint for delivery
        """
        if not endpoint:
            raise OutboundQueueError("No endpoint provided")
        if isinstance(payload, bytes):
            content_type = "application/ssi-agent-wire"
        else:
            content_type = "application/json"
        message = msgpack.packb(
            {
                "headers": {"Content-Type": content_type},
                "endpoint": endpoint,
                "payload": payload,
            }
        )
        key = f"{self.prefix}.outbound_transport".encode()
        return await self.push(key, message)
