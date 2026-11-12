"""Test stand-ins: a scheduler you fire by hand, and an in-memory token store."""


class ManualScheduler:
    def __init__(self): self.timers = {}
    def schedule(self, key, delay, fn): self.timers[key] = (delay, fn)
    def cancel(self, key): self.timers.pop(key, None)
    def cancel_all(self): self.timers.clear()
    def pending(self): return sorted(self.timers)
    def fire(self, key):
        delay, fn = self.timers.pop(key); fn()


class MemoryStore:
    def __init__(self): self.d = {}
    def get(self, k): return self.d.get(k)
    def set(self, k, v): self.d[k] = v
    def delete(self, k): self.d.pop(k, None)
