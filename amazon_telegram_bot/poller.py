import asyncio
import datetime
import logging

from telegram.ext import Application

from amazon_telegram_bot.amazon_client import AmazonClient, SessionNotReady
from amazon_telegram_bot.config import Config
from amazon_telegram_bot.storage import Storage

logger = logging.getLogger(__name__)


def _format_order_message(order) -> str:
    status = order.shipments[0].delivery_status if order.shipments else "Unknown status"
    total = f"${order.grand_total:.2f}" if order.grand_total is not None else "unknown total"
    return (
        f"\U0001F4E6 Order {order.order_number}\n"
        f"{total} - placed {order.order_placed_date}\n"
        f"Status: {status}"
    )


def _is_delivered_status(status: str | None) -> bool:
    return bool(status) and status.strip().lower().startswith("delivered")


def _format_transaction_message(transaction) -> str:
    kind = "Refund" if transaction.is_refund else "Charge"
    return (
        f"\U0001F4B3 {kind}: ${abs(transaction.grand_total):.2f}\n"
        f"Order {transaction.order_number or 'n/a'} - {transaction.payment_method}\n"
        f"{transaction.completed_date}"
    )


async def _poll_once(amazon: AmazonClient, storage: Storage, app: Application, chat_id: int) -> None:
    orders = await asyncio.to_thread(amazon.fetch_recent_orders, "last30")
    for order in orders:
        status = order.shipments[0].delivery_status if order.shipments else None
        previous_status = storage.get_order_status(order.order_number)
        is_new = previous_status is None
        changed = previous_status is not None and previous_status != status
        if is_new or changed:
            await app.bot.send_message(chat_id=chat_id, text=_format_order_message(order))

        now = datetime.datetime.utcnow().isoformat()
        # Only stamp delivered_at on an observed transition, not on first sight -
        # an order that's already delivered when we first see it has an unknown
        # true delivery date, so it's left out of /delivered rather than guessed.
        if changed and _is_delivered_status(status) and not _is_delivered_status(previous_status):
            storage.mark_delivered(order.order_number, now)
        storage.upsert_order(order.order_number, status, now, order.grand_total)

    transactions = await asyncio.to_thread(amazon.fetch_transactions)
    for transaction in transactions:
        key = f"{transaction.order_number}:{transaction.completed_date}:{transaction.grand_total}"
        if storage.is_new_transaction(key):
            await app.bot.send_message(chat_id=chat_id, text=_format_transaction_message(transaction))
            storage.mark_transaction_seen(key, datetime.datetime.utcnow().isoformat())

    storage.set_last_poll_at(datetime.datetime.utcnow().isoformat())


async def run(config: Config, amazon: AmazonClient, storage: Storage, app: Application) -> None:
    already_alerted_auth_failure = False
    while True:
        try:
            await _poll_once(amazon, storage, app, config.telegram_chat_id)
            already_alerted_auth_failure = False
        except SessionNotReady:
            if not already_alerted_auth_failure:
                await app.bot.send_message(
                    chat_id=config.telegram_chat_id,
                    text=(
                        "⚠️ Amazon session expired. Run "
                        "`docker compose run --rm bot python -m amazon_telegram_bot.login_cli` "
                        "to reauthenticate."
                    ),
                )
                already_alerted_auth_failure = True
            logger.warning("Amazon session not ready, skipping this poll cycle.")
        except Exception:
            logger.exception("Unexpected error during poll cycle.")

        await asyncio.sleep(config.poll_interval_minutes * 60)
