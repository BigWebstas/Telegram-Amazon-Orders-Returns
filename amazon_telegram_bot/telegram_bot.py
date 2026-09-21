import asyncio
import datetime
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from amazon_telegram_bot import returns_qr
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
                await update.message.reply_text(str(exc))
                return
            if not orders:
                await update.message.reply_text("No orders found.")
                return
            lines = [f"{o.order_number} - ${o.grand_total:.2f} - {_order_status(o)}" for o in orders[:20]]
            await update.message.reply_text("\n".join(lines))
            return

        # No year: serve from the poller's cache instead of calling Amazon,
        # unless the cache hasn't been populated by a poll cycle yet.
        rows = storage.get_cached_orders()
        if not rows and storage.get_last_poll_at() is None:
            try:
                orders = await asyncio.to_thread(amazon.fetch_recent_orders, "last30")
            except SessionNotReady as exc:
                await update.message.reply_text(str(exc))
                return
            rows = [
                (o.order_number, o.shipments[0].delivery_status if o.shipments else None, o.grand_total, o.cancelled)
                for o in orders
            ]

        active = [(number, status, total) for number, status, total, cancelled in rows
                  if _is_active_status(status, cancelled)]
        if not active:
            await update.message.reply_text("No active orders.")
            return

        lines = [
            f"{number} - {f'${total:.2f}' if total is not None else 'unknown total'} - {status}"
            for number, status, total in active[:20]
        ]
        await update.message.reply_text("\n".join(lines))

    async def transactions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        try:
            transactions = await asyncio.to_thread(amazon.fetch_transactions)
        except SessionNotReady as exc:
            await update.message.reply_text(str(exc))
            return

        if not transactions:
            await update.message.reply_text("No transactions found.")
            return

        lines = [
            f"{'Refund' if t.is_refund else 'Charge'} ${abs(t.grand_total):.2f} - "
            f"{t.order_number or 'n/a'} - {t.completed_date}"
            for t in transactions[:20]
        ]
        await update.message.reply_text("\n".join(lines))

    async def returns_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        try:
            await asyncio.to_thread(amazon.ensure_logged_in)
            returns = await asyncio.to_thread(returns_qr.get_returns_in_progress, amazon.session)
        except SessionNotReady as exc:
            await update.message.reply_text(str(exc))
            return
        except NotImplementedError:
            await update.message.reply_text(
                "Returns tracking and QR codes aren't implemented yet. "
                "See amazon_telegram_bot/returns_qr.py for the plan."
            )
            return

        if not returns:
            await update.message.reply_text("No returns in progress.")
            return

        lines = [f"{r.order_number} - {r.item_description} - {r.return_status}" for r in returns]
        await update.message.reply_text("\n".join(lines))

        for ret in returns:
            async def _send_photo(photo_bytes: bytes, _ret=ret) -> None:
                await update.message.reply_photo(photo=photo_bytes, caption=f"Return QR for order {_ret.order_number}")

            await returns_qr.send_return_qr_if_ready(
                amazon.session, storage, ret.return_id, ret.return_details_link, _send_photo
            )

    async def delivered_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=3)).isoformat()
        rows = storage.get_recent_deliveries(cutoff)

        if not rows:
            await update.message.reply_text(
                "No deliveries tracked in the last 3 days. Only deliveries the "
                "bot observed while running count here - it doesn't back-date "
                "ones from before it started polling."
            )
            return

        lines = []
        for order_number, delivered_at, grand_total in rows[:20]:
            total = f"${grand_total:.2f}" if grand_total is not None else "unknown total"
            lines.append(f"{order_number} - {total} - delivered {delivered_at[:10]}")
        await update.message.reply_text("\n".join(lines))

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return

        await update.message.reply_text("Checking Amazon login...")
        try:
            # No-ops if already authenticated, so this only pays the login-flow
            # cost when the cached session actually needs it.
            await asyncio.to_thread(amazon.ensure_logged_in)
            session_state = "authenticated"
        except SessionNotReady as exc:
            session_state = f"NOT authenticated - {exc}"

        last_poll_at = storage.get_last_poll_at()
        await update.message.reply_text(
            f"Amazon session: {session_state}\n"
            f"Last successful poll: {last_poll_at or 'never'}"
        )

        if os.path.exists(config.log_path) and os.path.getsize(config.log_path) > 0:
            with open(config.log_path, "rb") as log_file:
                await update.message.reply_document(document=log_file, filename="bot.log")
        else:
            await update.message.reply_text("No logs written yet.")

    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("delivered", delivered_command))
    app.add_handler(CommandHandler("transactions", transactions_command))
    app.add_handler(CommandHandler("returns", returns_command))
    app.add_handler(CommandHandler("status", status_command))

    return app
