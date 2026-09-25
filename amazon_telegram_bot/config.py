import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    amazon_email: str
    amazon_password: str
    telegram_bot_token: str
    telegram_chat_id: int
    poll_interval_minutes: int
    transaction_lookback_days: int
    data_dir: str
    mqtt_host: str | None
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_topic_prefix: str
    mqtt_retain: bool

    @property
    def amazon_config_dir(self) -> str:
        return os.path.join(self.data_dir, "amazon-orders")

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "bot.db")

    @property
    def log_path(self) -> str:
        return os.path.join(self.data_dir, "bot.log")


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    return Config(
        amazon_email=_require("AMAZON_EMAIL"),
        amazon_password=_require("AMAZON_PASSWORD"),
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=int(_require("TELEGRAM_CHAT_ID")),
        poll_interval_minutes=int(os.environ.get("POLL_INTERVAL_MINUTES", "30")),
        transaction_lookback_days=int(os.environ.get("TRANSACTION_LOOKBACK_DAYS", "14")),
        data_dir=os.environ.get("DATA_DIR", "/data"),
        mqtt_host=os.environ.get("MQTT_HOST") or None,
        mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
        mqtt_username=os.environ.get("MQTT_USERNAME") or None,
        mqtt_password=os.environ.get("MQTT_PASSWORD") or None,
        mqtt_topic_prefix=os.environ.get("MQTT_TOPIC_PREFIX", "amazon_orders_returns"),
        mqtt_retain=os.environ.get("MQTT_RETAIN", "true").strip().lower() not in ("false", "0", "no"),
    )
