"""Configuration and shared runtime state from the original script."""
import datetime as dt
import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

@dataclass
class TradingConfig:
    # Non-default arguments
    api_key: str
    username: str
    password: str
    totp_secret: str
    lot_size: int
    max_trades: int
    martingale_lots: List[int]
    option_expiry: str
    tick_timeframe: str
    check_interval: int
    trade_start_time: dt.time
    trade_end_time: dt.time
    
    # Default arguments
    trades_taken: int = 0
    first_run_after_candle: bool = True
    previous_signal: int = None
    instrument_list_url: str = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    offline_file_path: str = r'AlgoDxA/A/strategy-script/dos-spot/instrument_list.json'
    positions_dict: Dict[str, Any] = field(default_factory=dict)
    positions_lock: threading.Lock = field(default_factory=threading.Lock)
    supertrend_period: int = 10
    supertrend_multiplier: float = 3

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None):
        """Parse environment configuration without loading files or opening a session."""
        env = os.environ if environ is None else environ
        names = ["ANGEL_API_KEY", "ANGEL_USERNAME", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET",
                 "LOT_SIZE", "MAX_TRADES", "MARTINGALE_LOTS", "OPTION_EXPIRY",
                 "TICK_TIMEFRAME", "CHECK_INTERVAL", "TRADE_START_TIME", "TRADE_END_TIME",
                 "SUPERTREND_PERIOD", "SUPERTREND_MULTIPLIER"]
        missing = [name for name in names if not env.get(name, "").strip()]
        if missing:
            raise ValueError("Missing environment variables: " + ", ".join(missing))

        def positive_int(name):
            try:
                value = int(env[name])
            except ValueError:
                raise ValueError(f"{name} must be a positive integer") from None
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            return value

        def trading_time(name):
            try:
                return dt.datetime.strptime(env[name].strip(), "%H:%M").time()
            except ValueError:
                raise ValueError(f"{name} must be a valid HH:MM time") from None

        lot_size = positive_int("LOT_SIZE")
        max_trades = positive_int("MAX_TRADES")
        period = positive_int("SUPERTREND_PERIOD")
        interval = positive_int("CHECK_INTERVAL")
        try:
            lots = json.loads(env["MARTINGALE_LOTS"])
        except (ValueError, TypeError):
            raise ValueError("MARTINGALE_LOTS must be a JSON list of positive integers") from None
        if not isinstance(lots, list) or any(type(x) is not int or x <= 0 for x in lots):
            raise ValueError("MARTINGALE_LOTS must be a JSON list of positive integers")
        if len(lots) < max_trades:
            raise ValueError("MARTINGALE_LOTS must cover MAX_TRADES")
        expiry = env["OPTION_EXPIRY"].strip().upper()
        try:
            dt.datetime.strptime(expiry, "%d%b%Y")
        except ValueError:
            raise ValueError("OPTION_EXPIRY must use DDMMMYYYY format") from None
        timeframe = env["TICK_TIMEFRAME"].strip().upper()
        intervals = {"ONE_MINUTE": 60, "THREE_MINUTE": 180, "FIVE_MINUTE": 300,
                     "TEN_MINUTE": 600, "FIFTEEN_MINUTE": 900, "THIRTY_MINUTE": 1800,
                     "ONE_HOUR": 3600}
        if intervals.get(timeframe) != interval:
            raise ValueError("CHECK_INTERVAL in seconds must match TICK_TIMEFRAME")
        start, end = trading_time("TRADE_START_TIME"), trading_time("TRADE_END_TIME")
        if start >= end:
            raise ValueError("TRADE_START_TIME must precede TRADE_END_TIME")
        try:
            multiplier = float(env["SUPERTREND_MULTIPLIER"])
        except ValueError:
            raise ValueError("SUPERTREND_MULTIPLIER must be a finite positive number") from None
        if not 0 < multiplier < float("inf"):
            raise ValueError("SUPERTREND_MULTIPLIER must be a finite positive number")
        overrides = {}
        for name, key in [("INSTRUMENT_LIST_URL", "instrument_list_url"),
                          ("OFFLINE_FILE_PATH", "offline_file_path")]:
            if env.get(name, "").strip():
                overrides[key] = env[name].strip()
        return cls(api_key=env["ANGEL_API_KEY"], username=env["ANGEL_USERNAME"],
                   password=env["ANGEL_PASSWORD"], totp_secret=env["ANGEL_TOTP_SECRET"],
                   lot_size=lot_size, max_trades=max_trades, martingale_lots=lots,
                   option_expiry=expiry, tick_timeframe=timeframe, check_interval=interval,
                   trade_start_time=start, trade_end_time=end,
                   supertrend_period=period, supertrend_multiplier=multiplier, **overrides)

