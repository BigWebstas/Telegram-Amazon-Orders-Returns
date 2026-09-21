FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/bigwebstas/Telegram-Amazon-Orders-Returns"
LABEL org.opencontainers.image.description="Polls an Amazon account for order/transaction activity and pushes updates to Telegram"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY amazon_telegram_bot ./amazon_telegram_bot

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "amazon_telegram_bot.main"]
