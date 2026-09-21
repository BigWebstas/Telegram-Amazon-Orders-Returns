"""Scaffold for the Amazon returns QR code feature.

Not implemented yet. `amazon-orders` has no support for the returns flow —
it only covers order history, order details, and transactions. Amazon's
returns pages are also more JS-heavy than the order history pages, so this
will likely need the `amazon-orders[browser]` Playwright extra rather than
plain HTTP requests.

Finishing this requires driving a real return through amazon.com once to
learn the actual flow, since it can't be inspected from here:

1. Start a return on an eligible order (Your Orders -> Return or replace
   items) and choose a drop-off option (Whole Foods / UPS Store / Kohl's /
   Amazon Locker+) rather than a mailed/printed label - only drop-off
   returns get a scannable QR code.
2. Note the URL(s) reached after confirming the return, and whether the QR
   is on the return confirmation page or requires an extra
   "get QR code" step.
3. Inspect the DOM for the QR image: is it a plain `<img src="...">` that
   can be downloaded straight from the authenticated session, or is it
   drawn client-side (canvas/SVG), which would need the Playwright browser
   session to screenshot instead of a simple GET?
4. Find where "returns in progress" are listed (likely a filter on Your
   Orders, or Your Account -> "Return, refund or replace items" ->
   "Track your return") so `get_returns_in_progress` knows what to poll.
5. Update the stubs below with the real selectors/URLs, following the
   selector-driven pattern `amazon-orders` itself uses (see
   `amazonorders/selectors.py`) so this stays resilient to markup tweaks.
"""

from dataclasses import dataclass

from amazonorders.session import AmazonSession


@dataclass
class ReturnSummary:
    order_number: str
    item_description: str
    return_status: str
    return_details_link: str


def get_returns_in_progress(session: AmazonSession) -> list[ReturnSummary]:
    """List returns that are currently in progress on the account.

    TODO: navigate to the returns tracking page and parse each return card
    into a ReturnSummary, the way amazonorders.orders parses order cards.
    """
    raise NotImplementedError("Returns tracking is not implemented yet. See module docstring.")


def get_return_qr_code(session: AmazonSession, order_number: str) -> bytes:
    """Fetch the drop-off QR code image for a given order's return.

    TODO: open the return detail page for order_number, confirm it's a
    drop-off return (not a mailed label - those have no QR), locate the QR
    image, and return its raw bytes so the caller can send it to Telegram
    as a photo.
    """
    raise NotImplementedError("Returns QR fetching is not implemented yet. See module docstring.")
