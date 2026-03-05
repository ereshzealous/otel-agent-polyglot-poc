from sqlalchemy import Column, String, Integer, Numeric, DateTime, func
from app.database import Base

class Product(Base):
    __tablename__ = "products"

    id         = Column(String(100), primary_key=True)
    name       = Column(String(255), nullable=False)
    stock      = Column(Integer, nullable=False, default=0)
    reserved   = Column(Integer, nullable=False, default=0)
    unit_price = Column(Numeric(10, 2), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def available_stock(self):
        return self.stock - self.reserved
