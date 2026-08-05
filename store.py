from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock


class SignalStore:
    def __init__(self, duplicate_cooldown_minutes=45, history_limit=250):
        self.cooldown = timedelta(minutes=duplicate_cooldown_minutes)
        self.items = deque(maxlen=history_limit)
        self.notified = {}
        self.lock = Lock()

    @staticmethod
    def key(signal):
        return f"{signal['symbol']}:{signal['side']}"

    def add(self, signal):
        item = dict(signal)
        item["recorded_at"] = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.items.appendleft(item)

    def should_notify(self, signal):
        key = self.key(signal)
        now = datetime.now(timezone.utc)
        with self.lock:
            last = self.notified.get(key)
        return not last or now-last >= self.cooldown

    def mark_notified(self, signal):
        with self.lock:
            self.notified[self.key(signal)] = datetime.now(timezone.utc)

    def history(self, limit=50):
        with self.lock:
            return list(self.items)[:limit]
