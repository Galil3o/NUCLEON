"""Byte-bounded batching for the mail-fetch loop.

Groups items so each batch's cumulative size stays <= cap_bytes. A single item larger
than the cap can never fit the working set, so it is emitted as a ``SkipOversize`` marker
(to be logged and left untouched) rather than forced into a batch. Order is preserved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Union


@dataclass(frozen=True)
class Item:
    id: str
    size: int


@dataclass(frozen=True)
class Batch:
    items: tuple

    @property
    def total(self) -> int:
        return sum(i.size for i in self.items)


@dataclass(frozen=True)
class SkipOversize:
    item: Item


def plan_batches(items: Iterable[Item], cap_bytes: int) -> Iterator[Union[Batch, SkipOversize]]:
    if cap_bytes <= 0:
        raise ValueError("cap_bytes must be > 0")
    cur: list = []
    cur_total = 0
    for it in items:
        if it.size > cap_bytes:
            if cur:
                yield Batch(tuple(cur)); cur = []; cur_total = 0
            yield SkipOversize(it)
            continue
        if cur_total + it.size > cap_bytes:
            yield Batch(tuple(cur)); cur = []; cur_total = 0
        cur.append(it); cur_total += it.size
    if cur:
        yield Batch(tuple(cur))
