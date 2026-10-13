import datetime as dt
import os
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values
from strategy.config import TradingConfig
from strategy.tests.helpers import OfflineTestCase


class EnvironmentTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.values = dict(dotenv_values(Path(__file__).resolve().parents[1] / ".env.example"))
        for name in ["ANGEL_API_KEY", "ANGEL_USERNAME", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"]:
            self.values[name] = "test-only"

    def test_example_preserves_original_parameters(self):
        config = TradingConfig.from_env(self.values)
        self.assertEqual(config.lot_size, 65)
        self.assertEqual(config.max_trades, 3)
        self.assertEqual(config.martingale_lots, [1, 1, 1])
        self.assertEqual(config.option_expiry, "22SEP2026")
        self.assertEqual(config.tick_timeframe, "FIVE_MINUTE")
        self.assertEqual(config.check_interval, 300)
        self.assertEqual(config.trade_start_time, dt.time(1, 0))
        self.assertEqual(config.trade_end_time, dt.time(14, 55))
        self.assertEqual(config.supertrend_period, 10)
        self.assertEqual(config.supertrend_multiplier, 3)

    def test_process_environment_is_used_by_default(self):
        with patch.dict(os.environ, self.values, clear=True):
            self.assertEqual(TradingConfig.from_env().lot_size, 65)

    def test_parameters_can_be_changed_without_editing_code(self):
        self.values.update(LOT_SIZE="50", MAX_TRADES="2", MARTINGALE_LOTS="[2,3]",
                           OPTION_EXPIRY="22oct2026", TICK_TIMEFRAME="fifteen_minute",
                           CHECK_INTERVAL="900", TRADE_START_TIME="09:15",
                           TRADE_END_TIME="15:00", SUPERTREND_PERIOD="7",
                           SUPERTREND_MULTIPLIER="2.5", OFFLINE_FILE_PATH="local.json",
                           INSTRUMENT_LIST_URL="https://example.invalid/instruments.json")
        config = TradingConfig.from_env(self.values)
        self.assertEqual((config.lot_size, config.max_trades, config.martingale_lots), (50, 2, [2, 3]))
        self.assertEqual((config.tick_timeframe, config.check_interval), ("FIFTEEN_MINUTE", 900))
        self.assertEqual((config.supertrend_period, config.supertrend_multiplier), (7, 2.5))
        self.assertEqual(config.option_expiry, "22OCT2026")
        self.assertEqual(config.trade_start_time, dt.time(9, 15))
        self.assertEqual(config.trade_end_time, dt.time(15, 0))
        self.assertEqual(config.offline_file_path, "local.json")
        self.assertEqual(config.instrument_list_url, "https://example.invalid/instruments.json")

    def test_missing_values_are_reported_without_exposing_credentials(self):
        self.values["ANGEL_PASSWORD"] = "secret-do-not-print"
        del self.values["LOT_SIZE"]
        with self.assertRaises(ValueError) as caught:
            TradingConfig.from_env(self.values)
        self.assertIn("LOT_SIZE", str(caught.exception))
        self.assertNotIn("secret-do-not-print", str(caught.exception))

    def test_invalid_values_are_rejected(self):
        for name, value in [
            ("LOT_SIZE", "0"), ("LOT_SIZE", "abc"), ("MAX_TRADES", "-1"),
            ("MARTINGALE_LOTS", "[1]"), ("MARTINGALE_LOTS", "[1,0,1]"),
            ("MARTINGALE_LOTS", "[true,1,1]"), ("MARTINGALE_LOTS", "invalid"),
            ("OPTION_EXPIRY", "2026-10-22"), ("TRADE_START_TIME", "25:00"),
            ("TRADE_START_TIME", "15:00"), ("TICK_TIMEFRAME", "UNKNOWN"),
            ("CHECK_INTERVAL", "900"), ("SUPERTREND_PERIOD", "0"),
            ("SUPERTREND_MULTIPLIER", "nan"), ("SUPERTREND_MULTIPLIER", "inf"),
            ("SUPERTREND_MULTIPLIER", "-1"), ("SUPERTREND_MULTIPLIER", "abc"),
        ]:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                TradingConfig.from_env(dict(self.values, **{name: value}))
