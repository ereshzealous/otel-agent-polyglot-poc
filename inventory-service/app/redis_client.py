import redis.asyncio as redis
from app.config import settings
import logging

log = logging.getLogger(__name__)

_redis_client = None

async def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client

async def get_cached_stock(product_id: str) -> int | None:
    r = await get_redis()
    val = await r.get(f"inventory:stock:{product_id}")
    return int(val) if val is not None else None

async def set_cached_stock(product_id: str, stock: int, ttl: int = 300):
    r = await get_redis()
    await r.setex(f"inventory:stock:{product_id}", ttl, stock)

async def invalidate_stock_cache(product_id: str):
    r = await get_redis()
    await r.delete(f"inventory:stock:{product_id}")
