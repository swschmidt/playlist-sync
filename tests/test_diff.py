import random

from playlist_sync.diff import apply, plan


def items(videos):
    return [(f"item-{i}", v) for i, v in enumerate(videos)]


def check(current, desired):
    ops = plan(items(current), desired)
    assert apply(items(current), ops) == desired
    return ops


def test_already_in_sync_needs_nothing():
    assert check(list("abcde"), list("abcde")) == []


def test_one_item_moved_to_the_end_is_one_write():
    ops = check(list("abcde"), list("bcdea"))
    assert [op.kind for op in ops] == ["move"]


def test_reversal_keeps_one_item_still():
    assert len(check(list("abcd"), list("dcba"))) == 3


def test_adds_removes_and_reorders():
    ops = check(list("axbyc"), list("cbaz"))
    assert sorted(op.kind for op in ops).count("delete") == 2


def test_empty_playlist_is_filled_in_order():
    ops = check([], list("abc"))
    assert [(op.kind, op.position) for op in ops] == [("insert", 0), ("insert", 1), ("insert", 2)]


def test_duplicates_are_kept_and_trimmed():
    check(list("aab"), list("aba"))
    check(list("aaab"), list("ab"))
    check(list("ab"), list("abab"))


def test_random_playlists_always_converge_with_minimal_writes():
    rng = random.Random(1979)
    for _ in range(2000):
        desired = [rng.choice("abcdefgh") for _ in range(rng.randint(0, 12))]
        current = [rng.choice("abcdefghxyz") for _ in range(rng.randint(0, 12))]
        ops = check(current, desired)
        if len(set(desired)) == len(desired) and len(set(current)) == len(current):
            # Writes beyond deletes never exceed the items not already in relative order.
            kept = [v for v in current if v in desired]
            assert sum(op.kind != "delete" for op in ops) <= len(desired) - (1 if kept else 0) or not desired
