"""Original entry, exit, scheduling, and monitoring orchestration."""
import datetime as dt
import random
import threading
import time
from typing import Optional, Tuple
import pandas as pd
from logzero import logger
from .clock import get_next_interval_time
from .config import TradingConfig
from .signals import SignalGenerator

class TradingStrategy:
    def __init__(self, market_data, order_manager, config: TradingConfig):
        self.market_data = market_data
        self.order_manager = order_manager
        self.signal_generator = SignalGenerator()
        self.interval_minutes = self.market_data.config.check_interval // 60
        self.expiry_date_op = self.market_data.config.option_expiry
        self.config = config

        # Cache dictionary to store cached data and its timestamp
        self.cache = {
            "timestamp": None,
            "data": None,
        }
        self.CACHE_DURATION = 30  #seconds

    def reset_daily_states(self):
        logger.info("Resetting daily states for a new trading day.")
        self.config.first_run_after_candle = True
        self.config.previous_signal = None
        self.config.trades_taken = 0

        with self.config.positions_lock:
            self.config.positions_dict.clear()  #clear the existing dict in place
    
    def wait_for_next_interval(self):
        try:
            now = dt.datetime.now()
            market_closed = now.time() >= self.config.trade_end_time
            # Determine the wait time
            if market_closed:    #market-closed
                # Time until the next trading day (9:15 AM)
                next_day_start = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(9, 15))
                seconds_to_wait = (next_day_start - now).total_seconds()
                log_message = (
                    f"Market closed. Waiting {seconds_to_wait:.2f} seconds "
                    f"until the next trading day at 9:15 AM ({seconds_to_wait / 60:.2f} minutes or {seconds_to_wait / 3600:.2f} hours).")
            else:
                # Time until the next interval
                seconds_to_wait = get_next_interval_time(self.interval_minutes)
                log_message = f"Waiting {seconds_to_wait:.2f} seconds until the next {self.interval_minutes}-minute interval."
            # Log the calculated wait time
            logger.info(log_message)
            # Wait for the calculated time
            time.sleep(seconds_to_wait)
            #ADDING ADDITIONAL JITTER TO TACKLE WITH THE COLLISION WINDOW.
            if not market_closed:
                jitter = random.uniform(0.3, 1.5)
                logger.debug(f"[{threading.current_thread().name}] Jitter: sleeping {jitter:.2f}s extra before next cycle to avoid API rate-limit collisions")
                time.sleep(jitter)

        except KeyboardInterrupt:
            logger.warning("Interrupted while waiting for the next interval.")
        except Exception as e:
            logger.error(f"Error during wait: {e}", exc_info=True)

    def get_nifty_Spot_signal(self) -> Tuple[Optional[int], Optional[float]]:
        try:
            current_time = time.time()

            # Check if cached data is valid
            if self.cache["timestamp"] is not None and (current_time - self.cache["timestamp"] < self.CACHE_DURATION):
                logger.info("Using cached data for Nifty Spot signal.")
                logger.info(f'cached data: {self.cache}')
                return self.cache["data"]

            nifty_spot_token = "99926000"   #spot_token

            # Fetch historical data
            hist_data = self.market_data.get_historical_data(
                nifty_spot_token, duration=6, interval=self.market_data.config.tick_timeframe, exchange = "NSE")
            if hist_data is None:
                return None, None

            # Calculate SuperTrend
            hist_data = self.signal_generator.calculate_supertrend(
                hist_data, period=self.config.supertrend_period,
                multiplier=self.config.supertrend_multiplier)
            available_columns = [col for col in hist_data.columns ]
            logger.info(f"NIFTY Spot's ST :\n{hist_data[available_columns].tail(2)}")
            signal = hist_data["direction_signal"].iloc[-1]
            strike_price = round(hist_data["supertrend"].iloc[-1] / 100) * 100    #otm
            logger.info(f"otm_strike = {strike_price}")
    
            strike_price = round(hist_data["close"].iloc[-1] / 50) * 50 #ATM
            logger.info(f"Live Nifty Spot Signal: {signal}, Strike Price: {strike_price}")

            # Cache the fetched data
            self.cache["timestamp"] = current_time
            self.cache["data"] = (signal, strike_price)

            return signal, strike_price

        except Exception as e:
            logger.error(f"Error getting Nifty Spot signal: {e}")
            return None, None

    def execute_trade(self, signal: int, strike_price: float) -> bool:
        try:
            if self.config.trades_taken >= self.config.max_trades:
                logger.info("Maximum trades for the day reached")
                return False
            if not 0 <= self.config.trades_taken < len(self.config.martingale_lots):
                logger.error("No sizing entry available for the current trade")
                return False
            option_type = "CE" if signal < 0 else "PE"            
            token = self.market_data.get_token("NIFTY", strike_price, option_type)
            trading_symbol = self.market_data.get_trading_symbol(
                "NIFTY", self.market_data.config.option_expiry, strike_price, option_type)
            logger.info(f"From execute trade : token: {token}, trading_symbol: {trading_symbol}")   
            
            if not token or not trading_symbol:
                logger.error("Failed to get token or trading symbol")
                return False

            # Get historical data for option price
            hist_data = self.market_data.get_historical_data(
                token, duration=6, interval=self.market_data.config.tick_timeframe
            )
            if hist_data is None:
                return False

            limit_price = hist_data["close"].iloc[-1]
            quantity = self.market_data.config.lot_size * self.market_data.config.martingale_lots[self.config.trades_taken]
            
            logger.info(f"From execute_trade(): Opt Type : {option_type}, Trading Sym : {trading_symbol}, Lim Price : {limit_price}, Qty : {quantity}")
            
            order_id = self.order_manager.place_order(token=token, trading_symbol=trading_symbol, transaction_type="SELL",
                quantity=quantity,limit_price=limit_price)
            
            if order_id:
                self.config.trades_taken += 1
                logger.info(f"Trade executed successfully. Trades taken: {self.config.trades_taken}")
                return True
                
            return False
        
        except Exception as e:
            logger.error(f"Trade execution error: {e}")
            return False
    
    def get_op_signal(self) -> dict:
        """
        Fetch operational signals by processing current positions and historical market data.
        Returns: dict: A dictionary with tokens as keys and their associated details (strike price, 
                option type, exit price, and quantity) as values.
        """
        try:
            results = {}

            # Check if positions_dict is empty
            if not self.config.positions_dict:
                logger.warning("No positions found in positions_dict")
                return results

            with self.config.positions_lock:
                positions_snapshot = dict(self.config.positions_dict)

            # Iterate through each position in positions_dict
            for token_op, position in positions_snapshot.items():
                try:
                    # Validate each position's format
                    if not isinstance(position, dict):
                        logger.error(f"Invalid position format for token {token_op}")
                        continue

                    # Extract key details from position
                    strike_price = position.get('strike_price')
                    option_type = position.get('option_type')
                    quantity = position.get('quantity')
                    stop_loss = position.get('stop_loss')
                    entry_price = position.get('entry_price')

                    # Ensure required fields are available
                    if not all([strike_price, option_type, stop_loss]):
                        logger.warning(
                            f"Incomplete position details for token {token_op}. "
                            f"strike_price: {strike_price}, option_type: {option_type}, stop_loss: {stop_loss}"
                        )
                        continue

                    logger.info(
                        f"Processing signal for: Token: {token_op}, Strike Price: {strike_price}-{option_type}"
                    )

                    # Fetch historical data for the given token
                    hist_data_op = self.market_data.get_historical_data(
                        token_op, 
                        duration=6, 
                        interval=self.market_data.config.tick_timeframe
                    )
                    if hist_data_op is None or not isinstance(hist_data_op, pd.DataFrame) or hist_data_op.empty:
                        logger.error(f"Invalid or empty historical data for token {token_op}")
                        continue

                    if len(hist_data_op) < 2:
                        logger.warning(f"Insufficient historical data for token {token_op}. Need at least 2 rows.")
                        continue

                    # Calculate SuperTrend for the historical data
                    hist_data_st = self.signal_generator.calculate_supertrend(
                        hist_data_op, period=self.config.supertrend_period,
                        multiplier=self.config.supertrend_multiplier)
                    if hist_data_st is None or not isinstance(hist_data_st, pd.DataFrame):
                        logger.error(f"SuperTrend calculation returned invalid data for token {token_op}")
                        continue

                    if 'direction_signal' not in hist_data_st.columns:
                        logger.error(f"Missing 'direction_signal' column in SuperTrend data for token {token_op}")
                        continue

                    # Check for signal changes
                    try:
                        logger.info(
                            f"SuperTrend of {strike_price}{option_type} : token: {token_op} \n{hist_data_st.tail(2)}"
                        )
                        prec_signal = hist_data_st["direction_signal"].iloc[-2]
                        signal = hist_data_st["direction_signal"].iloc[-1]
                    except IndexError as e:
                        logger.error(f"Failed to access signal data for token {token_op}: {e}")
                        continue

                    # Signal change condition: Exit existing position
                    if signal > prec_signal:
                        try:
                            exit_price = hist_data_op['close'].iloc[-1]
                        except (KeyError, IndexError) as e:
                            logger.error(f"Failed to get exit price for token {token_op}: {e}")
                            continue

                        logger.info(
                            f"Signal change detected - Token: {token_op}, "
                            f"Strike Price: {strike_price}, Option Type: {option_type}, "
                            f"Previous Signal: {prec_signal}, New Signal: {signal}, "
                            f"Exit Price: {exit_price}, Quantity: {quantity}"
                        )
                        results[token_op] = {
                            'strike_price': strike_price,
                            'option_type': option_type,
                            'exit_price': exit_price,
                            'quantity': quantity
                        }
                    else:
                        # Handle stop-loss exit condition for 'buy' signal (1)
                        if prec_signal == 1:
                            try:
                                current_close_price = hist_data_op['close'].iloc[-1]
                            except (KeyError, IndexError) as e:
                                logger.error(f"Failed to get current close price for token {token_op}: {e}")
                                continue

                            # Check if current close price exceeds stop-loss
                            if isinstance(current_close_price, (int, float)) and current_close_price > stop_loss+entry_price:
                                logger.info(
                                    f"Force exit triggered - Token: {token_op}, "
                                    f"Strike Price: {strike_price}, Option Type: {option_type}, "
                                    f"Stop Loss: {stop_loss+entry_price}, Current Close Price: {current_close_price}"
                                )
                                results[token_op] = {
                                    'strike_price': strike_price,
                                    'option_type': option_type,
                                    'exit_price': current_close_price,
                                    'quantity': quantity
                                }
                            else:
                                logger.debug(
                                    f"Buy signal remained active - Token: {token_op}, "
                                    f"Strike Price: {strike_price}, Option Type: {option_type}, "
                                    f"Stop Loss: {stop_loss}, Current Close Price: {current_close_price}"
                                )
                        else:
                            logger.debug(
                                f"No signal change - Token: {token_op}, "
                                f"Strike Price: {strike_price}, Option Type: {option_type}, Quantity: {quantity}, "
                                f"Current Signal: {signal}"
                            )
                except Exception as e:
                    logger.error(f"Error processing token {token_op}: {str(e)}")
                    continue

            return results

        except Exception as e:
            logger.error(f"Critical error in get_op_signals: {str(e)}")
            return {}
        

    def log_and_wait(self, current_time, initial_candle_close_time):
        logger.info("= = = = ========= Monitoring Supertrend Signal of Nifty Spot STARTED ======== == = = =")
        logger.warning(f"{current_time} is less than {initial_candle_close_time}. Waiting...")
        logger.info("= ====  ==== == Monitoring Signal (Nifty Spot) Completed. == ===  ===== =")
        self.wait_for_next_interval()

    def handle_first_run(self):
        signal, strike_price = self.get_nifty_Spot_signal()
        if signal is not None and strike_price is not None:
            logger.info("Executing first trade")
            if self.execute_trade(signal, strike_price):
                self.config.previous_signal = signal
                self.config.first_run_after_candle = False
        self.wait_for_next_interval()

    def monitor_signal_changes(self, current_time_of_day):
        signal, strike_price = self.get_nifty_Spot_signal()
        if signal is None or strike_price is None:
            logger.info("No valid signal detected.")
            return

        if signal != self.config.previous_signal:
            if current_time_of_day < self.config.trade_end_time:
                logger.info("Signal changed; executing trade.")
                if self.execute_trade(signal, strike_price):
                    self.config.previous_signal = signal
            else:
                logger.warning(f"New signal detected at {current_time_of_day}, but it's past trade_end_time. No trade placed.")
        else:
            logger.info("No change in the signal from Nifty Spot.")

    def monitor_signals(self):
        startup_jitter = random.uniform(0.3, 1.5)
        logger.debug(f"[{threading.current_thread().name}] Startup jitter: sleeping {startup_jitter:.2f}s before first cycle")
        time.sleep(startup_jitter)
        while True:
            current_time = dt.datetime.now()
            current_time_of_day = current_time.time()  # Extract once for reuse

            try:
                initial_candle_close_time = (dt.datetime.combine(dt.date.today(), self.config.trade_start_time) +
                                                 dt.timedelta(minutes=self.interval_minutes))

                # Wait if current time is before the first candle closes
                if current_time < initial_candle_close_time:
                    self.log_and_wait(current_time, initial_candle_close_time)
                    continue

                logger.info(">= = = ========== Monitoring Supertrend Signal of Nifty Spot STARTED =========== = = =>")
                
                # Reset daily states if beyond trade end time
                if current_time_of_day >= self.config.trade_end_time:
                    self.reset_daily_states()
                    self.wait_for_next_interval()
                    continue

                # Handle first run after candle
                if self.config.first_run_after_candle:
                    self.handle_first_run()
                    continue

                # Suspend monitoring if max trades reached
                if self.config.trades_taken >= self.config.max_trades:
                    logger.info("Maximum trades for the day reached. Monitoring suspended.")
                    self.wait_for_next_interval()
                    continue

                # Monitor signals for changes
                self.monitor_signal_changes(current_time_of_day)

                logger.info("================= Monitoring Signal (Nifty Spot) Completed. ===========================")
                self.wait_for_next_interval()

            except Exception as e:
                logger.error(f"Error in signal monitoring: {e}", exc_info=True)
                if current_time_of_day >= self.config.trade_end_time:
                    self.reset_daily_states()
                self.wait_for_next_interval()

    def monitor_positions(self):
        startup_jitter = random.uniform(0.3, 1.5)
        logger.debug(f"[{threading.current_thread().name}] Startup jitter: sleeping {startup_jitter:.2f}s before first cycle")
        time.sleep(startup_jitter)
        while True:
            try:
                print("------------------ MONITORING POSITONS Started ----------------------------------")
                positions = self.order_manager.get_positions()
                positions = pd.DataFrame() if positions is None else positions
                
                if positions.empty:
                    logger.info("No active positions.")
                else:
                    position_columns = [
                        'symboltoken', 'tradingsymbol', 'strikeprice',
                        'optiontype', 'buyqty', 'sellqty', 'netqty', 'ltp', 'pnl']
                    available_columns = [col for col in position_columns if col in positions.columns]
                    logger.info(f"Active positions:\n{positions[available_columns]}")

                open_orders, rejected_orders = self.order_manager.get_open_and_rejected_orders()

                open_orders = pd.DataFrame() if open_orders is None else open_orders
                order_columns = ['symboltoken', 'tradingsymbol', 'strikeprice', 'optiontype']  # Initialize order_columns
                if open_orders.empty:
                    logger.info("No orders pending.")
                else:
                    available_columns = [col for col in order_columns if col in open_orders.columns]
                    logger.info(f"Open orders:\n{open_orders[available_columns]}")

                rejected_orders = pd.DataFrame() if rejected_orders is None else rejected_orders
                if rejected_orders.empty:
                    logger.info("No orders rejected.")
                else:
                    available_columns = [col for col in order_columns if col in rejected_orders.columns]
                    logger.info(f"Rejected orders:\n{rejected_orders[available_columns]}")

                # Fetch and process new signals
                # logger.info("# Fetching option signal of supertrend live.")
                new_decisions = self.get_op_signal()
                logger.debug(f"New decisions fetched: {new_decisions}")

                if isinstance(new_decisions, dict) and new_decisions:
                    for token_op, decision in new_decisions.items():
                        if not isinstance(decision, dict):
                            logger.warning(f"Invalid decision format for token {token_op}. Expected dict, got {type(decision)}.")
                            continue

                        # Extract required fields with proper type checking
                        strike_price = decision.get('strike_price')
                        option_type = decision.get('option_type')
                        exit_price = decision.get('exit_price')
                        quantity = decision.get('quantity')
                        

                        # Validate all required fields are present
                        if not all([strike_price, option_type, exit_price]):
                            logger.warning(
                                f"Incomplete decision for token {token_op}. "
                                f"Required fields: strike_price={strike_price}, "
                                f"option_type={option_type}, exit_price={exit_price}"
                            )
                            continue

                        # Generate trading symbol
                        trading_symbol = self.market_data.get_trading_symbol(
                            "NIFTY",
                            self.expiry_date_op,
                            strike_price,
                            option_type
                        )
                        logger.debug(f"Generated trading symbol: {trading_symbol}")

                        if not trading_symbol:
                            logger.error(
                                f"Failed to fetch trading symbol for {strike_price}-{option_type}. "
                                "Skipping..."
                            )
                            continue

                        # Process valid trading signal
                        option_type = trading_symbol[-2:]
                        quantity = quantity # applied martingle.
                        logger.info(
                            f"Signal changed for token {token_op}, {trading_symbol}: "
                            f"Exiting position, strike {strike_price}-{option_type}, Qty: {quantity}.")
                        # Place exit order
                        try:
                            exit_order_id = self.order_manager.exit_order(token_op,trading_symbol,"BUY",
                                option_type,quantity,exit_price)
                            logger.info(f"Exit order placed with ID: {exit_order_id}")
                        except Exception as e:
                            logger.error(f"Failed to place exit order: {e}")
                            continue

                        print("\n")
                else:
                    logger.info("No signal changes detected.")
                print("-------------------------- - - - MONITORING POSITONS ENDED - - - --------------------------")
                self.wait_for_next_interval()

            except Exception as e:
                logger.error(f"Position monitoring error: {e}")
                self.wait_for_next_interval()

