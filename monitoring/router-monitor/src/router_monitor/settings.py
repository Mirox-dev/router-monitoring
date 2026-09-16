from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://router_monitor:change-me@localhost:5432/router_monitor"
    inventory: Path = Path("config/inventory.example.yml")
    poll_interval_seconds: int = 60
    ssh_connect_timeout_seconds: int = 10
    metrics_host: str = "0.0.0.0"
    metrics_port: int = 9108
    ssh_user: str = "root"
    ssh_client_keys: str | None = None

    model_config = SettingsConfigDict(env_prefix="ROUTER_MONITOR_", env_file=".env", extra="ignore")

    @property
    def client_keys(self) -> list[str] | None:
        return [item for item in self.ssh_client_keys.split(",") if item] if self.ssh_client_keys else None
