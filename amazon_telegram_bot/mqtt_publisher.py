"""Publishes order/return/delivery counts to Home Assistant via MQTT.

Entirely optional - every function here is only called if MQTT_HOST is
configured (see config.py). With it unset, connect() returns None and
nothing else in this module runs.

Uses Home Assistant's MQTT Discovery so the three sensors below appear
automatically under one device in HA, with no manual configuration.yaml
entries needed: https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
"""

import json
import logging

import paho.mqtt.client as mqtt

from amazon_telegram_bot.config import Config

logger = logging.getLogger(__name__)

_DEVICE_ID = "amazon_orders_returns_bot"

_SENSORS = [
    {
        "key": "active_orders",
        "name": "Active Orders",
        "icon": "mdi:package-variant",
        "unit": "orders",
    },
    {
        "key": "returns_in_progress",
        "name": "Returns In Progress",
        "icon": "mdi:package-up",
        "unit": "returns",
    },
    {
        "key": "deliveries_last_3_days",
        "name": "Deliveries (Last 3 Days)",
        "icon": "mdi:package-check",
        "unit": "packages",
    },
]


def _status_topic(config: Config) -> str:
    return f"{config.mqtt_topic_prefix}/status"


def connect(config: Config) -> mqtt.Client | None:
    """Connect to the configured MQTT broker, or return None if MQTT isn't
    configured (or the broker can't be reached - this is best-effort and
    should never block the bot's core Telegram functionality)."""
    if not config.mqtt_host:
        return None

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="amazon-orders-returns-bot")
    if config.mqtt_username:
        client.username_pw_set(config.mqtt_username, config.mqtt_password)
    # Lets HA show the sensors as unavailable (not just stale) if the bot
    # goes down without a clean disconnect.
    client.will_set(_status_topic(config), payload="offline", retain=True)

    try:
        client.connect(config.mqtt_host, config.mqtt_port)
    except OSError:
        logger.warning(
            "Could not connect to MQTT broker at %s:%s - MQTT sensors disabled this run.",
            config.mqtt_host, config.mqtt_port, exc_info=True,
        )
        return None

    client.loop_start()
    client.publish(_status_topic(config), payload="online", retain=True)
    return client


def publish_discovery(client: mqtt.Client, config: Config) -> None:
    """Publish retained HA MQTT Discovery config payloads for all three
    sensors, grouped under one device. Safe to call every startup -
    retained + idempotent, so HA just picks up the same config again."""
    device = {
        "identifiers": [_DEVICE_ID],
        "name": "Amazon Orders & Returns Bot",
        "manufacturer": "Telegram-Amazon-Orders-Returns",
    }
    for sensor in _SENSORS:
        payload = {
            "name": sensor["name"],
            "unique_id": f"{_DEVICE_ID}_{sensor['key']}",
            "state_topic": f"{config.mqtt_topic_prefix}/{sensor['key']}/state",
            "availability_topic": _status_topic(config),
            "payload_available": "online",
            "payload_not_available": "offline",
            "icon": sensor["icon"],
            "unit_of_measurement": sensor["unit"],
            "device": device,
        }
        topic = f"homeassistant/sensor/{_DEVICE_ID}/{sensor['key']}/config"
        client.publish(topic, payload=json.dumps(payload), retain=True)


def publish_counts(
    client: mqtt.Client,
    config: Config,
    active_orders: int,
    returns_in_progress: int,
    deliveries_last_3_days: int,
) -> None:
    """Publish the three current counts (retained, so HA has a value
    immediately on restart rather than waiting for the next poll)."""
    values = {
        "active_orders": active_orders,
        "returns_in_progress": returns_in_progress,
        "deliveries_last_3_days": deliveries_last_3_days,
    }
    for key, value in values.items():
        client.publish(f"{config.mqtt_topic_prefix}/{key}/state", payload=str(value), retain=True)
