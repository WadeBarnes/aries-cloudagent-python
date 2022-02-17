"""Kafka Inbound Delivery Service."""
import asyncio
import logging
import msgpack
import json
import sys

from aiohttp import WSMessage, WSMsgType, web
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from configargparse import ArgumentParser
from uuid import uuid4

logging.basicConfig(
    format="%(asctime)s | %(levelname)s: %(message)s",
    level=logging.INFO,
)


class KafkaHTTPHandler:
    """Kafka inbound delivery service for HTTP."""

    # for unit testing
    RUNNING = True
    RUNNING_DIRECT_RESP = True

    def __init__(self, host: str, prefix: str, site_host: str, site_port: str):
        """Initialize KafkaHTTPHandler."""
        (self._host, self.username, self.password) = self.parse_connection_url(host)
        self.prefix = prefix
        self.site_host = site_host
        self.site_port = site_port
        self.producer = None
        self.consumer_direct_response = None
        self.direct_response_txn_request_map = {}
        self.direct_resp_topic = f"{self.prefix}.inbound_direct_responses"
        self.inbound_transport_key = f"{self.prefix}.inbound_transport"
        self.site = None
        self.timedelay_s = 1

    async def run(self):
        """Run the service."""
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self._host,
            enable_idempotence=True,
            transactional_id=str(uuid4()),
            sasl_plain_username=self.username,
            sasl_plain_password=self.password,
        )
        self.consumer_direct_response = AIOKafkaConsumer(
            self.direct_resp_topic,
            bootstrap_servers=self._host,
            group_id="my_group",
            auto_offset_reset="earliest",
            isolation_level="read_committed",
            sasl_plain_username=self.username,
            sasl_plain_password=self.password,
        )
        await asyncio.gather(self.start(), self.process_direct_responses())

    def parse_connection_url(self, connection):
        """Retreive bootstrap_server, username and password from provided connection."""
        kafka_username = None
        kafka_password = None
        split_kafka_url_by_hash = connection.rsplit("#", 1)
        if len(split_kafka_url_by_hash) > 1:
            kafka_username = split_kafka_url_by_hash[1].split(":")[0]
            kafka_password = split_kafka_url_by_hash[1].split(":")[1]
        kafka_url = split_kafka_url_by_hash[0]
        return (kafka_url, kafka_username, kafka_password)

    async def start(self):
        """Construct the aiohttp application."""
        await self.producer.start()
        app = web.Application()
        app.add_routes([web.get("/", self.invite_handler)])
        app.add_routes([web.post("/", self.message_handler)])
        runner = web.AppRunner(app)
        await runner.setup()
        self.site = web.TCPSite(runner, host=self.site_host, port=self.site_port)
        await self.site.start()

    async def stop(self) -> None:
        """Shutdown."""
        if self.site:
            await self.site.stop()
            self.site = None
        await self.consumer_direct_response.stop()
        await self.producer.stop()

    async def process_direct_responses(self):
        """Process inbound_direct_responses and update direct_response_txn_request_map."""
        await self.consumer_direct_response.start()
        while self.RUNNING_DIRECT_RESP:
            data = await self.consumer_direct_response.getmany(timeout_ms=10000)
            for tp, messages in data.items():
                for msg in messages:
                    msg = msgpack.unpackb(msg.value)
                    if not isinstance(msg, dict):
                        logging.error("Received non-dict message")
                        continue
                    elif "response_data" not in msg:
                        logging.error("No response provided")
                        continue
                    elif "txn_id" not in msg:
                        logging.error("No txn_id provided")
                        continue
                    txn_id = msg["txn_id"]
                    response_data = msg["response_data"]
                    self.direct_response_txn_request_map[txn_id] = response_data
                    await asyncio.sleep(self.timedelay_s)

    async def get_direct_responses(self, txn_id):
        """Get direct_response for a specific transaction/request."""
        while self.RUNNING_DIRECT_RESP:
            if txn_id in self.direct_response_txn_request_map:
                return self.direct_response_txn_request_map[txn_id]
            await asyncio.sleep(self.timedelay_s)

    async def invite_handler(self, request):
        """Handle inbound invitation."""
        if request.query.get("c_i"):
            return web.Response(
                text="You have received a connection invitation. To accept the "
                "invitation, paste it into your agent application."
            )
        else:
            return web.Response(status=200)

    async def message_handler(self, request):
        """Message handler for inbound messages."""
        ctype = request.headers.get("content-type", "")
        if ctype.split(";", 1)[0].lower() == "application/json":
            body = await request.text()
        else:
            body = await request.read()
        message_dict = json.loads(body)
        direct_response_request = False
        transport_dec = message_dict.get("~transport")
        if transport_dec:
            direct_response_mode = transport_dec.get("return_route")
            if direct_response_mode and direct_response_mode != "none":
                direct_response_request = True
        txn_id = str(uuid4())
        if direct_response_request:
            self.direct_response_txn_request_map[txn_id] = request
            message = msgpack.packb(
                {
                    "host": request.host,
                    "remote": request.remote,
                    "data": body,
                    "txn_id": txn_id,
                    "transport_type": "http",
                }
            )
            async with self.producer.transaction():
                await self.producer.send(
                    self.inbound_transport_key,
                    value=message,
                )
            try:
                response_data = await asyncio.wait_for(
                    self.get_direct_responses(
                        txn_id=txn_id,
                    ),
                    15,
                )
                response = response_data["response"]
                content_type = response_data.get("content_type", "application/json")
                if response:
                    return web.Response(
                        text=response,
                        status=200,
                        headers={"Content-Type": content_type},
                    )
            except asyncio.TimeoutError:
                return web.Response(status=200)
        else:
            logging.info(f"Message received from {request.remote}")
            message = msgpack.packb(
                {
                    "host": request.host,
                    "remote": request.remote,
                    "data": body,
                    "transport_type": "http",
                }
            )
            async with self.producer.transaction():
                await self.producer.send(
                    self.inbound_transport_key,
                    value=message,
                )
            return web.Response(status=200)


class KafkaWSHandler:
    """Kafka Inbound Delivery Service for WebSockets."""

    # for unit testing
    RUNNING = True
    RUNNING_DIRECT_RESP = True

    def __init__(self, host: str, prefix: str, site_host: str, site_port: str):
        """Initialize KafkaWSHandler."""
        (self._host, self.username, self.password) = self.parse_connection_url(host)
        self.prefix = prefix
        self.site_host = site_host
        self.site_port = site_port
        self.producer = None
        self.consumer_direct_response = None
        self.direct_response_txn_request_map = {}
        self.direct_resp_topic = f"{self.prefix}.inbound_direct_responses"
        self.inbound_transport_key = f"{self.prefix}.inbound_transport"
        self.site = None
        self.timedelay_s = 1

    async def run(self):
        """Run the service."""
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self._host,
            enable_idempotence=True,
            transactional_id=str(uuid4()),
            sasl_plain_username=self.username,
            sasl_plain_password=self.password,
        )
        self.consumer_direct_response = AIOKafkaConsumer(
            self.direct_resp_topic,
            bootstrap_servers=self._host,
            group_id="my_group",
            auto_offset_reset="earliest",
            isolation_level="read_committed",
            sasl_plain_username=self.username,
            sasl_plain_password=self.password,
        )
        await asyncio.gather(self.start(), self.process_direct_responses())

    def parse_connection_url(self, connection):
        """Retreive bootstrap_server, username and password from provided connection."""
        kafka_username = None
        kafka_password = None
        split_kafka_url_by_hash = connection.rsplit("#", 1)
        if len(split_kafka_url_by_hash) > 1:
            kafka_username = split_kafka_url_by_hash[1].split(":")[0]
            kafka_password = split_kafka_url_by_hash[1].split(":")[1]
        kafka_url = split_kafka_url_by_hash[0]
        return (kafka_url, kafka_username, kafka_password)

    async def start(self):
        """Construct the aiohttp application."""
        await self.producer.start()
        app = web.Application()
        app.add_routes([web.get("/", self.message_handler)])
        runner = web.AppRunner(app)
        await runner.setup()
        self.site = web.TCPSite(runner, host=self.site_host, port=self.site_port)
        await self.site.start()

    async def stop(self) -> None:
        """Shutdown."""
        if self.site:
            await self.site.stop()
            self.site = None
        await self.consumer_direct_response.stop()
        await self.producer.stop()

    async def process_direct_responses(self):
        """Process inbound_direct_responses and update direct_response_txn_request_map."""
        await self.consumer_direct_response.start()
        while self.RUNNING_DIRECT_RESP:
            data = await self.consumer_direct_response.getmany(timeout_ms=10000)
            for tp, messages in data.items():
                for msg in messages:
                    msg = msgpack.unpackb(msg.value)
                    if not isinstance(msg, dict):
                        logging.error("Received non-dict message")
                        continue
                    elif "response_data" not in msg:
                        logging.error("No response provided")
                        continue
                    elif "txn_id" not in msg:
                        logging.error("No txn_id provided")
                        continue
                    txn_id = msg["txn_id"]
                    response_data = msg["response_data"]
                    self.direct_response_txn_request_map[txn_id] = response_data
                    await asyncio.sleep(self.timedelay_s)

    async def get_direct_responses(self, txn_id):
        """Get direct_response for a specific transaction/request."""
        while self.RUNNING_DIRECT_RESP:
            if txn_id in self.direct_response_txn_request_map:
                return self.direct_response_txn_request_map[txn_id]
            await asyncio.sleep(self.timedelay_s)

    async def message_handler(self, request):
        """Message handler for inbound messages."""
        ws = web.WebSocketResponse(
            autoping=True,
            heartbeat=3,
            receive_timeout=15,
        )
        await ws.prepare(request)
        loop = asyncio.get_event_loop()
        inbound = loop.create_task(ws.receive())
        while not ws.closed:
            await asyncio.wait((inbound), return_when=asyncio.FIRST_COMPLETED)
            if inbound.done():
                msg: WSMessage = inbound.result()
                if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                    message_dict = json.loads(msg.data)
                    direct_response_request = False
                    transport_dec = message_dict.get("~transport")
                    if transport_dec:
                        direct_response_mode = transport_dec.get("return_route")
                        if direct_response_mode and direct_response_mode != "none":
                            direct_response_request = True
                    txn_id = str(uuid4())
                    if direct_response_request:
                        self.direct_response_txn_request_map[txn_id] = request
                        message = msgpack.packb(
                            {
                                "host": request.host,
                                "remote": request.remote,
                                "data": msg.data,
                                "txn_id": txn_id,
                                "transport_type": "ws",
                            }
                        )
                        async with self.producer.transaction():
                            await self.producer.send(
                                self.inbound_transport_key,
                                value=message,
                            )
                        try:
                            response_data = await asyncio.wait_for(
                                self.get_direct_responses(
                                    txn_id=txn_id,
                                ),
                                15,
                            )
                            response = response_data["response"]
                            if response:
                                if isinstance(response, bytes):
                                    await ws.send_bytes(response)
                                else:
                                    await ws.send_str(response)
                        except asyncio.TimeoutError:
                            pass
                    else:
                        logging.info(f"Message received from {request.remote}")
                        message = msgpack.packb(
                            {
                                "host": request.host,
                                "remote": request.remote,
                                "data": msg.data,
                                "transport_type": "ws",
                            }
                        )
                        async with self.producer.transaction():
                            await self.producer.send(
                                self.inbound_transport_key,
                                value=message,
                            )
                elif msg.type == WSMsgType.ERROR:
                    logging.error(
                        "Websocket connection closed with exception: %s",
                        ws.exception(),
                    )
                else:
                    logging.error(
                        "Unexpected Websocket message type received: %s: %s, %s",
                        msg.type,
                        msg.data,
                        msg.extra,
                    )
                if not ws.closed:
                    inbound = loop.create_task(ws.receive())
        if inbound and not inbound.done():
            inbound.cancel()
        if not ws.closed:
            await ws.close()
        logging.error("Websocket connection closed")
        return ws


async def main(args):
    """Start services."""
    args = argument_parser(args)
    if args.inbound_queue:
        host = args.inbound_queue
    else:
        raise SystemExit("No Kafka bootsrap server or host provided.")
    if args.inbound_queue_prefix:
        prefix = args.inbound_queue_prefix
    else:
        prefix = "acapy"
    tasks = []
    if not args.inbound_queue_transports:
        raise SystemExit("No inbound transport config provided.")
    for inbound_transport in args.inbound_queue_transports:
        transport_type, site_host, site_port = inbound_transport
        if transport_type == "ws":
            logging.info(
                "Starting Kafka ws inbound delivery service agent "
                f"with args: {host}, {prefix}, {site_host}, {site_port}"
            )
            handler = KafkaWSHandler(host, prefix, site_host, site_port)
        elif transport_type == "http":
            logging.info(
                "Starting Kafka http inbound delivery service agent "
                f"with args: {host}, {prefix}, {site_host}, {site_port}"
            )
            handler = KafkaHTTPHandler(host, prefix, site_host, site_port)
        else:
            raise SystemExit("Only ws and http transport type are supported.")
        tasks.append(handler.run())
    await asyncio.gather(*tasks)


def argument_parser(args):
    """Argument parser."""
    parser = ArgumentParser(description="kafka Inbound Delivery Service.")
    parser.add_argument(
        "-iq",
        "--inbound-queue",
        dest="inbound_queue",
        type=str,
        env_var="ACAPY_INBOUND_TRANSPORT_QUEUE",
    )
    parser.add_argument(
        "-iqp",
        "--inbound-queue-prefix",
        dest="inbound_queue_prefix",
        type=str,
        default="acapy",
        env_var="ACAPY_INBOUND_TRANSPORT_QUEUE_PREFIX",
    )
    parser.add_argument(
        "-iqt",
        "--inbound-queue-transports",
        dest="inbound_queue_transports",
        type=str,
        action="append",
        required=False,
        nargs=3,
        metavar=("<module>", "<host>", "<port>"),
        env_var="ACAPY_INBOUND_QUEUE_TRANSPORT",
    )
    return parser.parse_args(args)


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main(sys.argv[1:]))
