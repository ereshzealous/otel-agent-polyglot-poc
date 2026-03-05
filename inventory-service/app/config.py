from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://shopflow:shopflow123@localhost:5432/inventorydb"
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap_servers: str = "localhost:9092"
    analytics_service_url: str = "analytics-service:8083"
    port: int = 8081

    class Config:
        env_file = ".env"

settings = Settings()
