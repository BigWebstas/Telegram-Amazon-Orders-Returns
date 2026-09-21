import asyncio
import logging

from amazon_telegram_bot import poller
from amazon_telegram_bot.amazon_client import AmazonClient
from amazon_telegram_bot.config import load_config
from amazon_telegram_bot.storage import Storage
from amazon_telegram_bot.telegram_bot import build_application

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main() -> None:
    config = load_config()
    amazon = AmazonClient(config)
    storage = Storage(config.db_path)
    app = build_application(config, amazon, storage)

    async def start_poller(_app):
        asyncio.create_task(poller.run(config, amazon, storage, app))

    app.post_init = start_poller
    app.run_polling()


if __name__ == "__main__":
    main()
