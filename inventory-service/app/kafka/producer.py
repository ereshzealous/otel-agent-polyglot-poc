import json
import logging
from aiokafka import AIOKafkaProducer
from opentelemetry import propagate, trace
from app.config import settings

log = logging.getLogger(__name__)
tracer = trace.get_tracer("inventory-service")
_producer = None


def build_trace_headers():
    """Pack the current trace context into kafka headers so
    downstream consumers can pick up the same trace."""
    carrier = {}
    propagate.inject(carrier)
    return [(k, v.encode("utf-8")) for k, v in carrier.items()]


async def get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await _producer.start()
    return _producer

async def publish_inventory_reserved(event: dict):
    with tracer.start_as_current_span(
        "kafka.produce inventory.reserved",
        kind=trace.SpanKind.PRODUCER,
        attributes={"messaging.system": "kafka", "messaging.destination": "inventory.reserved"},
    ):
        producer = await get_producer()
        headers = build_trace_headers()
        await producer.send_and_wait("inventory.reserved", value=event, key=event["orderId"].encode(), headers=headers)
        log.info("Published INVENTORY_RESERVED for orderId=%s", event["orderId"])

async def publish_inventory_failed(event: dict):
    with tracer.start_as_current_span(
        "kafka.produce inventory.failed",
        kind=trace.SpanKind.PRODUCER,
        attributes={"messaging.system": "kafka", "messaging.destination": "inventory.failed"},
    ):
        producer = await get_producer()
        headers = build_trace_headers()
        await producer.send_and_wait("inventory.failed", value=event, key=event["orderId"].encode(), headers=headers)
        log.info("Published INVENTORY_FAILED for orderId=%s", event["orderId"])
