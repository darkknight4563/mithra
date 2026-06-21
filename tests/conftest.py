"""Shared test helpers."""


class FakeClock:
    """A manually-advanced monotonic clock for deterministic time-based tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
