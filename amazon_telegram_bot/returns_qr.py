"""Scaffold for the Amazon returns QR code feature.

Not implemented yet. `amazon-orders` has no support for the returns flow —
it only covers order history, order details, and transactions. Amazon's
returns pages are also more JS-heavy than the order history pages, so this
will likely need the `amazon-orders[browser]` Playwright extra rather than
plain HTTP requests (the same one already wired up in `amazon_client.py`
for the ACIC/JS auth challenges).

Finishing this requires driving a real return through amazon.com once,
since none of this can be inspected from here. Fill in this checklist
while doing it, then update the two stub functions below accordingly:

1. Entry point - from Your Orders, which link starts a return, and does
   it differ for Whole Foods items vs. everything else?
2. Drop-off vs. mail-back - confirm only drop-off options (Whole Foods /
   UPS Store / Kohl's / Amazon Locker+) produce a QR; note the exact label
   text Amazon uses for each, so return_status parsing can recognize them.
3. Return identity - does the URL or page contain a distinct return ID
   separate from the order number? Record its format (this is why
   ReturnSummary below already has a separate return_id field - one order
   can have more than one return).
4. QR location - after confirming the drop-off method, is the QR on that
   same confirmation page, or does it require navigating to a
   "Track your return" / "View QR code" page? Record the URL pattern.
5. QR rendering - view source on the QR: is it a plain `<img src="...">`
   (downloadable straight from the existing `requests`-based session) or
   client-rendered (canvas/SVG via JS), which would need the Playwright
   browser session instead of a simple GET?
6. Returns list page - where are all in-progress returns listed at once
   (for get_returns_in_progress to poll)? Likely Your Account ->
   "Return, refund or replace items" -> a tracking page. Record the exact
   URL and the HTML structure of each return "card."
7. Status text - what text values does Amazon show for return status
   (e.g. "Drop off by [date]", "Refund processed", "Return received")?
   List them so status logic can be written the same way poller.py
   already handles order delivery status.

Once known, follow the selector-driven pattern amazon-orders itself uses
(see amazonorders/selectors.py) so this stays resilient to markup tweaks,
rather than hardcoding CSS selectors inline here.
"""

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from amazonorders.session import AmazonSession

from amazon_telegram_bot.storage import Storage


@dataclass
class ReturnSummary:
    return_id: str
    order_number: str
    item_description: str
    return_status: str
    return_details_link: str


def get_returns_in_progress(session: AmazonSession) -> list[ReturnSummary]:
    """List returns that are currently in progress on the account.

    TODO: navigate to the returns tracking page (checklist item 6) and
    parse each return card into a ReturnSummary, the way amazonorders.orders
    parses order cards.
    """
    raise NotImplementedError("Returns tracking is not implemented yet. See module docstring.")


def get_return_qr_code(session: AmazonSession, return_id: str) -> bytes:
    """Fetch the drop-off QR code image for a given return.

    TODO: open the return detail page for return_id (checklist item 4),
    confirm it's a drop-off return (not a mailed label - those have no QR),
    locate the QR image (checklist item 5), and return its raw bytes so the
    caller can send it to Telegram as a photo.
    """
    raise NotImplementedError("Returns QR fetching is not implemented yet. See module docstring.")


async def send_return_qr_if_ready(
    session: AmazonSession,
    storage: Storage,
    return_id: str,
    send_photo: Callable[[bytes], Awaitable[None]],
) -> bool:
    """Fetch and deliver a return's QR code once, via the given send_photo callback.

    Shared by poller.py (proactive push) and telegram_bot.py (/returns
    on-demand) so both go through the same "already sent?" bookkeeping
    instead of duplicating it. Returns True if a QR was actually sent.
    """
    if storage.return_qr_already_sent(return_id):
        return False

    try:
        qr_bytes = await asyncio.to_thread(get_return_qr_code, session, return_id)
    except NotImplementedError:
        return False

    await send_photo(qr_bytes)
    storage.mark_qr_sent(return_id)
    return True
