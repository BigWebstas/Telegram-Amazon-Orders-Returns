import os

from amazonorders.conf import AmazonOrdersConfig
from amazonorders.exception import AmazonOrdersAuthError
from amazonorders.orders import AmazonOrders
from amazonorders.session import AmazonSession
from amazonorders.transactions import AmazonTransactions

from amazon_telegram_bot.config import Config


class SessionNotReady(Exception):
    """Raised when the persisted Amazon session is missing or expired.

    Recovery is to re-run login_cli.py interactively to solve 2FA/CAPTCHA
    and persist a fresh session under Config.amazon_config_dir.
    """


class AmazonClient:
    def __init__(self, config: Config):
        self._config = config
        self._amazon_config = AmazonOrdersConfig(
            config_path=os.path.join(config.amazon_config_dir, "config.yml"),
            data={
                "cookie_jar_path": os.path.join(config.amazon_config_dir, "cookies.json"),
                "output_dir": os.path.join(config.amazon_config_dir, "output"),
            },
        )
        self._session = AmazonSession(
            config.amazon_email,
            config.amazon_password,
            config=self._amazon_config,
        )

    @property
    def session(self) -> AmazonSession:
        return self._session

    def ensure_logged_in(self) -> None:
        if self._session.is_authenticated:
            return
        try:
            self._session.login()
        except (AmazonOrdersAuthError, EOFError) as exc:
            # EOFError covers the case where the session needs a fresh
            # interactive challenge (2FA/CAPTCHA) but is running headless
            # in the background poller, where stdin isn't available.
            raise SessionNotReady(
                "Amazon session is missing or expired. Run login_cli.py "
                "interactively to reauthenticate."
            ) from exc

    def fetch_recent_orders(self, time_filter: str = "last30"):
        self.ensure_logged_in()
        return AmazonOrders(self._session).get_order_history(time_filter=time_filter)

    def fetch_orders_for_year(self, year: int):
        self.ensure_logged_in()
        return AmazonOrders(self._session).get_order_history(year=year)

    def fetch_transactions(self):
        self.ensure_logged_in()
        return AmazonTransactions(self._session).get_transactions()
