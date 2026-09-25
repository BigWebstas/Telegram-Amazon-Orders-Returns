"""Every outbound Telegram message goes through here.

Two things this buys, shared by poller.py (proactive pushes) and
telegram_bot.py (on-demand commands):

1. Resilience - a single flood-control hit (RetryAfter) is retried once;
   any other failure is logged and swallowed (returns None) instead of
   raised, so one bad send can't abort a whole poll cycle or command.
2. Tracking - every message actually delivered is recorded in storage
   (message_id + sent_at), which is what /clear (see telegram_bot.py)
   uses to find and delete old messages. Only messages sent after this
   was added are trackable - there's no way to retroactively learn the
   IDs/timestamps of messages sent before it existed.
"""

import asyncio
import datetime
import logging
from typing import Awaitable, Callable

from telegram import Message
from telegram.error import RetryAfter, TelegramError
from telegram.ext import Application

from amazon_telegram_bot.storage import Storage

logger = logging.getLogger(__name__)


async def _with_retry(send: Callable[[], Awaitable[Message]]) -> Message | None:
    try:
        return await send()
    except RetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 1)
        try:
            return await send()
        except TelegramError:
            logger.exception("Telegram send failed even after flood-control wait.")
            return None
    except TelegramError:
        logger.exception("Telegram send failed.")
        return None


def _track(storage: Storage, message: Message | None) -> int | None:
    if message is None:
        return None
    storage.record_sent_message(message.message_id, datetime.datetime.utcnow().isoformat())
    return message.message_id


async def send_message(app: Application, storage: Storage, chat_id: int, text: str) -> int | None:
    message = await _with_retry(lambda: app.bot.send_message(chat_id=chat_id, text=text))
    return _track(storage, message)


async def send_photo(app: Application, storage: Storage, chat_id: int, photo: bytes, caption: str) -> int | None:
    message = await _with_retry(lambda: app.bot.send_photo(chat_id=chat_id, photo=photo, caption=caption))
    return _track(storage, message)


async def reply_text(update, storage: Storage, text: str) -> int | None:
    message = await _with_retry(lambda: update.message.reply_text(text))
    return _track(storage, message)


async def reply_photo(update, storage: Storage, photo: bytes, caption: str) -> int | None:
    message = await _with_retry(lambda: update.message.reply_photo(photo=photo, caption=caption))
    return _track(storage, message)


async def reply_document(update, storage: Storage, document, filename: str) -> int | None:
    message = await _with_retry(lambda: update.message.reply_document(document=document, filename=filename))
    return _track(storage, message)


async def _delete_one(app: Application, chat_id: int, message_id: int) -> bool:
    try:
        await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except RetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 1)
        try:
            await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except TelegramError:
            logger.warning("Could not delete message %s after flood-control wait.", message_id, exc_info=True)
            return False
    except TelegramError:
        # Expected for messages a user already deleted by hand, or (per the
        # Bot API) ones too old for Telegram to delete at all.
        logger.info("Could not delete message %s (already gone, or too old to delete).", message_id, exc_info=True)
        return False


async def clear_messages_older_than(app: Application, storage: Storage, chat_id: int, days: int) -> tuple[int, int]:
    """Delete every tracked bot message older than `days` days. Returns (deleted, failed).

    Only ever touches messages this bot sent and tracked via the helpers
    above - Telegram lets a bot delete its own messages without needing
    admin rights, but deleting other users' messages needs admin/
    can_delete_messages, which this deliberately doesn't attempt.
    """
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()
    message_ids = storage.get_sent_messages_older_than(cutoff)

    deleted = 0
    failed = 0
    for message_id in message_ids:
        if await _delete_one(app, chat_id, message_id):
            deleted += 1
        else:
            failed += 1

    # Drop every attempted record regardless of outcome - a failed delete is
    # almost always permanent, so retrying it on a future /clear would just
    # repeat the same failure.
    storage.delete_sent_message_records(message_ids)
    return deleted, failed
