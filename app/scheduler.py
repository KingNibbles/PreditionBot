"""Keyed one-shot timers. One timer per key => no duplicate timer tasks, no polling loop."""
import threading, logging
log = logging.getLogger(__name__)

class Scheduler:
    def __init__(self):
        self._timers = {}
        self._lock = threading.Lock()
    def schedule(self, key, delay, fn):
        with self._lock:
            old = self._timers.pop(key, None)
            if old: old.cancel()
            def run():
                with self._lock:
                    if self._timers.get(key) is t: self._timers.pop(key, None)
                try: fn()
                except Exception: log.exception("timer %s failed", key)
            t = threading.Timer(max(0.0, delay), run); t.daemon = True
            self._timers[key] = t; t.start()
    def cancel(self, key):
        with self._lock:
            t = self._timers.pop(key, None)
            if t: t.cancel()
    def cancel_all(self):
        with self._lock:
            for t in self._timers.values(): t.cancel()
            self._timers.clear()
