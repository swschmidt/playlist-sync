"""Tidy rules (My Shazam Tracks, 2026-11-18)."""
from playlist_sync.tidy import Entry, plan


def e(pos, day, tid, name, artist="A"):
    return Entry(pos, f"2026-01-{day:02d}T00:00:00Z", tid, name, artist)


def test_owned_songs_leave_and_the_earliest_copy_of_each_song_stays():
    entries = [e(0, 5, "x", "Song X"), e(1, 1, "y", "Song Y"), e(2, 2, "y", "Song Y"),
               e(3, 3, "y2", "Song Y"), e(4, 4, "z", "Owned Z"), e(5, 6, "z", "Owned Z"),
               e(6, 7, "r", "Song X - Remix")]
    owned = {"ids": {"z": {"Dirt Road"}}, "keys": {}}
    gone_owned, gone_dupes, kept = plan(entries, owned, dedupe=True)
    assert [x.pos for x, _ in gone_owned] == [4, 5]
    assert sorted(x.pos for x, _ in gone_dupes) == [2, 3]       # same id later, and same song under another id
    assert [x.pos for x in kept] == [0, 1, 6]                   # a remix is a different song


def test_owned_matches_by_title_and_artist_too():
    entries = [e(0, 1, "a", "Loyal", "Chris Brown")]
    owned = {"ids": {}, "keys": {("loyal", "chris brown"): {"Boom"}}}
    gone_owned, _, kept = plan(entries, owned, dedupe=True)
    assert gone_owned[0][1] == ["Boom"] and kept == []


def test_no_dedupe_keeps_repeats():
    entries = [e(0, 1, "y", "Song Y"), e(1, 2, "y", "Song Y")]
    assert len(plan(entries, None, dedupe=False)[2]) == 2
