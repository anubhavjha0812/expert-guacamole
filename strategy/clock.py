"""Original candle interval scheduling helper."""
import datetime as dt

def get_next_interval_time(interval_minutes: int = 5) -> float:
    current_time = dt.datetime.now()
    next_interval = ((current_time.minute // interval_minutes) + 1) * interval_minutes
    if next_interval >= 60:
        next_interval = 0
    seconds_to_wait = (
        (next_interval - current_time.minute) * 60
        - current_time.second
        - current_time.microsecond / 1_000_000
    )
    if seconds_to_wait <= 0:
        seconds_to_wait += 3600
    return seconds_to_wait

