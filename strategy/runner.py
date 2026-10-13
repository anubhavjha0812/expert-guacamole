"""Application configuration, broker session, and worker startup."""
import threading
from pathlib import Path
from SmartApi import SmartConnect
import pyotp
from dotenv import load_dotenv
from logzero import logger, loglevel, DEBUG
from .config import TradingConfig
from .engine import TradingStrategy
from .market_data import MarketDataManager
from .orders import OrderManager

def main():
    loglevel(DEBUG)
    # Preserve the original script's .env location at the repository root.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    config = TradingConfig.from_env()
    
    smart_api = SmartConnect(api_key=config.api_key)
    # Generate session
    try:
        totp = pyotp.TOTP(config.totp_secret).now()
        session_data = smart_api.generateSession(config.username, config.password, totp)
        if not session_data["status"]:
            logger.error("Session generation failed.")
            return
        logger.info("Session generated successfully")
    except Exception as e:
        logger.error(f"Session generation error: {e}")
        return
    
    try:
        market_data = MarketDataManager(smart_api, config)
        order_manager = OrderManager(smart_api, config)
        strategy = TradingStrategy(market_data, order_manager,config)

        signal_thread = threading.Thread(
            target=strategy.monitor_signals,
            name="SignalMonitor_NF")
        position_thread = threading.Thread(
            target=strategy.monitor_positions,
            name="PositionMonitor_OP")

        signal_thread.start()
        position_thread.start()
        
        signal_thread.join()
        position_thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    finally:
        try:
            smart_api.terminateSession(config.username)
            logger.info("Session terminated successfully")
        except Exception as e:
            logger.error(f"Session termination error: {e}")

