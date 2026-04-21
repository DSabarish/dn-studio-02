from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class StepTiming:
    step: int
    name: str
    elapsed_seconds: float
    started_at_utc: str
    ended_at_utc: str
    success: bool
    error: str | None = None


class StepTimer:
    def __init__(self) -> None:
        self._items: list[StepTiming] = []

    def run(self, step: int, name: str, fn):
        started_at = datetime.now(timezone.utc)
        start = time.perf_counter()
        try:
            result = fn()
        except Exception as exc:
            elapsed = time.perf_counter() - start
            ended_at = datetime.now(timezone.utc)
            self._items.append(
                StepTiming(
                    step=step,
                    name=name,
                    elapsed_seconds=elapsed,
                    started_at_utc=started_at.isoformat(),
                    ended_at_utc=ended_at.isoformat(),
                    success=False,
                    error=str(exc),
                )
            )
            raise

        elapsed = time.perf_counter() - start
        ended_at = datetime.now(timezone.utc)
        self._items.append(
            StepTiming(
                step=step,
                name=name,
                elapsed_seconds=elapsed,
                started_at_utc=started_at.isoformat(),
                ended_at_utc=ended_at.isoformat(),
                success=True,
            )
        )
        return result, elapsed

    def as_list(self) -> list[StepTiming]:
        return list(self._items)
