"""Broker order submission, position tracking, and risk calculations."""
import datetime as dt
import time
from typing import Optional
import pandas as pd
from logzero import logger
from .config import TradingConfig
from .rate_limiter import RateLimiter

class OrderManager:
    def __init__(self, smart_api, config: TradingConfig):
        self.smart_api = smart_api
        self.config = config
        self.rate_limiter = RateLimiter(max_requests=1, time_window=1.0)

    def _fetch_with_retry(self, api_call, description: str, max_retries: int = 3, retry_delay: float = 1.0):
        for attempt in range(max_retries):
            try:
                self.rate_limiter.wait_if_needed()
                return api_call()
            except Exception as e:
                logger.error(f"Error fetching {description}: {e}")
                time.sleep(retry_delay * (2 ** attempt))
        logger.error(f"Failed to fetch {description} after {max_retries} attempts")
        return pd.DataFrame()

    def manage_position(self, token, strike_price, option_type, transaction_type, quantity,limit_price,initial_sl,target):
            with self.config.positions_lock:
                key = str(token)    # Using token as a unique key
                if transaction_type == "SELL":
                    # If the token is not already in the dictionary, add the entry
                    if key not in self.config.positions_dict:
                        self.config.positions_dict[key] = {
                            "strike_price": strike_price,
                            "option_type": option_type,
                            "quantity": quantity,
                            "transaction_type": "SELL",
                            "entry_price": limit_price,
                            "stop_loss": initial_sl,
                            "target_price": target
                        }
                        logger.info(f"Added: {key} -> {self.config.positions_dict[key]}")
                    else:
                        logger.info(f"Token {key} already present. No action taken.")

                elif transaction_type == "BUY":
                    # If a "BUY" order comes for an existing "SELL" position, remove that entry
                    if key in self.config.positions_dict:
                        del self.config.positions_dict[key]
                        logger.info(f"Removed: {key}")
                    else:
                        logger.error(f"Token {key} not found for removal. No action taken.")

    def place_order(self, token: str, trading_symbol: str, transaction_type: str, 
                quantity: int, limit_price: float) -> Optional[str]:
        try:
            initial_sl = self.calculate_stoploss(limit_price)
            target = self.calculate_target(limit_price)
            strike_price = trading_symbol[-7:-2]
            option_type = trading_symbol[-2:]
            
            n_order_params = {
                "variety": "NORMAL",
                "tradingsymbol": trading_symbol,
                "symboltoken": token,
                "transactiontype": transaction_type,
                "exchange": "NFO",
                "ordertype": "LIMIT",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "price": limit_price,
                "squareoff": "0",
                "stoploss": "0",
                "quantity": quantity,
                "ordertag": "dos",
            }         
            order_id = self.smart_api.placeOrder(n_order_params)
            if not isinstance(order_id, str) or not order_id.strip():
                logger.error("Order submission did not return a valid order ID")
                return None
            logger.debug(f"Order placed: ID = {order_id}, Price={limit_price}, SL={initial_sl}, Target={target}")
            self.manage_position(token, strike_price, option_type, transaction_type, quantity,limit_price,initial_sl,target)
            return order_id
        
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return None
    
    def exit_order(self,token,trading_symbol,transaction_type, opt_type: str,quantity, exit_price: int) -> bool:
        try:
            order_id = self.place_order(token, trading_symbol,transaction_type, quantity, exit_price)
            logger.info(f"Exit Order placed for token: {token}, trading_sym: {trading_symbol} at price: {exit_price} ID = {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Exit-Order placement failed: {e}")
            return None

    def calculate_stoploss(self, entry_price: float) -> float:
        dte = (dt.datetime.strptime(self.config.option_expiry, "%d%b%Y").date() - dt.datetime.now().date()).days
        if dte <= 1:
            return round(entry_price) / 2 if dte == 1 else round(entry_price)
        return 25

    def calculate_target(self, entry_price: float) -> float:
        dte = (dt.datetime.strptime(self.config.option_expiry, "%d%b%Y").date() - dt.datetime.now().date()).days
        if dte <= 1:
            return round(entry_price) * 0.7 if dte == 1 else round(entry_price) * 0.9
        return 50

    def get_positions(self) -> pd.DataFrame:
        def _call():
            response = self.smart_api.position()
            if response and "data" in response:
                positions = pd.DataFrame(response["data"])
                return positions if not positions.empty else pd.DataFrame()
            return pd.DataFrame()
        return self._fetch_with_retry(_call, "positions")

    def get_orderbook(self):
        def _call():
            response = self.smart_api.orderBook()
            if response and "data" in response:
                return pd.DataFrame(response["data"])
            raise ValueError("No valid data in the response from orderBook API.")
        return self._fetch_with_retry(_call, "order book")

    def get_open_and_rejected_orders(self):
        try:
            df = self.get_orderbook()
            if df.empty:
                print("Order book is empty.")
                return pd.DataFrame(), pd.DataFrame()
            open_orders = df[df["orderstatus"] == "open"]
            rejected_orders = df[df["orderstatus"] == "rejected"]
            return open_orders, rejected_orders
        except Exception as e:
            print(f"Error fetching open/rejected orders: {e}")
            return pd.DataFrame(), pd.DataFrame()

