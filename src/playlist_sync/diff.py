"""Turn the current YouTube playlist into the desired order with the fewest writes.

Every write costs 50 quota units, so reordering keeps the longest run of items
that is already in the right relative order and moves only the rest.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

Key = tuple[str, int]  # (video_id, occurrence) so duplicates are tracked separately


@dataclass(frozen=True)
class Op:
    kind: Literal["delete", "insert", "move"]
    video_id: str
    position: int | None = None
    item_id: str | None = None


def _keys(video_ids: list[str]) -> list[Key]:
    seen: Counter[str] = Counter()
    keys = []
    for vid in video_ids:
        seen[vid] += 1
        keys.append((vid, seen[vid]))
    return keys


def _lis(seq: list[int]) -> set[int]:
    """Indices into `seq` of one longest strictly increasing subsequence."""
    tails: list[int] = []  # index into seq of the smallest tail for each length
    prev = [-1] * len(seq)
    for i, value in enumerate(seq):
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if seq[tails[mid]] < value:
                lo = mid + 1
            else:
                hi = mid
        prev[i] = tails[lo - 1] if lo else -1
        if lo == len(tails):
            tails.append(i)
        else:
            tails[lo] = i
    out, i = set(), tails[-1] if tails else -1
    while i != -1:
        out.add(i)
        i = prev[i]
    return out


def plan(current: list[tuple[str, str]], desired: list[str]) -> list[Op]:
    """`current` is (playlist item id, video id) in playlist order; `desired` is video ids in order.

    Deletes come first; insert and move positions are final indices after the
    preceding ops, which is how playlistItems.insert and .update interpret them.
    """
    need = Counter(desired)
    seen: Counter[str] = Counter()
    ops: list[Op] = []
    kept: list[tuple[str, Key]] = []
    for item_id, vid in current:
        if seen[vid] < need[vid]:
            seen[vid] += 1
            kept.append((item_id, (vid, seen[vid])))
        else:
            ops.append(Op("delete", vid, item_id=item_id))

    want = _keys(desired)
    index = {key: i for i, key in enumerate(want)}
    stable = {kept[i][1] for i in _lis([index[key] for _, key in kept])}

    sim: list[tuple[str | None, Key]] = list(kept)

    def find(key: Key) -> int:
        return next((j for j, (_, k) in enumerate(sim) if k == key), -1)

    for i, key in enumerate(want):
        if key in stable:
            continue
        at = find(key)
        prev = find(want[i - 1]) if i else -1
        if at != -1 and at == prev + 1:
            continue  # already sits right after its predecessor
        if at != -1:
            item_id = sim.pop(at)[0]
            prev = find(want[i - 1]) if i else -1
            sim.insert(prev + 1, (item_id, key))
            ops.append(Op("move", key[0], prev + 1, item_id))
        else:
            sim.insert(prev + 1, (None, key))
            ops.append(Op("insert", key[0], prev + 1))
    return ops


def apply(current: list[tuple[str, str]], ops: list[Op]) -> list[str]:
    """Simulate `ops` on `current` and return the resulting video ids (used by tests and dry runs)."""
    rows = list(current)
    for n, op in enumerate(ops):
        if op.kind == "delete":
            rows = [r for r in rows if r[0] != op.item_id]
        elif op.kind == "insert":
            rows.insert(op.position, (f"new-{n}", op.video_id))
        else:
            row = next(r for r in rows if r[0] == op.item_id)
            rows.remove(row)
            rows.insert(op.position, row)
    return [vid for _, vid in rows]
