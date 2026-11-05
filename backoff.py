"""per-model adaptive retry backoff for mule."""
import random

# a model that recently hit a rate limit waits this much longer
LIMITED_MULTIPLIER = 2.0


class AdaptiveBackoff:
    # exponential backoff with jitter, per-model rate-limit memory
    def __init__(self, base=1.0, factor=2.0, jitter=0.25,
                 max_delay=60.0, seed=None):
        self.base = float(base)
        self.factor = float(factor)
        self.jitter = float(jitter)
        self.max_delay = float(max_delay)
        self._rng = random.Random(seed)
        self._limited = set()

    def record_rate_limit(self, model):
        # mark the model as recently limited
        self._limited.add(model)

    def record_success(self, model):
        # a good call clears the limited flag
        self._limited.discard(model)

    def is_limited(self, model):
        # True when the model recently rate-limited
        return model in self._limited

    def reset(self):
        # drop all per-model rate-limit state
        self._limited.clear()

    def _plain_delay(self, model, attempt):
        # delay before jitter: exponential base, doubled when limited
        attempt = max(0, int(attempt))
        delay = self.base * (self.factor ** attempt)
        if model in self._limited:
            delay *= LIMITED_MULTIPLIER
        return delay

    def delay_bounds(self, model, attempt):
        # (low, high) seconds delay_for can return, before the cap
        plain = self._plain_delay(model, attempt)
        low = plain * (1.0 - self.jitter)
        high = plain * (1.0 + self.jitter)
        return (low, high)

    def delay_for(self, model, attempt, retry_after=None):
        # seconds to wait: server retry_after wins when present
        if retry_after is not None:
            try:
                value = float(retry_after)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                return value
        low, high = self.delay_bounds(model, attempt)
        if self.jitter > 0:
            delay = self._rng.uniform(low, high)
        else:
            delay = (low + high) / 2.0
        return min(delay, self.max_delay)
