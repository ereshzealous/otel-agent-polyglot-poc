import asyncio
import json
import logging
from aiokafka import AIOKafkaConsumer
from opentelemetry import context, propagate, trace
from app.config import settings
from app.database import AsyncSessionLocal
from app.services.inventory_service import reserve_inventory, InsufficientStockError, ProductNotFoundError
from app.kafka.producer import publish_inventory_reserved, publish_inventory_failed

log = logging.getLogger(__name__)
tracer = trace.get_tracer("inventory-service")


def extract_trace_ctx(headers):
    """Pull W3C traceparent/tracestate from kafka headers so spans
    stay linked to the same trace that the producer started."""
    carrier = {}
    if headers:
        for key, value in headers:
            carrier[key] = value.decode("utf-8") if isinstance(value, bytes) else value
    return propagate.extract(carrier)


async def start_kafka_consumer():
    consumer = AIOKafkaConsumer(
        "order.created",
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="inventory-service-group",
        auto_offset_reset="earliest",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        enable_auto_commit=False,
    )

    await consumer.start()
    log.info("Kafka consumer started on topic: order.created")

    try:
        async for message in consumer:
            parent_ctx = extract_trace_ctx(message.headers)
            with trace.use_span(
                tracer.start_span(
                    f"kafka.consume {message.topic}",
                    context=parent_ctx,
                    kind=trace.SpanKind.CONSUMER,
                    attributes={
                        "messaging.system": "kafka",
                        "messaging.destination": message.topic,
                        "messaging.operation": "receive",
                        "messaging.kafka.partition": message.partition,
                        "messaging.kafka.offset": message.offset,
                    },
                ),
                end_on_exit=True,
            ):
                await process_order_created(message.value)
            await consumer.commit()
    except asyncio.CancelledError:
        log.info("Kafka consumer shutting down")
    finally:
        await consumer.stop()

async def process_order_created(event: dict):
    order_id   = event.get("orderId")
    product_id = event.get("productId")
    quantity   = event.get("quantity")
    customer_id = event.get("customerId")

    log.info("Processing ORDER_CREATED: orderId=%s product=%s qty=%d", order_id, product_id, quantity)

    async with AsyncSessionLocal() as db:
        try:
            result = await reserve_inventory(db, product_id, quantity, order_id)
            await publish_inventory_reserved({
                "eventType": "INVENTORY_RESERVED",
                "orderId": order_id,
                "customerId": customer_id,
                "productId": product_id,
                "quantity": quantity,
                **result,
            })
            log.info("Inventory reserved for orderId=%s", order_id)

        except InsufficientStockError as e:
            log.warning("Insufficient stock for orderId=%s: %s", order_id, e)
            await publish_inventory_failed({
                "eventType": "INVENTORY_FAILED",
                "orderId": order_id,
                "reason": str(e),
            })
        except ProductNotFoundError as e:
            log.error("Product not found for orderId=%s: %s", order_id, e)
            await publish_inventory_failed({
                "eventType": "INVENTORY_FAILED",
                "orderId": order_id,
                "reason": str(e),
            })
