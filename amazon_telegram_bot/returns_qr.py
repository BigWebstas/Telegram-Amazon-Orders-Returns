"""Amazon returns tracking and QR code delivery.

Reverse-engineered from several real walkthroughs (2026-09-21) rather than
`amazon-orders`, which doesn't cover returns at all. What's confirmed vs.
still a best-effort guess:

CONFIRMED:
- Returns list page: GET https://www.amazon.com/your-returns. Each return
  is a <div class="a-box a-spacing-medium item-return-history-card"> -
  a real, distinctive card boundary (seen in real HTML from two different
  cards), so parsing is scoped per-card rather than page-wide.
- Within a card, "View return status" is an <a> with
  data-event-type="returnHistoryItemCard:viewReturnStatus" - stable and
  semantic. Its href is `/spr/returns/prep?contractId=...&rmaId=...&
  orderId=...&itemId=...&shipmentId=...&returnSessionId=...`. `rmaId` is
  Amazon's actual return ID, distinct from `orderId` (one order can have
  multiple returns) - this is why ReturnSummary.return_id exists
  separately from order_number. The full link has to be captured from
  this page and replayed later; it can't be reconstructed from rmaId
  alone since contractId/itemId/shipmentId are also required to load
  the page.
- Also within a card: the item name is an
  <a class="a-size-base a-link-normal" href=".../dp/{ASIN}">, confirmed
  against two different cards. This lives on the *list* page, not the
  detail page - no need to visit the detail page just for this field.
- The QR (drop-off returns only) is a plain <img>, not canvas/JS-rendered:
  a presigned S3 URL like
  https://trans-qrcode-images-na.s3.amazonaws.com/<carrier-tracking-number>.gif
  Being presigned, it needs no Amazon auth to fetch - only the page that
  contains the <img src> needs an authenticated request. Confirmed against
  a FedEx drop-off only, though - three real UPS drop-off returns (Drop
  off at any UPS dropoff/Store) all came back with no QR found, so this
  URL pattern may be FedEx-specific, or UPS ones may render differently.
  Not yet confirmed which - get_return_qr_code() now logs any
  similar-looking URL it finds when the primary pattern misses, so the
  next miss should reveal the real one without another manual HTML paste.
- Terminal (completed) detection: only the declarative heading sentences
  "we have issued your refund" and "your refund was issued" are used.
  "Refund issued" alone was tried and reverted - a completed return's
  detail page also renders a step timeline ("Initiated" -> "Dropped off"
  -> "Refund issued" -> "Refund credited"), and that same short label
  apparently also renders for returns that HAVEN'T reached that step yet
  (confirmed bug: an active, not-yet-dropped-off battery return was
  wrongly excluded because its page contained "refund issued" as a
  timeline label, not a completion statement). The two full sentences
  are declarative status headers, not timeline chrome, so they don't have
  that problem - checked first, unconditionally, since a completed
  return's page can still show a QR image or "Return in transit" text.
- "Return in transit" and "Return by [date]" (the latter seen on the
  list page itself, per-return, alongside "Drop off at any UPS dropoff"/
  "Drop off at any UPS Store") confirmed as active (non-terminal) status
  phrases. "Drop off by [date]" specifically is still an unverified guess
  by analogy.

CORRECTED (history, so this mistake doesn't get repeated): an earlier
version parsed item_description from "Details ... Size:" text on what
turned out to be the wrong page (order details, not returns status), and
a later version added "refund issued" as a terminal phrase, which turned
out to be a timeline label present regardless of actual state (see above).
Both were guessed from copy-pasted visible text rather than real HTML/DOM
structure - the card-scoped selectors above came from actual view-source
snippets instead and have held up across multiple different cards.

BEST EFFORT / UNVERIFIED (revisit once more real examples are seen):
- "Drop off by [date]" as the pre-shipment status text is still a guess,
  not yet observed directly.
- Cards with no item-name link (unlikely, but not proven impossible) fall
  back to a generic "Return" label.
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

_RETURN_CARD_CLASS = "item-return-history-card"
_RETURN_STATUS_LINK_ATTRS = {"data-event-type": "returnHistoryItemCard:viewReturnStatus"}
_ITEM_LINK_SELECTOR = 'a.a-size-base.a-link-normal[href*="/dp/"]'
_QR_IMAGE_URL_PATTERN = re.compile(r"https://trans-qrcode-images-na\.s3\.amazonaws\.com/[^\"'\s]+")
# Only confirmed against a FedEx drop-off so far. Used purely for logging
# when the primary pattern misses, so the next carrier's real URL shows up
# in bot.log instead of needing another manual HTML paste to diagnose.
_QR_HINT_PATTERN = re.compile(r"https://[^\"'\s]*(?:qrcode|barcode|s3\.amazonaws\.com)[^\"'\s]*", re.IGNORECASE)

# "Drop off by ..." is still an unverified guess; "Return by ..." and
# "Return in transit" are confirmed - see module docstring.
_ACTIVE_STATUS_PATTERNS = [
    re.compile(r"return by [a-z]{3,9}\.?\s*\d{1,2}", re.IGNORECASE),
    re.compile(r"drop off by[^.\n]{0,40}", re.IGNORECASE),
    re.compile(r"return in transit", re.IGNORECASE),
]

# Deliberately just these two full declarative sentences - see module
# docstring for why the shorter "refund issued" was tried and reverted.
_TERMINAL_STATUS_PHRASES = [
    "we have issued your refund",
    "your refund was issued",
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


def format_return_message(ret: ReturnSummary, *, heading: str = "\U0001F504 Return in progress") -> str:
    """Shared by poller.py (proactive push) and telegram_bot.py (/returns)
    so a return always reads the same way regardless of where it's sent
    from."""
    return f"{heading}\nOrder {ret.order_number} - {ret.item_description}\n{ret.return_status}"


def _is_terminal_status(page_text: str) -> bool:
    lowered = page_text.lower()
    return any(phrase in lowered for phrase in _TERMINAL_STATUS_PHRASES)


def _guess_status_label(page_text: str) -> str:
    for pattern in _ACTIVE_STATUS_PATTERNS:
        match = pattern.search(page_text)
        if match:
            return match.group(0).strip()
    return "In progress"


def _extract_item_description(card) -> str:
    link = card.select_one(_ITEM_LINK_SELECTOR)
    if link and link.text.strip():
        return link.text.strip()
    return "Return"


def get_returns_in_progress(session: AmazonSession) -> list[ReturnSummary]:
    """List returns that are currently in progress (not yet completed).

    Fetches the returns list page for its per-return cards (item name
    comes from here), then visits each return's detail page (one request
    per return) to determine whether it's terminal, since the list page
    alone doesn't say.
    """
    list_response = session.get(RETURNS_LIST_URL)
    session.check_response(list_response)

    cards = list_response.parsed.find_all("div", class_=_RETURN_CARD_CLASS)
    logger.info("get_returns_in_progress: found %d return card(s) on %s", len(cards), RETURNS_LIST_URL)

    returns = []
    for card in cards:
        link_tag = card.find("a", attrs=_RETURN_STATUS_LINK_ATTRS)
        if not link_tag:
            logger.info("Return card had no 'View return status' link, skipping.")
            continue

        href = link_tag.get("href")
        if not href:
            logger.warning("Return-status link had no href, skipping: %s", link_tag)
            continue

        details_link = urljoin(RETURNS_LIST_URL, href)
        params = parse_qs(urlparse(details_link).query)
        rma_id = params.get("rmaId", [None])[0]
        order_number = params.get("orderId", [None])[0]
        if not rma_id or not order_number:
            logger.warning("Return-status link missing rmaId/orderId, skipping: %s", details_link)
            continue

        item_description = _extract_item_description(card)

        try:
            detail_response = session.get(details_link)
            session.check_response(detail_response)
        except AmazonOrdersError:
            logger.warning("Could not load return detail page for rmaId=%s, skipping.", rma_id, exc_info=True)
            continue

        page_text = detail_response.parsed.get_text(" ", strip=True)
        if _is_terminal_status(page_text):
            logger.info("rmaId=%s classified terminal, excluding from results.", rma_id)
            continue

        status_label = _guess_status_label(page_text)
        logger.info("rmaId=%s order=%s classified in-progress, status_label=%r", rma_id, order_number, status_label)

        returns.append(
            ReturnSummary(
                return_id=rma_id,
                order_number=order_number,
                item_description=item_description,
                return_status=status_label,
                return_details_link=details_link,
            )
        )

    logger.info("get_returns_in_progress: returning %d in-progress return(s)", len(returns))
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
        hint = _QR_HINT_PATTERN.search(response.response.text)
        if hint:
            logger.warning(
                "No QR match via the confirmed FedEx pattern on %s, but found a "
                "similar-looking URL that might be this carrier's actual QR image: "
                "%s - the pattern likely needs to cover this too.",
                return_details_link, hint.group(0),
            )
        else:
            logger.info(
                "No QR or QR-like URL found at all on %s (page may genuinely have "
                "none - e.g. not a drop-off return, or JS-rendered rather than "
                "static HTML).",
                return_details_link,
            )
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
    *,
    force: bool = False,
) -> bool:
    """Fetch and deliver a return's QR code, via the given send_photo callback.

    Shared by poller.py (proactive push) and telegram_bot.py (/returns
    on-demand). By default only sends once ever (storage-backed dedup),
    which is what the poller wants - it shouldn't re-push the same QR
    every poll cycle. Pass force=True (as /returns does) to resend
    on-demand regardless of that history - a user explicitly asking for
    their returns status wants the QR again, not "already sent, skipped."
    Returns True if a QR was actually sent.
    """
    if not force and storage.return_qr_already_sent(return_id):
        return False

    try:
        qr_bytes = await asyncio.to_thread(get_return_qr_code, session, return_details_link)
    except (NotImplementedError, ReturnHasNoQRCode):
        return False

    await send_photo(qr_bytes)
    storage.mark_qr_sent(return_id)
    return True
