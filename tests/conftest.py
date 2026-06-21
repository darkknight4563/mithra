"""Shared test helpers."""


class FakeClock:
    """A manually-advanced monotonic clock for deterministic time-based tests."""

    def __init__(self, t: float = 0.0) -> None:
        """Start the clock at time ``t`` seconds."""
        self.t = t

    def __call__(self) -> float:
        """Return the current (frozen) clock value."""
        return self.t

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds``."""
        self.t += seconds
