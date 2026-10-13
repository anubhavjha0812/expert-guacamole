import datetime as dt
from unittest.mock import Mock, patch

import pandas as pd
from strategy.engine import TradingStrategy
from strategy.tests.helpers import OfflineTestCase, make_config


class EngineTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.config = make_config()
        self.market = Mock(config=self.config)
        self.orders = Mock()
        self.engine = TradingStrategy(self.market, self.orders, self.config)
        self.engine.signal_generator = Mock()
        self.market.get_token.return_value = "123"
        self.market.get_trading_symbol.return_value = "NIFTY22OCT2625000CE"
        self.market.get_historical_data.return_value = pd.DataFrame({"close": [100.0, 105.0]})
        self.orders.place_order.return_value = "fake-order"

    def test_call_and_put_selection_and_quantity_schedule(self):
        for signal, side in [(-1, "CE"), (1, "PE")]:
            with self.subTest(signal=signal):
                self.config.trades_taken = 1
                self.assertTrue(self.engine.execute_trade(signal, 25000))
                self.market.get_token.assert_called_with("NIFTY", 25000, side)
                self.assertEqual(self.orders.place_order.call_args.kwargs["quantity"], 130)
                self.assertEqual(self.orders.place_order.call_args.kwargs["transaction_type"], "SELL")
                self.assertEqual(self.config.trades_taken, 2)

    def test_missing_instrument_prevents_submission(self):
        self.market.get_token.return_value = None
        self.assertFalse(self.engine.execute_trade(-1, 25000))
        self.orders.place_order.assert_not_called()

    def test_missing_history_prevents_submission(self):
        self.market.get_historical_data.return_value = None
        self.assertFalse(self.engine.execute_trade(-1, 25000))
        self.orders.place_order.assert_not_called()

    def test_failed_submission_does_not_increment_trade_count(self):
        self.orders.place_order.return_value = None
        self.assertFalse(self.engine.execute_trade(-1, 25000))
        self.assertEqual(self.config.trades_taken, 0)

    def test_signal_cache_hit_avoids_data_fetch(self):
        self.engine.cache = {"timestamp": 100, "data": (1, 25000)}
        with patch("strategy.engine.time.time", return_value=110):
            self.assertEqual(self.engine.get_nifty_Spot_signal(), (1, 25000))
        self.market.get_historical_data.assert_not_called()

    def test_expired_cache_refreshes_and_rounds_atm_strike(self):
        self.engine.cache = {"timestamp": 100, "data": (-1, 24000)}
        self.engine.signal_generator.calculate_supertrend.return_value = pd.DataFrame(
            {"close": [25026.0], "supertrend": [24900.0], "direction_signal": [1]})
        with patch("strategy.engine.time.time", return_value=131):
            self.assertEqual(self.engine.get_nifty_Spot_signal(), (1, 25050))
        self.assertEqual(self.engine.cache["timestamp"], 131)
        self.market.get_historical_data.assert_called_once()

    def test_missing_spot_history_returns_no_signal(self):
        self.market.get_historical_data.return_value = None
        self.assertEqual(self.engine.get_nifty_Spot_signal(), (None, None))

    def test_configured_indicator_parameters_reach_spot_and_option_signals(self):
        self.config.supertrend_period = 7
        self.config.supertrend_multiplier = 2.5
        indicator = self.engine.signal_generator.calculate_supertrend
        indicator.return_value = pd.DataFrame(
            {"close": [25026.0], "supertrend": [24900.0], "direction_signal": [1]})
        self.engine.get_nifty_Spot_signal()
        self.assertEqual(indicator.call_args.kwargs, {"period": 7, "multiplier": 2.5})
        indicator.reset_mock()
        self.set_position()
        indicator.return_value = pd.DataFrame({"direction_signal": [-1, 1]})
        self.engine.get_op_signal()
        self.assertEqual(indicator.call_args.kwargs, {"period": 7, "multiplier": 2.5})

    def test_signal_monitor_only_enters_on_change_before_cutoff(self):
        self.config.previous_signal = 1
        self.engine.get_nifty_Spot_signal = Mock(return_value=(1, 25000))
        self.engine.execute_trade = Mock(return_value=True)
        self.engine.monitor_signal_changes(dt.time(10))
        self.engine.execute_trade.assert_not_called()
        self.engine.get_nifty_Spot_signal.return_value = (-1, 25000)
        self.engine.monitor_signal_changes(dt.time(15))
        self.engine.execute_trade.assert_not_called()
        self.engine.monitor_signal_changes(dt.time(10))
        self.engine.execute_trade.assert_called_once_with(-1, 25000)
        self.assertEqual(self.config.previous_signal, -1)

    def test_first_run_uses_valid_signal(self):
        self.engine.get_nifty_Spot_signal = Mock(return_value=(1, 25000))
        self.engine.execute_trade = Mock(return_value=True)
        self.engine.wait_for_next_interval = Mock()
        self.engine.handle_first_run()
        self.engine.execute_trade.assert_called_once_with(1, 25000)
        self.assertFalse(self.config.first_run_after_candle)
        self.assertEqual(self.config.previous_signal, 1)

    def test_empty_positions_have_no_exit_signals(self):
        self.assertEqual(self.engine.get_op_signal(), {})
        self.market.get_historical_data.assert_not_called()

    def set_position(self):
        self.config.positions_dict["123"] = dict(strike_price="25000", option_type="CE",
                                                quantity=65, stop_loss=25, entry_price=100.0)

    def test_exit_on_option_signal_reversal(self):
        self.set_position()
        self.engine.signal_generator.calculate_supertrend.return_value = pd.DataFrame(
            {"direction_signal": [-1, 1]})
        decisions = self.engine.get_op_signal()
        self.assertEqual(decisions["123"]["exit_price"], 105.0)
        self.assertEqual(decisions["123"]["quantity"], 65)

    def test_stoploss_exit_and_no_exit_at_threshold(self):
        self.set_position()
        self.engine.signal_generator.calculate_supertrend.return_value = pd.DataFrame(
            {"direction_signal": [1, 1]})
        for price, exits in [(125.0, False), (126.0, True)]:
            with self.subTest(price=price):
                self.market.get_historical_data.return_value = pd.DataFrame({"close": [100.0, price]})
                self.assertEqual(bool(self.engine.get_op_signal()), exits)

    def test_invalid_or_short_history_skips_exit(self):
        self.set_position()
        for history in [None, pd.DataFrame(), pd.DataFrame({"close": [100.0]})]:
            with self.subTest(history=history):
                self.market.get_historical_data.return_value = history
                self.assertEqual(self.engine.get_op_signal(), {})

    def test_daily_reset_preserves_original_state_semantics(self):
        self.set_position()
        self.config.trades_taken = 2
        self.config.previous_signal = -1
        self.config.first_run_after_candle = False
        self.engine.reset_daily_states()
        self.assertEqual(self.config.trades_taken, 0)
        self.assertIsNone(self.config.previous_signal)
        self.assertTrue(self.config.first_run_after_candle)
        self.assertEqual(self.config.positions_dict, {})

    def test_wait_during_session_uses_interval_and_jitter(self):
        self.freeze("strategy.engine", dt.datetime(2026, 10, 13, 10))
        with patch("strategy.engine.get_next_interval_time", return_value=300), \
                patch("strategy.engine.random.uniform", return_value=0.5), \
                patch("strategy.engine.time.sleep") as sleep:
            self.engine.wait_for_next_interval()
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [300, 0.5])

    def test_wait_after_close_uses_next_day_start(self):
        self.freeze("strategy.engine", dt.datetime(2026, 10, 13, 15))
        with patch("strategy.engine.time.sleep") as sleep:
            self.engine.wait_for_next_interval()
        sleep.assert_called_once_with((dt.datetime(2026, 10, 14, 9, 15) -
                                       dt.datetime(2026, 10, 13, 15)).total_seconds())

    def test_trade_limit_is_checked_before_data_access(self):
        for taken in [self.config.max_trades, self.config.max_trades + 1]:
            with self.subTest(taken=taken):
                self.config.trades_taken = taken
                self.assertFalse(self.engine.execute_trade(-1, 25000))
                self.market.get_token.assert_not_called()
                self.market.get_historical_data.assert_not_called()
                self.orders.place_order.assert_not_called()

    def test_missing_sizing_entry_prevents_data_access(self):
        self.config.martingale_lots = [1]
        self.config.trades_taken = 1
        self.assertFalse(self.engine.execute_trade(-1, 25000))
        self.market.get_token.assert_not_called()
        self.orders.place_order.assert_not_called()

    def test_last_allowed_trade_uses_last_sizing_entry(self):
        self.config.trades_taken = self.config.max_trades - 1
        self.assertTrue(self.engine.execute_trade(-1, 25000))
        self.assertEqual(self.orders.place_order.call_args.kwargs["quantity"], 195)
        self.assertEqual(self.config.trades_taken, self.config.max_trades)

    def test_failed_entry_does_not_advance_signal_and_can_retry(self):
        self.config.previous_signal = 1
        self.engine.get_nifty_Spot_signal = Mock(return_value=(-1, 25000))
        self.engine.execute_trade = Mock(side_effect=[False, True])
        self.engine.monitor_signal_changes(dt.time(10))
        self.assertEqual(self.config.previous_signal, 1)
        self.engine.monitor_signal_changes(dt.time(10, 5))
        self.assertEqual(self.config.previous_signal, -1)
        self.assertEqual(self.engine.execute_trade.call_count, 2)

    def test_failed_first_entry_remains_pending_until_success(self):
        self.engine.get_nifty_Spot_signal = Mock(return_value=(1, 25000))
        self.engine.execute_trade = Mock(side_effect=[False, True])
        self.engine.wait_for_next_interval = Mock()
        self.engine.handle_first_run()
        self.assertTrue(self.config.first_run_after_candle)
        self.assertIsNone(self.config.previous_signal)
        self.engine.handle_first_run()
        self.assertFalse(self.config.first_run_after_candle)
        self.assertEqual(self.config.previous_signal, 1)

    def test_missing_first_signal_does_not_consume_first_entry(self):
        self.engine.get_nifty_Spot_signal = Mock(return_value=(None, None))
        self.engine.execute_trade = Mock()
        self.engine.wait_for_next_interval = Mock()
        self.engine.handle_first_run()
        self.assertTrue(self.config.first_run_after_candle)
        self.assertIsNone(self.config.previous_signal)
        self.engine.execute_trade.assert_not_called()
