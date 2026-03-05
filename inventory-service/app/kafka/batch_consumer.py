import asyncio
import logging
import random
import httpx
from aiokafka import AIOKafkaConsumer
from opentelemetry import context, propagate, trace
from app.config import settings
from app.database import AsyncSessionLocal
from app.services.inventory_service import get_available_stock, get_product, ProductNotFoundError
from app.redis_client import get_cached_stock, set_cached_stock

log = logging.getLogger(__name__)
tracer = trace.get_tracer("inventory-service")


def extract_trace_ctx(headers):
    """Same W3C header extraction as the main consumer."""
    carrier = {}
    if headers:
        for key, value in headers:
            carrier[key] = value.decode("utf-8") if isinstance(value, bytes) else value
    return propagate.extract(carrier)


async def start_batch_consumer():
    consumer = AIOKafkaConsumer(
        "batch.test",
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="inventory-batch-test-group",
        auto_offset_reset="earliest",
        value_deserializer=lambda v: __import__("json").loads(v.decode("utf-8")),
        enable_auto_commit=False,
    )

    await consumer.start()
    log.info("Batch consumer started on topic: batch.test")

    try:
        async for message in consumer:
            parent_ctx = extract_trace_ctx(message.headers)

            # each message gets its own consumer span, linked to the producer's trace
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
                await route_batch_action(message.value)
            await consumer.commit()
    except asyncio.CancelledError:
        log.info("Batch consumer shutting down")
    finally:
        await consumer.stop()


async def route_batch_action(event: dict):
    """Each action type takes a different code path so we get distinct spans."""
    action = event.get("action", "UNKNOWN")
    batch_id = event.get("batchId")
    seq = event.get("seq")
    product_id = event.get("productId")
    quantity = event.get("quantity", 1)

    log.info("Batch[%s] seq=%s action=%s product=%s", batch_id, seq, action, product_id)

    if action == "STOCK_CHECK":
        await handle_stock_check(product_id)

    elif action == "PRICE_LOOKUP":
        await handle_price_lookup(product_id)

    elif action == "RESTOCK_ALERT":
        await handle_restock_alert(product_id, quantity)

    elif action == "AUDIT_LOG":
        await handle_audit_log(batch_id, seq, event)

    elif action == "CACHE_WARM":
        await handle_cache_warm(product_id)

    elif action == "REPORT_GENERATE":
        await handle_report_generate()

    else:
        log.warning("Unknown batch action: %s", action)


async def handle_stock_check(product_id: str):
    """Query the DB for current stock level."""
    with tracer.start_as_current_span("batch.stock_check",
            attributes={"product.id": product_id}):
        async with AsyncSessionLocal() as db:
            try:
                stock = await get_available_stock(db, product_id)
                log.info("Stock check: product=%s available=%d", product_id, stock)
            except ProductNotFoundError:
                log.warning("Stock check: product=%s not found", product_id)


async def handle_price_lookup(product_id: str):
    """Fetch product details — simulates a price service call."""
    with tracer.start_as_current_span("batch.price_lookup",
            attributes={"product.id": product_id}):
        async with AsyncSessionLocal() as db:
            try:
                product = await get_product(db, product_id)
                log.info("Price lookup: product=%s price=%s", product_id, product.unit_price)
            except ProductNotFoundError:
                log.warning("Price lookup: product=%s not found", product_id)


async def handle_restock_alert(product_id: str, quantity: int):
    """Checks stock and calls the inventory service's own HTTP endpoint
    to verify — this creates an outbound HTTP span."""
    with tracer.start_as_current_span("batch.restock_alert",
            attributes={"product.id": product_id, "restock.quantity": quantity}):
        try:
            async with httpx.AsyncClient() as client:
                # call our own HTTP endpoint — goes through otelhttp instrumentation
                resp = await client.get(
                    f"http://127.0.0.1:{settings.port}/api/products/{product_id}/stock",
                    timeout=5.0,
                )
                data = resp.json()
                available = data.get("availableStock", 0)
                if available < quantity:
                    log.warning("Restock needed: product=%s available=%d requested=%d",
                                product_id, available, quantity)
                else:
                    log.info("Stock OK: product=%s available=%d", product_id, available)
        except Exception as e:
            log.error("Restock alert failed: %s", e)


async def handle_audit_log(batch_id: str, seq: int, event: dict):
    """Simulates writing an audit record — just a delay + log."""
    with tracer.start_as_current_span("batch.audit_log",
            attributes={"batch.id": batch_id, "batch.seq": seq}):
        delay = random.uniform(0.01, 0.05)
        await asyncio.sleep(delay)
        log.info("Audit logged: batch=%s seq=%s action=%s (%.0fms)",
                 batch_id, seq, event.get("action"), delay * 1000)


async def handle_cache_warm(product_id: str):
    """Pre-loads stock into redis cache."""
    with tracer.start_as_current_span("batch.cache_warm",
            attributes={"product.id": product_id}):
        async with AsyncSessionLocal() as db:
            try:
                stock = await get_available_stock(db, product_id)
                await set_cached_stock(product_id, stock, ttl=600)
                log.info("Cache warmed: product=%s stock=%d", product_id, stock)
            except ProductNotFoundError:
                log.warning("Cache warm skipped: product=%s not found", product_id)


async def handle_report_generate():
    """Calls the analytics service to trigger a report — outbound HTTP span."""
    with tracer.start_as_current_span("batch.report_generate"):
        try:
            async with httpx.AsyncClient() as client:
                # calls analytics-service — creates a cross-service HTTP span
                resp = await client.get(
                    f"http://{settings.analytics_service_url}/api/reports",
                    timeout=5.0,
                )
                log.info("Report fetched: status=%d", resp.status_code)
        except Exception as e:
            log.error("Report generate failed: %s", e)
