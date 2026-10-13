import datetime as dt
from unittest.mock import Mock, patch

from strategy.orders import OrderManager
from strategy.tests.helpers import OfflineTestCase, make_config


class OrderTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.config = make_config()
        self.broker = Mock()
        self.orders = OrderManager(self.broker, self.config)
        self.orders.rate_limiter = Mock()
        self.freeze("strategy.orders", dt.datetime(2026, 10, 13))

    def submit(self, side="SELL"):
        return self.orders.place_order("123", "NIFTY22OCT2625000CE", side, 65, 100.0)

    def test_position_add_duplicate_and_remove(self):
        self.orders.manage_position("123", "25000", "CE", "SELL", 65, 100, 25, 50)
        self.orders.manage_position("123", "25000", "CE", "SELL", 130, 110, 25, 50)
        self.assertEqual(self.config.positions_dict["123"]["quantity"], 65)
        self.orders.manage_position("123", "25000", "CE", "BUY", 65, 90, 25, 50)
        self.assertEqual(self.config.positions_dict, {})

    def test_unknown_exit_does_not_create_position(self):
        self.orders.manage_position("missing", "25000", "CE", "BUY", 65, 90, 25, 50)
        self.assertEqual(self.config.positions_dict, {})

    def test_order_payload_and_local_position(self):
        self.broker.placeOrder.return_value = "fake-order-1"
        self.assertEqual(self.submit(), "fake-order-1")
        payload = self.broker.placeOrder.call_args.args[0]
        self.assertEqual(payload["transactiontype"], "SELL")
        self.assertEqual(payload["quantity"], 65)
        self.assertEqual(payload["price"], 100.0)
        self.assertEqual(payload["exchange"], "NFO")
        self.assertEqual(payload["ordertype"], "LIMIT")
        self.assertEqual(self.config.positions_dict["123"]["strike_price"], "25000")

    def test_broker_exception_does_not_add_position(self):
        self.broker.placeOrder.side_effect = RuntimeError("simulated rejection")
        self.assertIsNone(self.submit())
        self.assertEqual(self.config.positions_dict, {})

    def test_exit_order_delegates_to_buy_order(self):
        with patch.object(self.orders, "place_order", return_value="fake-exit") as submit:
            self.assertEqual(self.orders.exit_order("123", "symbol", "BUY", "CE", 65, 90), "fake-exit")
        submit.assert_called_once_with("123", "symbol", "BUY", 65, 90)

    def test_stoploss_and_target_expiry_branches(self):
        for expiry, stoploss, target in [("22OCT2026", 25, 50),
                                         ("14OCT2026", 50, 70), ("13OCT2026", 100, 90)]:
            with self.subTest(expiry=expiry):
                self.config.option_expiry = expiry
                self.assertEqual(self.orders.calculate_stoploss(100), stoploss)
                self.assertAlmostEqual(self.orders.calculate_target(100), target)

    def test_retry_recovers_and_uses_backoff(self):
        callback = Mock(side_effect=[RuntimeError("temporary"), "ok"])
        with patch("strategy.orders.time.sleep") as sleep:
            self.assertEqual(self.orders._fetch_with_retry(callback, "test"), "ok")
        self.assertEqual(callback.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_retry_exhaustion_returns_empty_frame(self):
        callback = Mock(side_effect=RuntimeError("unavailable"))
        with patch("strategy.orders.time.sleep"):
            self.assertTrue(self.orders._fetch_with_retry(callback, "test").empty)
        self.assertEqual(callback.call_count, 3)

    def test_position_responses(self):
        for response, count in [(None, 0), ({"data": []}, 0),
                                ({"data": [{"symboltoken": "123", "netqty": -65}]}, 1)]:
            with self.subTest(response=response):
                self.broker.position.return_value = response
                self.assertEqual(len(self.orders.get_positions()), count)

    def test_order_status_filtering(self):
        self.broker.orderBook.return_value = {"data": [
            {"orderstatus": "open", "orderid": "a"},
            {"orderstatus": "rejected", "orderid": "b"},
            {"orderstatus": "complete", "orderid": "c"}]}
        opened, rejected = self.orders.get_open_and_rejected_orders()
        self.assertEqual(opened["orderid"].tolist(), ["a"])
        self.assertEqual(rejected["orderid"].tolist(), ["b"])

    def test_empty_orderbook(self):
        self.broker.orderBook.return_value = {"data": []}
        opened, rejected = self.orders.get_open_and_rejected_orders()
        self.assertTrue(opened.empty and rejected.empty)

    def test_invalid_order_id_must_not_create_position(self):
        for response in [None, "", "   ", False, 123, {"status": False}]:
            with self.subTest(response=response):
                self.broker.placeOrder.return_value = response
                self.assertIsNone(self.submit())
                self.assertEqual(self.config.positions_dict, {})

    def test_failed_exit_must_not_remove_position(self):
        self.orders.manage_position("123", "25000", "CE", "SELL", 65, 100, 25, 50)
        self.broker.placeOrder.return_value = None
        self.assertIsNone(self.submit("BUY"))
        self.assertIn("123", self.config.positions_dict)

    def test_dte_uses_calendar_dates_for_stoploss_and_target(self):
        for hour in [0, 12, 23]:
            self.freeze("strategy.orders", dt.datetime(2026, 10, 13, hour))
            for expiry, stoploss, target in [("13OCT2026", 100, 90),
                                             ("14OCT2026", 50, 70),
                                             ("15OCT2026", 25, 50)]:
                with self.subTest(hour=hour, expiry=expiry):
                    self.config.option_expiry = expiry
                    self.assertEqual(self.orders.calculate_stoploss(100), stoploss)
                    self.assertAlmostEqual(self.orders.calculate_target(100), target)
