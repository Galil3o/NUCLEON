"""Working-set byte budget for the mail-fetch loop. Pure accounting, no I/O.

The loop must never let the concurrent working set on the reserved volume exceed a hard
cap, and oversized single objects (> cap) must be skipped, never written. The watchdog
that enforces the volume quota / root reserve lives at the live wiring; this class is the
in-process accounting it builds on.
"""
from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(Exception):
    """Raised when an acquire would push the working set past the cap."""


@dataclass
class DiskBudget:
    cap_bytes: int            # hard cap for the concurrent working set
    reserve_bytes: int = 0    # headroom kept free below the cap; really subtracted (not just noted)
    _in_use: int = 0

    def __post_init__(self) -> None:
        if self.cap_bytes <= 0:
            raise ValueError("cap_bytes must be > 0")
        if self.reserve_bytes < 0:
            raise ValueError("reserve_bytes must be >= 0")
        if self.reserve_bytes >= self.cap_bytes:
            raise ValueError("reserve_bytes must be < cap_bytes")

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def effective_cap(self) -> int:
        """The usable cap after keeping the reserve free."""
        return self.cap_bytes - self.reserve_bytes

    @property
    def available(self) -> int:
        return max(0, self.effective_cap - self._in_use)

    def fits(self, n: int) -> bool:
        return 0 <= n <= self.available

    def too_large_ever(self, n: int) -> bool:
        """A single object larger than the usable cap can never be staged -> skip it."""
        return n > self.effective_cap

    def acquire(self, n: int) -> None:
        if n < 0:
            raise ValueError("n must be >= 0")
        if n > self.available:
            raise BudgetExceeded("acquire %d exceeds available %d (cap %d)"
                                 % (n, self.available, self.cap_bytes))
        self._in_use += n

    def release(self, n: int) -> None:
        if n < 0:
            raise ValueError("n must be >= 0")
        self._in_use = max(0, self._in_use - n)
