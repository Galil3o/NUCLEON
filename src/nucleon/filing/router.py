"""Routing layer: pick the target Drive folder for a staged attachment.

Conservative default (owner decision 2026-11-25): EVERYTHING goes to the flat
Eingangsordner `/my-files/0 NUCLEON INBOX/`; NOTHING auto-lands in case folders.
The rule mechanism exists so that, later and per rule, selected items can be routed into
specific case folders — but every rule is disabled by default and must be switched on
explicitly. A rule only ever applies when ``enabled`` is True.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class Rule:
    name: str
    target: str                                   # e.g. "/my-files/CASES/<case>" (generic placeholder)
    predicate: Callable[[Mapping], bool]
    enabled: bool = False                          # OFF until the owner enables it


@dataclass(frozen=True)
class Router:
    default_target: str                            # the Eingangsordner
    rules: Sequence[Rule] = ()

    def target_for(self, meta: Mapping) -> str:
        """First ENABLED rule whose predicate matches wins; otherwise the default."""
        for r in self.rules:
            if r.enabled:
                try:
                    if r.predicate(meta):
                        return r.target
                except Exception:
                    continue   # a misbehaving rule never diverts to a wrong place
        return self.default_target
