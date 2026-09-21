# Telegram Amazon Returns

A personal Telegram bot that polls your own Amazon account for order and
transaction activity, using the unofficial [`amazon-orders`](https://github.com/alexdlaird/amazon-orders)
library (Amazon has no official buyer-facing API). Returns tracking and the
return QR code feature are scaffolded but not implemented yet — see
[Returns QR status](#returns-qr-status).

## Setup

1. Copy `.env.example` to `.env` and fill in your Amazon credentials, your
   Telegram bot token (from [@BotFather](https://t.me/BotFather)), and your
   Telegram chat id (from [@userinfobot](https://t.me/userinfobot)).
2. Build the image:
   ```
   docker compose build
   ```
3. Log in once, interactively, to solve 2FA/CAPTCHA and persist a session:
   ```
   docker compose run --rm bot python -m amazon_telegram_bot.login_cli
   ```
4. Start the bot:
   ```
   docker compose up -d
   ```

The session cookies and the sqlite tracking DB live under `./data`, which is
gitignored and persisted across restarts. If the session ever expires, the
bot will send you a Telegram message telling you to re-run step 3.

## Commands

- `/orders` — orders from the last 30 days currently in "Arriving" status.
- `/orders <year>` — all orders for that year, unfiltered.
- `/delivered` — orders delivered in the last 3 days. Only counts
  deliveries the bot itself observed while polling (a status transition
  to "Delivered"), not ones that already happened before it started.
- `/transactions` — recent account transactions.
- `/status` — actively checks the Amazon login (not just a cached flag),
  reports last successful poll time, and sends `bot.log` as a file.
- `/returns` — currently replies that returns tracking isn't implemented.

The bot also polls in the background (every `POLL_INTERVAL_MINUTES`,
default 30) and pushes a message for any new order, order status change, or
new transaction.

## Returns QR status

Not implemented. Amazon has no returns API, and `amazon-orders` doesn't
cover returns at all, so this needs someone to drive a real return through
amazon.com once and note the actual page/DOM structure — not something
that could be worked out without a live account. `amazon_telegram_bot/returns_qr.py`
has the intended function signatures and a numbered checklist for finishing
it, and `/returns` already exists as a placeholder command wired up to call
into that module once it's implemented.

## Unraid (Community Applications)

The published image is `ghcr.io/bigwebstas/telegram-amazon-orders-returns:latest`,
built automatically by [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)
on every push to `main` — push this repo to GitHub with that workflow enabled
before installing on Unraid, since nothing is published until it runs once.

1. In Unraid, go to **Apps → gear icon → Template Repositories** and add:
   `https://github.com/bigwebstas/Telegram-Amazon-Orders-Returns`
   The app then shows up under **Apps** using [`unraid-template.xml`](unraid-template.xml).
   (Alternatively: **Docker → Add Container**, and paste the template's
   `Repository` value and each `Config` variable in by hand.)
2. Fill in Amazon email/password, Telegram bot token, and Telegram chat id.
   Leave **Data** pointed at its default appdata path — that's where the
   session cookies and sqlite DB persist.
3. Start the container.
4. Open the container's **Console** (click its icon → Console) and run:
   ```
   python -m amazon_telegram_bot.login_cli
   ```
   to solve 2FA/CAPTCHA interactively, the same as the `docker compose run`
   step above.
5. **Restart the container** from the Docker tab. This step matters: the
   main process loads session cookies from disk once at startup, so it won't
   pick up the session `login_cli` just wrote until it restarts.

Note: the template ships with an empty `Icon` field — add one (host it in
this repo and update the `<Icon>` URL) before publishing it anywhere wider
than your own Unraid box, or it'll show as a broken image in Apps.

## Notes

- Only the `TELEGRAM_CHAT_ID` you configure is served; the bot ignores
  messages from any other chat.
- `amazon-orders` scrapes Amazon's website — there's no official API for
  this, so expect it to occasionally need re-login if Amazon changes its
  challenge flow or the session cookie expires.
