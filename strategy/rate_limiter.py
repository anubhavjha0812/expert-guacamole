"""Thread-safe request limiting from the original script."""
import threading
import time

class RateLimiter:
    def __init__(self, max_requests: int, time_window: float):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = []
        self.lock = threading.Lock()
                
    def wait_if_needed(self):
        with self.lock:
            now = time.time()
            # Remove requests older than time window
            self.requests = [req for req in self.requests if now - req < self.time_window] #read
            if len(self.requests) >= self.max_requests:
                # Wait until oldest request expires
                sleep_time = self.requests[0] + self.time_window - now
                if sleep_time > 0:
                    time.sleep(sleep_time)            
            self.requests.append(time.time())           #write

