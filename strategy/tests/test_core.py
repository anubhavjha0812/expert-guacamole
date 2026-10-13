import datetime as dt
from unittest.mock import patch

from strategy.clock import get_next_interval_time
from strategy.rate_limiter import RateLimiter
from strategy.tests.helpers import OfflineTestCase, make_config


class ConfigTests(OfflineTestCase):
    def test_runtime_state_is_independent_between_instances(self):
        first, second = make_config(), make_config()
        first.positions_dict["one"] = {"quantity": 65}
        first.trades_taken += 1
        self.assertEqual(second.positions_dict, {})
        self.assertEqual(second.trades_taken, 0)
        self.assertIsNot(first.positions_lock, second.positions_lock)

    def test_initial_state(self):
        config = make_config()
        self.assertTrue(config.first_run_after_candle)
        self.assertIsNone(config.previous_signal)
        self.assertEqual(config.trades_taken, 0)


class ClockTests(OfflineTestCase):
    def test_next_interval_including_hour_and_day_rollover(self):
        for now, interval, expected in [
            (dt.datetime(2026, 10, 13, 9, 16, 30), 5, 210),
            (dt.datetime(2026, 10, 13, 9, 20), 5, 300),
            (dt.datetime(2026, 10, 13, 9, 59, 59, 500000), 5, 0.5),
            (dt.datetime(2026, 10, 13, 23, 59, 30), 5, 30),
            (dt.datetime(2026, 10, 13, 9, 31), 15, 840),
        ]:
            with self.subTest(now=now, interval=interval):
                clock = self.freeze("strategy.clock", now)
                self.assertEqual(get_next_interval_time(interval), expected)
                clock.datetime.now.assert_called_once()


class LimiterTests(OfflineTestCase):
    def test_first_request_does_not_sleep(self):
        limiter = RateLimiter(1, 1)
        with patch("strategy.rate_limiter.time.time", return_value=10), \
                patch("strategy.rate_limiter.time.sleep") as sleep:
            limiter.wait_if_needed()
        sleep.assert_not_called()
        self.assertEqual(limiter.requests, [10])

    def test_second_request_waits_for_remaining_window(self):
        limiter = RateLimiter(1, 1)
        limiter.requests = [10]
        with patch("strategy.rate_limiter.time.time", side_effect=[10.25, 11]), \
                patch("strategy.rate_limiter.time.sleep") as sleep:
            limiter.wait_if_needed()
        sleep.assert_called_once_with(0.75)
        self.assertEqual(limiter.requests[-1], 11)

    def test_expired_requests_are_removed(self):
        limiter = RateLimiter(1, 1)
        limiter.requests = [9, 10]
        with patch("strategy.rate_limiter.time.time", return_value=11), \
                patch("strategy.rate_limiter.time.sleep") as sleep:
            limiter.wait_if_needed()
        sleep.assert_not_called()
        self.assertEqual(limiter.requests, [11])
