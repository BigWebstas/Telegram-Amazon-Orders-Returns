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
    data_dir: str

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
        data_dir=os.environ.get("DATA_DIR", "/data"),
    )
