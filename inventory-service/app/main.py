import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from prometheus_client import make_asgi_app, Counter, Histogram

from app.config import settings
from app.database import get_db
from app.models import Product
from app.services.inventory_service import (
    get_all_products, get_product, get_available_stock,
    ProductNotFoundError
)
from app.kafka.consumer import start_kafka_consumer
from app.kafka.batch_consumer import start_batch_consumer
from app.kafka.producer import get_producer

log = logging.getLogger(__name__)

STOCK_CHECK_COUNTER = Counter(
    "inventory_stock_checks_total",
    "Total stock check requests",
    ["product_id", "cache_hit"]
)
RESERVATION_COUNTER = Counter(
    "inventory_reservations_total",
    "Total inventory reservations",
    ["status"]
)
RESERVATION_LATENCY = Histogram(
    "inventory_reservation_duration_seconds",
    "Time to reserve inventory",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0]
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting inventory service")
    await get_producer()
    consumer_task = asyncio.create_task(start_kafka_consumer())
    batch_task = asyncio.create_task(start_batch_consumer())
    yield
    consumer_task.cancel()
    batch_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    try:
        await batch_task
    except asyncio.CancelledError:
        pass
    log.info("Inventory service stopped")

app = FastAPI(
    title="ShopFlow Inventory Service",
    version="1.0.0",
    lifespan=lifespan,
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

@app.get("/api/products")
async def list_products(db: AsyncSession = Depends(get_db)):
    products = await get_all_products(db)
    return [
        {
            "id": p.id,
            "name": p.name,
            "stock": p.stock,
            "reserved": p.reserved,
            "available": p.available_stock,
            "unitPrice": float(p.unit_price),
        }
        for p in products
    ]

@app.get("/api/products/{product_id}/stock")
async def check_stock(product_id: str, db: AsyncSession = Depends(get_db)):
    try:
        available = await get_available_stock(db, product_id)
        STOCK_CHECK_COUNTER.labels(product_id=product_id, cache_hit="unknown").inc()
        return {"productId": product_id, "availableStock": available}
    except ProductNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/api/products/{product_id}")
async def get_product_detail(product_id: str, db: AsyncSession = Depends(get_db)):
    try:
        p = await get_product(db, product_id)
        return {
            "id": p.id,
            "name": p.name,
            "stock": p.stock,
            "reserved": p.reserved,
            "available": p.available_stock,
            "unitPrice": float(p.unit_price),
        }
    except ProductNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok", "service": "inventory-service"}
