import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

from amazon_telegram_bot import mqtt_publisher, poller
from amazon_telegram_bot.amazon_client import AmazonClient
from amazon_telegram_bot.config import Config, load_config
from amazon_telegram_bot.storage import Storage
from amazon_telegram_bot.telegram_bot import build_application


def _configure_logging(config: Config) -> None:
    os.makedirs(config.data_dir, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    file_handler = RotatingFileHandler(config.log_path, maxBytes=1_000_000, backupCount=2)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logging.basicConfig(level=logging.INFO, handlers=[stream_handler, file_handler])


def main() -> None:
    config = load_config()
    _configure_logging(config)

    amazon = AmazonClient(config)
    storage = Storage(config.db_path)
    app = build_application(config, amazon, storage)

    mqtt_client = mqtt_publisher.connect(config)
    if mqtt_client:
        mqtt_publisher.publish_discovery(mqtt_client, config)

    async def start_poller(_app):
        asyncio.create_task(poller.run(config, amazon, storage, app, mqtt_client))

    app.post_init = start_poller
    app.run_polling()


if __name__ == "__main__":
    main()
