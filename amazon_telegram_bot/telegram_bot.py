import asyncio
import datetime
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from amazon_telegram_bot import returns_qr, telegram_send
from amazon_telegram_bot.amazon_client import AmazonClient, SessionNotReady
from amazon_telegram_bot.config import Config
from amazon_telegram_bot.storage import Storage


def _authorized(config: Config, update: Update) -> bool:
    return bool(update.effective_chat) and update.effective_chat.id == config.telegram_chat_id


def _is_active_status(status: str | None, cancelled: bool) -> bool:
    if cancelled:
        return False
    return (status or "").strip().lower().startswith("arriving")


def _order_status(order) -> str:
    if order.cancelled:
        return "Cancelled"
    statuses = {s.delivery_status for s in order.shipments if s.delivery_status}
    return "; ".join(sorted(statuses)) if statuses else "Processing"


def _item_description(order) -> str:
    titles = [item.title for item in order.items if item.title]
    return "; ".join(titles) if titles else "Unknown item"


def _format_order_listing_message(order_number: str, item_description: str, total: float | None, status: str) -> str:
    total_str = f"${total:.2f}" if total is not None else "unknown total"
    return f"\U0001F4E6 Order {order_number} - {item_description}\n{total_str}\n{status}"


def build_application(config: Config, amazon: AmazonClient, storage: Storage) -> Application:
    app = Application.builder().token(config.telegram_bot_token).build()

    async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        year = int(context.args[0]) if context.args else None

        if year:
            # A specific year is always outside the poller's rolling 30-day
            # cache, so this still has to hit Amazon live.
            try:
                orders = await asyncio.to_thread(amazon.fetch_orders_for_year, year)
            except SessionNotReady as exc:
                await telegram_send.reply_text(update, storage, str(exc))
                return
            if not orders:
                await telegram_send.reply_text(update, storage, "No orders found.")
                return
            for o in orders[:20]:
                await telegram_send.reply_text(
                    update,
                    storage,
                    _format_order_listing_message(o.order_number, _item_description(o), o.grand_total, _order_status(o)),
                )
            return

        # No year: serve from the poller's cache instead of calling Amazon,
        # unless the cache hasn't been populated by a poll cycle yet.
        rows = storage.get_cached_orders()
        if not rows and storage.get_last_poll_at() is None:
            try:
                orders = await asyncio.to_thread(amazon.fetch_recent_orders, "last30")
            except SessionNotReady as exc:
                await telegram_send.reply_text(update, storage, str(exc))
                return
            rows = [
                (
                    o.order_number,
                    o.shipments[0].delivery_status if o.shipments else None,
                    o.grand_total,
                    o.cancelled,
                    _item_description(o),
                )
                for o in orders
            ]

        active = [
            (number, status, total, item_description)
            for number, status, total, cancelled, item_description in rows
            if _is_active_status(status, cancelled)
        ]
        if not active:
            await telegram_send.reply_text(update, storage, "No active orders.")
            return

        for number, status, total, item_description in active[:20]:
            await telegram_send.reply_text(
                update,
                storage,
                _format_order_listing_message(number, item_description or "Unknown item", total, status),
            )

    async def transactions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        try:
            transactions = await asyncio.to_thread(amazon.fetch_transactions)
        except SessionNotReady as exc:
            await telegram_send.reply_text(update, storage, str(exc))
            return

        if not transactions:
            await telegram_send.reply_text(update, storage, "No transactions found.")
            return

        lines = [
            f"{'Refund' if t.is_refund else 'Charge'} ${abs(t.grand_total):.2f} - "
            f"{t.order_number or 'n/a'} - {t.completed_date}"
            for t in transactions[:20]
        ]
        await telegram_send.reply_text(update, storage, "\n".join(lines))

    async def returns_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        try:
            await asyncio.to_thread(amazon.ensure_logged_in)
            returns = await asyncio.to_thread(returns_qr.get_returns_in_progress, amazon.session)
        except SessionNotReady as exc:
            await telegram_send.reply_text(update, storage, str(exc))
            return
        except NotImplementedError:
            await telegram_send.reply_text(
                update,
                storage,
                "Returns tracking and QR codes aren't implemented yet. "
                "See amazon_telegram_bot/returns_qr.py for the plan.",
            )
            return

        if not returns:
            await telegram_send.reply_text(update, storage, "No returns in progress.")
            return

        for ret in returns:
            message_text = returns_qr.format_return_message(ret)

            async def _send_photo(photo_bytes: bytes, _text=message_text) -> None:
                # Photo caption carries the full status text, so this is one
                # message instead of a separate text message plus a photo.
                await telegram_send.reply_photo(update, storage, photo_bytes, _text)

            sent_with_photo = await returns_qr.send_return_qr_if_ready(
                amazon.session, storage, ret.return_id, ret.return_details_link, _send_photo, force=True
            )
            if not sent_with_photo:
                await telegram_send.reply_text(update, storage, message_text)

    async def delivered_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=3)).isoformat()
        rows = storage.get_recent_deliveries(cutoff)

        if not rows:
            await telegram_send.reply_text(
                update,
                storage,
                "No deliveries tracked in the last 3 days. Only deliveries the "
                "bot observed while running count here - it doesn't back-date "
                "ones from before it started polling.",
            )
            return

        lines = []
        for order_number, delivered_at, grand_total in rows[:20]:
            total = f"${grand_total:.2f}" if grand_total is not None else "unknown total"
            lines.append(f"{order_number} - {total} - delivered {delivered_at[:10]}")
        await telegram_send.reply_text(update, storage, "\n".join(lines))

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return

        await telegram_send.reply_text(update, storage, "Checking Amazon login...")
        try:
            # No-ops if already authenticated, so this only pays the login-flow
            # cost when the cached session actually needs it.
            await asyncio.to_thread(amazon.ensure_logged_in)
            session_state = "authenticated"
        except SessionNotReady as exc:
            session_state = f"NOT authenticated - {exc}"

        last_poll_at = storage.get_last_poll_at()
        await telegram_send.reply_text(
            update,
            storage,
            f"Amazon session: {session_state}\n"
            f"Last successful poll: {last_poll_at or 'never'}",
        )

        if os.path.exists(config.log_path) and os.path.getsize(config.log_path) > 0:
            with open(config.log_path, "rb") as log_file:
                await telegram_send.reply_document(update, storage, log_file, filename="bot.log")
        else:
            await telegram_send.reply_text(update, storage, "No logs written yet.")

    async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return

        if not context.args:
            await telegram_send.reply_text(
                update, storage, "Usage: /clear <days> - deletes bot messages older than that many days."
            )
            return

        try:
            days = int(context.args[0])
        except ValueError:
            await telegram_send.reply_text(update, storage, "Days must be a whole number, e.g. /clear 10")
            return
        if days < 0:
            await telegram_send.reply_text(update, storage, "Days must be zero or greater.")
            return

        deleted, failed = await telegram_send.clear_messages_older_than(app, storage, update.effective_chat.id, days)

        summary = f"\U0001F5D1 Cleared {deleted} message(s) older than {days} day(s)."
        if failed:
            summary += f" {failed} couldn't be deleted (already removed, or too old for Telegram to delete)."
        await telegram_send.reply_text(update, storage, summary)

    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("delivered", delivered_command))
    app.add_handler(CommandHandler("transactions", transactions_command))
    app.add_handler(CommandHandler("returns", returns_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("clear", clear_command))

    return app
