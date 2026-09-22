import asyncio
import datetime
import logging

import paho.mqtt.client as mqtt
from telegram.ext import Application

from amazon_telegram_bot import mqtt_publisher, returns_qr
from amazon_telegram_bot.amazon_client import AmazonClient, SessionNotReady
from amazon_telegram_bot.config import Config
from amazon_telegram_bot.storage import Storage

logger = logging.getLogger(__name__)


def _is_delivered_status(status: str | None) -> bool:
    return bool(status) and status.strip().lower().startswith("delivered")


def _shipment_status(order) -> str | None:
    return order.shipments[0].delivery_status if order.shipments else None


def _is_active_order(order) -> bool:
    if order.cancelled:
        return False
    return (_shipment_status(order) or "").strip().lower().startswith("arriving")


def _is_arriving_today(order) -> bool:
    if order.cancelled:
        return False
    return (_shipment_status(order) or "").strip().lower() == "arriving today"


def _total_str(order) -> str:
    return f"${order.grand_total:.2f}" if order.grand_total is not None else "unknown total"


def _item_description(order) -> str:
    titles = [item.title for item in order.items if item.title]
    return "; ".join(titles) if titles else "Unknown item"


def _format_new_order_message(order) -> str:
    return (
        f"\U0001F195 New order placed\n"
        f"Order {order.order_number} - {_item_description(order)}\n"
        f"{_total_str(order)} - placed {order.order_placed_date}"
    )


def _format_delivered_message(order) -> str:
    return f"✅ Delivered!\nOrder {order.order_number} - {_item_description(order)}\n{_total_str(order)}"


def _format_status_change_message(order, previous_status: str | None, status: str | None) -> str:
    return (
        f"\U0001F4E6 Status update\n"
        f"Order {order.order_number} - {_item_description(order)}\n"
        f"{_total_str(order)}\n"
        f"{previous_status or 'Unknown'} → {status or 'Unknown'}"
    )


def _format_transaction_message(transaction) -> str:
    kind = "Refund" if transaction.is_refund else "Charge"
    return (
        f"\U0001F4B3 {kind}: ${abs(transaction.grand_total):.2f}\n"
        f"Order {transaction.order_number or 'n/a'} - {transaction.payment_method}\n"
        f"{transaction.completed_date}"
    )


async def _poll_once(
    amazon: AmazonClient,
    storage: Storage,
    app: Application,
    chat_id: int,
    mqtt_client: mqtt.Client | None,
    config: Config,
) -> None:
    # On the very first poll ever, seen_orders/seen_transactions are empty, so
    # every existing order/transaction would otherwise look "new" and flood
    # the chat. Seed storage silently instead - only report what changes
    # from here on.
    is_bootstrap = storage.get_last_poll_at() is None

    orders = await asyncio.to_thread(amazon.fetch_recent_orders, "last30")
    for order in orders:
        status = order.shipments[0].delivery_status if order.shipments else None
        previous_status = storage.get_order_status(order.order_number)
        is_new = previous_status is None
        changed = previous_status is not None and previous_status != status
        became_delivered = changed and _is_delivered_status(status) and not _is_delivered_status(previous_status)

        if is_new and not is_bootstrap:
            await app.bot.send_message(chat_id=chat_id, text=_format_new_order_message(order))
        elif changed:
            if became_delivered:
                await app.bot.send_message(chat_id=chat_id, text=_format_delivered_message(order))
            else:
                await app.bot.send_message(
                    chat_id=chat_id, text=_format_status_change_message(order, previous_status, status)
                )

        now = datetime.datetime.utcnow().isoformat()
        # Only stamp delivered_at on an observed transition, not on first sight -
        # an order that's already delivered when we first see it has an unknown
        # true delivery date, so it's left out of /delivered rather than guessed.
        if became_delivered:
            storage.mark_delivered(order.order_number, now)
        storage.upsert_order(
            order.order_number, status, now, order.grand_total, order.cancelled, _item_description(order)
        )

    transactions = await asyncio.to_thread(amazon.fetch_transactions)
    for transaction in transactions:
        key = f"{transaction.order_number}:{transaction.completed_date}:{transaction.grand_total}"
        if storage.is_new_transaction(key):
            if not is_bootstrap:
                await app.bot.send_message(chat_id=chat_id, text=_format_transaction_message(transaction))
            storage.mark_transaction_seen(key, datetime.datetime.utcnow().isoformat())

    # NotImplementedError kept here for safety, but get_returns_in_progress
    # is implemented now - see that module's docstring for what's confirmed
    # vs. still a best-effort guess (status text, item descriptions).
    try:
        returns = await asyncio.to_thread(returns_qr.get_returns_in_progress, amazon.session)
    except NotImplementedError:
        returns = []

    for ret in returns:
        is_new_return = storage.is_new_return(ret.return_id)
        should_announce = is_new_return and not is_bootstrap
        heading = "\U0001F504 Return started" if should_announce else "\U0001F504 Return in progress"
        message_text = returns_qr.format_return_message(ret, heading=heading)

        storage.upsert_return(ret.return_id, ret.order_number, ret.return_status, datetime.datetime.utcnow().isoformat())

        async def _send_photo(photo_bytes: bytes, _text=message_text) -> None:
            # Photo caption carries the full status text, so a return that
            # already has its QR ready gets one message, not two.
            await app.bot.send_photo(chat_id=chat_id, photo=photo_bytes, caption=_text)

        # Deliberately not gated on is_bootstrap: the QR is something you
        # actually need to complete the return, so a pre-existing one from
        # before the bot started shouldn't be swallowed silently.
        sent_with_photo = await returns_qr.send_return_qr_if_ready(
            amazon.session, storage, ret.return_id, ret.return_details_link, _send_photo
        )

        # Only send a bare text announcement if this was actually a new
        # return AND the combined photo+caption didn't already cover it
        # (no QR ready yet, or it wasn't a drop-off return at all).
        if should_announce and not sent_with_photo:
            await app.bot.send_message(chat_id=chat_id, text=message_text)

    if mqtt_client:
        # Reuses orders/returns already fetched this cycle rather than
        # issuing extra Amazon requests just for the sensor counts.
        now = datetime.datetime.utcnow()
        deliveries_3day_cutoff = (now - datetime.timedelta(days=3)).isoformat()
        # UTC calendar day, consistent with every other timestamp this bot
        # stores - may not line up with your local "today" near midnight.
        today_cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        mqtt_publisher.publish_counts(
            mqtt_client,
            config,
            active_orders=sum(1 for o in orders if _is_active_order(o)),
            returns_in_progress=len(returns),
            deliveries_last_3_days=len(storage.get_recent_deliveries(deliveries_3day_cutoff)),
            delivered_today=len(storage.get_recent_deliveries(today_cutoff)),
            will_be_delivered_today=sum(1 for o in orders if _is_arriving_today(o)),
        )

    storage.set_last_poll_at(datetime.datetime.utcnow().isoformat())


async def run(
    config: Config,
    amazon: AmazonClient,
    storage: Storage,
    app: Application,
    mqtt_client: mqtt.Client | None = None,
) -> None:
    already_alerted_auth_failure = False
    while True:
        try:
            await _poll_once(amazon, storage, app, config.telegram_chat_id, mqtt_client, config)
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
