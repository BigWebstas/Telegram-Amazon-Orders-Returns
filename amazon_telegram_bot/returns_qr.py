"""Amazon returns tracking and QR code delivery.

Reverse-engineered from a real walkthrough (2026-09-21) rather than
`amazon-orders`, which doesn't cover returns at all. What's confirmed vs.
still a best-effort guess:

CONFIRMED:
- Returns list page: GET https://www.amazon.com/your-returns. Each return's
  "View return status" button is an <a> with
  data-event-type="returnHistoryItemCard:viewReturnStatus" - a stable,
  semantic selector, not a generic CSS class.
- That link's href is `/spr/returns/prep?contractId=...&rmaId=...&orderId=
  ...&itemId=...&shipmentId=...&returnSessionId=...`. `rmaId` is Amazon's
  actual return ID, distinct from `orderId` (one order can have multiple
  returns) - this is why ReturnSummary.return_id exists separately from
  order_number. The full link has to be captured from this page and
  replayed later; it can't be reconstructed from rmaId alone since
  contractId/itemId/shipmentId are also required to load the page.
- The QR (drop-off returns only) is a plain <img>, not canvas/JS-rendered:
  a presigned S3 URL like
  https://trans-qrcode-images-na.s3.amazonaws.com/<carrier-tracking-number>.gif
  Being presigned, it needs no Amazon auth to fetch - only the page that
  contains the <img src> needs an authenticated request.
- Two confirmed terminal (completed) phrasings: "we have issued your
  refund" and, separately, "refund issued" (e.g. "$27.55 refund issued on
  Sep 20, 2026" - third person, with amount/date, seen on a return whose
  page *also* still said "Return in transit"). The QR image, and even an
  "in transit" tracking line, can both still be present after completion,
  so a terminal-phrase match always wins over any other status text found
  on the same page - checked first, unconditionally.
- item_description: the page's visible text runs "Returns center / Details
  / <item name> / Size: ... / Color: ... / $price / <status>" in that
  order (title tags/headings are generic, e.g. "Returns center" - not the
  item). _guess_item_description() pulls the text between "Details" and
  "Size:", confirmed against one real example ("Lepunuo Womens Casual
  Jumpsuits Summer...").
- "Return in transit" confirmed as an active (non-terminal) status phrase,
  alongside the still-unverified "Drop off by [date]" guess.

BEST EFFORT / UNVERIFIED (revisit once more real examples are seen):
- "Drop off by [date]" as the pre-shipment status text is still a guess,
  not yet observed directly.
- The "Details ... Size:" item_description pattern is confirmed for one
  item; items without a Size line (electronics, etc.) will fall through
  to the generic "Return" fallback instead.
- "return received" as a terminal phrase is still a guess by analogy, not
  observed.
"""

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from amazonorders.exception import AmazonOrdersError
from amazonorders.session import AmazonSession

from amazon_telegram_bot.storage import Storage

logger = logging.getLogger(__name__)

RETURNS_LIST_URL = "https://www.amazon.com/your-returns"

_RETURN_STATUS_LINK_ATTRS = {"data-event-type": "returnHistoryItemCard:viewReturnStatus"}
_QR_IMAGE_URL_PATTERN = re.compile(r"https://trans-qrcode-images-na\.s3\.amazonaws\.com/[^\"'\s]+")
_ITEM_DESCRIPTION_PATTERN = re.compile(r"\bDetails\b\s*(.+?)\s*\bSize:", re.IGNORECASE | re.DOTALL)

# "Drop off by ..." is still an unverified guess; "Return in transit" is
# confirmed - see module docstring.
_ACTIVE_STATUS_PATTERNS = [
    re.compile(r"drop off by[^.\n]{0,40}", re.IGNORECASE),
    re.compile(r"return in transit", re.IGNORECASE),
]

# Confirmed: "we have issued your refund" and "refund issued" (2026-09-21).
# "return received" is still guessed by analogy - see module docstring.
_TERMINAL_STATUS_PHRASES = [
    "we have issued your refund",
    "refund issued",
    "return received",
]


class ReturnHasNoQRCode(Exception):
    """Raised when a return's detail page has no drop-off QR to fetch.

    Expected for mail-back/printed-label returns, or once a return has
    progressed past the point where a QR is shown - not a failure.
    """


@dataclass
class ReturnSummary:
    return_id: str
    order_number: str
    item_description: str
    return_status: str
    return_details_link: str


def _is_terminal_status(page_text: str) -> bool:
    lowered = page_text.lower()
    return any(phrase in lowered for phrase in _TERMINAL_STATUS_PHRASES)


def _guess_status_label(page_text: str) -> str:
    for pattern in _ACTIVE_STATUS_PATTERNS:
        match = pattern.search(page_text)
        if match:
            return match.group(0).strip()
    return "In progress"


def _guess_item_description(page_text: str) -> str:
    match = _ITEM_DESCRIPTION_PATTERN.search(page_text)
    return match.group(1).strip() if match else "Return"


def get_returns_in_progress(session: AmazonSession) -> list[ReturnSummary]:
    """List returns that are currently in progress (not yet completed).

    Fetches the returns list page for the set of return links, then visits
    each return's detail page (one request per return) to determine
    whether it's terminal, since the list page alone doesn't say.
    """
    list_response = session.get(RETURNS_LIST_URL)
    session.check_response(list_response)

    returns = []
    for link_tag in list_response.parsed.find_all("a", attrs=_RETURN_STATUS_LINK_ATTRS):
        href = link_tag.get("href")
        if not href:
            continue

        details_link = urljoin(RETURNS_LIST_URL, href)
        params = parse_qs(urlparse(details_link).query)
        rma_id = params.get("rmaId", [None])[0]
        order_number = params.get("orderId", [None])[0]
        if not rma_id or not order_number:
            continue

        try:
            detail_response = session.get(details_link)
            session.check_response(detail_response)
        except AmazonOrdersError:
            logger.warning("Could not load return detail page for rmaId=%s, skipping.", rma_id)
            continue

        page_text = detail_response.parsed.get_text(" ", strip=True)
        if _is_terminal_status(page_text):
            continue

        returns.append(
            ReturnSummary(
                return_id=rma_id,
                order_number=order_number,
                item_description=_guess_item_description(page_text),
                return_status=_guess_status_label(page_text),
                return_details_link=details_link,
            )
        )

    return returns


def get_return_qr_code(session: AmazonSession, return_details_link: str) -> bytes:
    """Fetch the drop-off QR code image bytes from a return's detail page.

    Takes the full details link (as captured by get_returns_in_progress),
    not a bare return_id - contractId/itemId/shipmentId are also required
    to load the page, so return_id alone isn't enough to reconstruct it.
    """
    response = session.get(return_details_link)
    session.check_response(response)

    match = _QR_IMAGE_URL_PATTERN.search(response.response.text)
    if not match:
        raise ReturnHasNoQRCode(
            f"No QR code found on {return_details_link} - likely a mail-back "
            "return, or already past the point where a QR is shown."
        )

    qr_url = html.unescape(match.group(0))
    qr_response = requests.get(qr_url, timeout=30)
    qr_response.raise_for_status()
    return qr_response.content


async def send_return_qr_if_ready(
    session: AmazonSession,
    storage: Storage,
    return_id: str,
    return_details_link: str,
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
        qr_bytes = await asyncio.to_thread(get_return_qr_code, session, return_details_link)
    except (NotImplementedError, ReturnHasNoQRCode):
        return False

    await send_photo(qr_bytes)
    storage.mark_qr_sent(return_id)
    return True
