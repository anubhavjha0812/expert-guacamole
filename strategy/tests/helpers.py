import datetime as dt
import unittest
from unittest.mock import Mock, patch

from strategy.config import TradingConfig


def make_config(**overrides):
    values = dict(api_key="test-only", username="test-only", password="test-only",
                  totp_secret="test-only", lot_size=65, max_trades=3,
                  martingale_lots=[1, 2, 3], option_expiry="22OCT2026",
                  tick_timeframe="FIVE_MINUTE", check_interval=300,
                  trade_start_time=dt.time(9, 15), trade_end_time=dt.time(14, 55))
    values.update(overrides)
    return TradingConfig(**values)


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        # Fail closed if any test accidentally attempts an external connection.
        for name in ("socket.socket.connect", "socket.socket.connect_ex",
                     "socket.create_connection", "urllib.request.urlopen"):
            self.enterContext(patch(name, side_effect=AssertionError("Network access forbidden in tests")))
        self.enterContext(patch("logzero.logger.disabled", True))

    def freeze(self, module, now):
        fake = self.enterContext(patch(f"{module}.dt", wraps=dt))
        fake.datetime = Mock(wraps=dt.datetime)
        fake.datetime.now.return_value = now
        return fake
