"""YouTube Data API quota ledger. The quota resets at midnight Pacific time."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

# https://developers.google.com/youtube/v3/determine_quota_cost
COSTS = {
    "search": 100,
    "videos.list": 1,
    "playlists.list": 1,
    "playlists.insert": 50,
    "playlistItems.list": 1,
    "playlistItems.insert": 50,
    "playlistItems.update": 50,
    "playlistItems.delete": 50,
}


class QuotaExhausted(RuntimeError):
    pass


def pacific_today() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


class Quota:
    def __init__(self, path: Path, budget: int, today: Callable[[], str] = pacific_today):
        self.path, self.budget, self._today = path, budget, today
        data = json.loads(path.read_text()) if path.exists() else {}
        self.day: str | None = data.get("day")
        self.used: int = data.get("used", 0)
        self._roll()

    def _roll(self) -> None:
        if self.day != self._today():
            self.day, self.used = self._today(), 0

    @property
    def remaining(self) -> int:
        self._roll()
        return max(self.budget - self.used, 0)

    def can(self, op: str, count: int = 1) -> bool:
        return COSTS[op] * count <= self.remaining

    def spend(self, op: str) -> None:
        if not self.can(op):
            raise QuotaExhausted(f"daily budget of {self.budget} units reached")
        self.used += COSTS[op]
        self._save()

    def exhaust(self) -> None:
        """Google says we're out, whatever the ledger thinks."""
        self._roll()
        self.used = max(self.used, self.budget)
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"day": self.day, "used": self.used}))
