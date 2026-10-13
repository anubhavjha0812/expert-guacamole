import datetime as dt
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pandas as pd
from strategy.market_data import MarketDataManager
from strategy.tests.helpers import OfflineTestCase, make_config


INSTRUMENTS = [dict(name="NIFTY", expiry="22OCT2026", strike="2500000.000000",
                    symbol="NIFTY22OCT2625000CE", exch_seg="NFO", token="123")]


class MarketDataTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.broker = Mock()
        self.config = make_config()
        with patch.object(MarketDataManager, "fetch_instrument_list", return_value=INSTRUMENTS):
            self.data = MarketDataManager(self.broker, self.config)
        self.data.rate_limiter = Mock()
        self.freeze("strategy.market_data", dt.datetime(2026, 10, 13, 9, 25))
        self.enterContext(patch("strategy.market_data.time.sleep"))

    def fetch(self):
        return self.data.get_historical_data("123", 6, "FIVE_MINUTE")

    def test_instrument_lookup_and_case_normalization(self):
        self.assertEqual(self.data.get_token("nifty", 25000, "ce"), "123")
        self.assertEqual(self.data.get_trading_symbol("nifty", "22oct2026", 25000, "ce"),
                         "NIFTY22OCT2625000CE")

    def test_instrument_lookup_rejects_mismatches(self):
        for ticker, strike, side in [("BANKNIFTY", 25000, "CE"),
                                     ("NIFTY", 25100, "CE"), ("NIFTY", 25000, "PE")]:
            with self.subTest(ticker=ticker, strike=strike, side=side):
                self.assertIsNone(self.data.get_token(ticker, strike, side))
        self.assertIsNone(self.data.get_trading_symbol("NIFTY", "29OCT2026", 25000, "CE"))
        self.config.option_expiry = "29OCT2026"
        self.assertIsNone(self.data.get_token("NIFTY", 25000, "CE"))

    def test_online_instrument_loading_uses_fake_response(self):
        with patch("strategy.market_data.urllib.request.urlopen",
                   return_value=io.BytesIO(json.dumps(INSTRUMENTS).encode())):
            self.assertEqual(self.data.fetch_instrument_list(), INSTRUMENTS)

    def test_offline_instrument_fallback(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "instruments.json"
            path.write_text(json.dumps(INSTRUMENTS))
            self.config.offline_file_path = str(path)
            with patch("strategy.market_data.urllib.request.urlopen", side_effect=OSError("offline")):
                self.assertEqual(self.data.fetch_instrument_list(), INSTRUMENTS)

    def test_both_instrument_sources_unavailable(self):
        with TemporaryDirectory() as folder:
            self.config.offline_file_path = str(Path(folder) / "missing.json")
            with patch("strategy.market_data.urllib.request.urlopen", side_effect=OSError("offline")):
                with self.assertRaises(RuntimeError):
                    self.data.fetch_instrument_list()

    def test_candle_conversion_and_current_minute_removal(self):
        self.broker.getCandleData.return_value = {"status": True, "data": [
            ["2026-10-13 09:20", "100", "110", "90", "105", "1000"],
            ["2026-10-13 09:25", "105", "115", "95", "110", "2000"]]}
        frame = self.fetch()
        self.assertEqual(len(frame), 1)
        self.assertIsInstance(frame.index, pd.DatetimeIndex)
        self.assertEqual(frame["close"].iloc[0], 105)
        params = self.broker.getCandleData.call_args.args[0]
        self.assertEqual(params["symboltoken"], "123")
        self.assertEqual(params["exchange"], "NFO")
        self.assertEqual(params["fromdate"], "2026-10-07 09:25")

    def test_empty_history(self):
        self.broker.getCandleData.return_value = {"status": True, "data": []}
        self.assertIsNone(self.fetch())

    def test_invalid_response_retries_three_times(self):
        self.broker.getCandleData.return_value = "invalid"
        self.assertIsNone(self.fetch())
        self.assertEqual(self.broker.getCandleData.call_count, 3)

    def test_failed_broker_response(self):
        self.broker.getCandleData.return_value = {"status": False, "message": "rate limit"}
        self.assertIsNone(self.fetch())
        self.assertEqual(self.broker.getCandleData.call_count, 3)

    def test_broker_exception(self):
        self.broker.getCandleData.side_effect = RuntimeError("offline broker")
        self.assertIsNone(self.fetch())
        self.assertEqual(self.broker.getCandleData.call_count, 3)
