import numpy as np
import pandas as pd

from strategy.signals import SignalGenerator
from strategy.tests.helpers import OfflineTestCase


def candles(descending=False):
    close = np.linspace(200, 100, 80) if descending else np.linspace(100, 200, 80)
    return pd.DataFrame(dict(open=close - 0.2, high=close + 1, low=close - 1,
                             close=close, volume=np.full(80, 1000)))


class SignalTests(OfflineTestCase):
    def test_missing_ohlc_columns_raise(self):
        for missing in ("high", "low", "close"):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                SignalGenerator.calculate_supertrend(candles().drop(columns=missing))

    def test_actual_indicator_output_on_rising_and_falling_data(self):
        for descending, direction in [(False, 1), (True, -1)]:
            with self.subTest(descending=descending):
                result = SignalGenerator.calculate_supertrend(candles(descending))
                self.assertEqual(len(result), 80)
                self.assertTrue({"supertrend", "direction_signal", "final_lower_band",
                                 "final_upper_band"}.issubset(result.columns))
                self.assertEqual(result["direction_signal"].iloc[-1], direction)
                self.assertTrue(np.isfinite(result["supertrend"].iloc[-1]))

    def test_custom_integer_parameters(self):
        result = SignalGenerator.calculate_supertrend(candles(), period=7, multiplier=2)
        self.assertIn("direction_signal", result.columns)
        self.assertEqual(result["direction_signal"].iloc[-1], 1)

    def test_float_multiplier_column_names(self):
        result = SignalGenerator.calculate_supertrend(candles(), multiplier=2.5)
        self.assertIn("direction_signal", result.columns)
