"""Instrument discovery and historical candle retrieval."""
import datetime as dt
import json
import time
import urllib.request
from typing import Any, Dict, List, Optional
import pandas as pd
from logzero import logger
from .config import TradingConfig
from .rate_limiter import RateLimiter

class MarketDataManager:
    def __init__(self, smart_api, config: TradingConfig):
        self.smart_api = smart_api
        self.config = config
        self.instrument_list = self.fetch_instrument_list()
        # Initialize rate limiter (3 requests per second)
        self.rate_limiter = RateLimiter(max_requests=1, time_window=1.0)

    def fetch_instrument_list(self) -> List[Dict[str, Any]]:
        try:
            logger.info("Attempting to fetch instrument list online...")
            response = urllib.request.urlopen(self.config.instrument_list_url, timeout=30)
            return json.loads(response.read())
        except Exception as e:
            logger.error(f"Error fetching online instrument list: {e}")
            try:
                with open(self.config.offline_file_path, 'r') as file:
                    logger.info("Fetching instrument list offline...")
                    return json.loads(file.read())
            except Exception as file_error:
                logger.error(f"Error reading offline file: {file_error}")
                raise RuntimeError("Failed to fetch instrument list from both sources")


    def get_historical_data(self, token: str, duration: int, interval: str, exchange = "NFO") -> Optional[pd.DataFrame]:   
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": (dt.datetime.now() - dt.timedelta(days=duration)).strftime("%Y-%m-%d %H:%M"),
            "todate": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        max_retries = 3
        retry_delay = 1  # seconds
        for attempt in range(max_retries):
            try:
                # Apply rate limiting
                self.rate_limiter.wait_if_needed() #instantinated by constructor
                # Fetch data
                data = self.smart_api.getCandleData(params)
                if not isinstance(data, dict):
                    logger.error(f"Invalid response format for token {token}")
                    continue  
                if data.get("status"):
                    try:
                        df = pd.DataFrame(
                            data["data"],
                            columns=["date", "open", "high", "low", "close", "volume"])
                        # Validate data
                        if df.empty:
                            logger.warning(f"Empty dataset received for token {token}")
                            return None
                            
                        required_columns = ["date", "open", "high", "low", "close", "volume"]
                        if not all(col in df.columns for col in required_columns):
                            logger.error(f"Missing required columns for token {token}")
                            return None
                        
                        # Convert date and set index
                        df["date"] = pd.to_datetime(df["date"])
                        df.set_index("date", inplace=True)
                        
                        ommit = df.index[-1].minute
                        if dt.datetime.now().minute == ommit:
                            df = df.iloc[:-1] # Remove last row so that we get interval less

                        # Convert numeric columns
                        numeric_columns = ["open", "high", "low", "close", "volume"]
                        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')
                        
                        # Check for invalid values
                        if df.isnull().any().any():
                            logger.warning(f"NaN values found in data for token {token}")
                        
                        # logger.info(f"From get_historical_data(), Historical data fetched successfully for token {token}.")
                        return df
                        
                    except Exception as e:
                        logger.error(f"Error processing data for token {token}: {str(e)}")
                        continue
                else:
                    error_msg = data.get('message', 'Unknown error')
                    logger.error(f"Failed to fetch historical data for token {token}: {error_msg}")
                    
                    # Check if error is due to rate limiting
                    if 'rate limit' in error_msg.lower():
                        time.sleep(retry_delay * (attempt + 1))  # LINEAR backoff
                    continue
                    
            except Exception as e:
                logger.error(f"Error fetching historical data for token {token}: {str(e)}")
                time.sleep(retry_delay*(2**attempt)) #EXPONENTIAL BACKOFF
                continue
        
        logger.error(f"Failed to fetch historical data after {max_retries} attempts for token {token}")
        return None

    def get_token(self, ticker: str, strike: float, opt_type: str) -> Optional[str]:
        expiry = self.config.option_expiry.upper()
        formatted_strike = f"{float(strike) * 100:.6f}"
        formatted_expiry = expiry.upper()
        
        for instrument in self.instrument_list:
            if (
                instrument["name"].upper() == ticker.upper()
                and formatted_expiry == instrument["expiry"]
                and formatted_strike == instrument["strike"]
                and opt_type.upper() == instrument["symbol"][-2:]
                and instrument["exch_seg"] == "NFO"
            ):
                return instrument["token"]
        return None

    def get_trading_symbol(self, ticker: str, expiry: str, strike: float, opt_type: str) -> Optional[str]:
        formatted_strike = f"{float(strike) * 100:.6f}"
        formatted_expiry = expiry.upper()
        
        for instrument in self.instrument_list:
            if (
                instrument["name"].upper() == ticker.upper()
                and formatted_expiry == instrument["expiry"]
                and formatted_strike == instrument["strike"]
                and opt_type.upper() == instrument["symbol"][-2:]
                and instrument["exch_seg"] == "NFO"
            ):
                return instrument["symbol"]
        return None

