import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models import Product
from app.redis_client import get_cached_stock, set_cached_stock, invalidate_stock_cache

log = logging.getLogger(__name__)

class InsufficientStockError(Exception):
    pass

class ProductNotFoundError(Exception):
    pass

async def get_product(db: AsyncSession, product_id: str) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise ProductNotFoundError(f"Product {product_id} not found")
    return product

async def get_available_stock(db: AsyncSession, product_id: str) -> int:
    cached = await get_cached_stock(product_id)
    if cached is not None:
        log.info("Cache hit for product=%s stock=%d", product_id, cached)
        return cached

    log.info("Cache miss for product=%s, querying DB", product_id)
    product = await get_product(db, product_id)
    available = product.available_stock
    await set_cached_stock(product_id, available)
    return available

async def reserve_inventory(db: AsyncSession, product_id: str, quantity: int, order_id: str) -> dict:
    log.info("Reserving product=%s qty=%d for order=%s", product_id, quantity, order_id)

    # lock the row so two concurrent orders can't double-book the same stock
    result = await db.execute(
        select(Product).where(Product.id == product_id).with_for_update()
    )
    product = result.scalar_one_or_none()

    if not product:
        raise ProductNotFoundError(f"Product {product_id} not found")

    if product.available_stock < quantity:
        raise InsufficientStockError(
            f"Insufficient stock for {product_id}: "
            f"available={product.available_stock}, requested={quantity}"
        )

    product.reserved += quantity
    await db.commit()
    await db.refresh(product)
    await invalidate_stock_cache(product_id)

    log.info("Reserved product=%s qty=%d new_reserved=%d", product_id, quantity, product.reserved)
    return {
        "productId": product.id,
        "productName": product.name,
        "reservedQuantity": quantity,
        "remainingStock": product.available_stock,
        "orderId": order_id,
    }

async def get_all_products(db: AsyncSession) -> list[Product]:
    result = await db.execute(select(Product))
    return result.scalars().all()
