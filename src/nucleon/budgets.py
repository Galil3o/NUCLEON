"""Size/count budgets (R-011): shared types. Overruns are visible per message."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class BudgetExceeded(Exception):
    """A hard size budget was exceeded; the message is rejected, the run goes on."""

    def __init__(self, kind: str, limit: int, observed: int) -> None:
        super().__init__(f"{kind}: {observed} > {limit}")
        self.kind = kind
        self.limit = limit
        self.observed = observed
