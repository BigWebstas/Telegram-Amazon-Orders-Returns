import asyncio

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from amazon_telegram_bot.amazon_client import AmazonClient, SessionNotReady
from amazon_telegram_bot.config import Config
from amazon_telegram_bot.storage import Storage


def _authorized(config: Config, update: Update) -> bool:
    return bool(update.effective_chat) and update.effective_chat.id == config.telegram_chat_id


def build_application(config: Config, amazon: AmazonClient, storage: Storage) -> Application:
    app = Application.builder().token(config.telegram_bot_token).build()

    async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        year = int(context.args[0]) if context.args else None
        try:
            if year:
                orders = await asyncio.to_thread(amazon.fetch_orders_for_year, year)
            else:
                orders = await asyncio.to_thread(amazon.fetch_recent_orders, "last30")
        except SessionNotReady as exc:
            await update.message.reply_text(str(exc))
            return

        if not orders:
            await update.message.reply_text("No orders found.")
            return

        lines = [f"{o.order_number} - ${o.grand_total:.2f} - {o.order_placed_date}" for o in orders[:20]]
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
        await update.message.reply_text(
            "Returns tracking and QR codes aren't implemented yet. "
            "See amazon_telegram_bot/returns_qr.py for the plan."
        )

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(config, update):
            return
        last_poll_at = storage.get_last_poll_at()
        session_state = "authenticated" if amazon.session.is_authenticated else "not authenticated"
        await update.message.reply_text(
            f"Amazon session: {session_state}\n"
            f"Last successful poll: {last_poll_at or 'never'}"
        )

    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("transactions", transactions_command))
    app.add_handler(CommandHandler("returns", returns_command))
    app.add_handler(CommandHandler("status", status_command))

    return app
