from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class StepTiming:
    step: int
    name: str
    elapsed_seconds: float


class StepTimer:
    def __init__(self) -> None:
        self._items: list[StepTiming] = []

    def run(self, step: int, name: str, fn):
        start = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - start
        self._items.append(StepTiming(step=step, name=name, elapsed_seconds=elapsed))
        return result, elapsed

    def as_list(self) -> list[StepTiming]:
        return list(self._items)
