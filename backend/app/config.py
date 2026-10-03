"""Configuration management for the Solar Tracker backend."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Application settings read from environment variables."""
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = "solartracker"
    mqtt_password: str = "changeme"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_key: str = "changeme-api-key"
    db_path: str = "./solar_tracker.db"
    device_id: str = "tracker-01"
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    timezone: str = "UTC"
    raw_retention_days: int = 7

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of the settings."""
    return Settings()
