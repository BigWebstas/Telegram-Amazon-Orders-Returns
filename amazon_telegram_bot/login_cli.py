"""Run this once interactively to log in and persist the Amazon session.

    docker compose run --rm bot python -m amazon_telegram_bot.login_cli

Solves any 2FA/CAPTCHA prompt against a real terminal, then writes cookies
under Config.amazon_config_dir so the background poller and bot commands can
reuse the session without prompting again. Re-run whenever the session
expires (the bot will tell you via a Telegram message when that happens).
"""

from amazon_telegram_bot.amazon_client import AmazonClient
from amazon_telegram_bot.config import load_config


def main() -> None:
    config = load_config()
    amazon = AmazonClient(config)
    amazon.session.login()
    print(f"Logged in and session persisted under {config.amazon_config_dir}")


if __name__ == "__main__":
    main()
