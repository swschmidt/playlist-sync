"""`playlist-sync check`: what a morning routine reports from the night's log."""
from playlist_sync.check import findings

CLEAN = """playlist-sync run 2026-11-17 03:30

== Dazed ==
tracks 31, matched 31
in sync

== P.W. ==
tracks 58, matched 57 (kept from YouTube 0, searches 27, upgrades 0), youtube PLSEWUaZwCNbA
applied 55 of 55 change(s):

quota used today: 5552 of 9000
"""

HELD_MATCHING = """playlist-sync run 2026-11-17 08:58

(held: Boom is matched and planned only; set hold = false to sync it)

== Boom ==
tracks 118, matched 0
stopped: quota (matching); the next run continues

quota used today: 8943 of 9000
"""

HELD_READY = """playlist-sync run 2026-11-18 03:30

(held: Boom is matched and planned only; set hold = false to sync it)

== Boom ==
Spotify: would apply 16 change(s):
  add X at 1 (on YouTube)
would apply 123 change(s):
  insert Y at 2

== tidy: My Shazam Tracks ==
518 -> 400 entries: would remove 118 already in your playlists, 0 duplicate(s)
stopped: 118 removals is over max_remove 25; run `playlist-sync tidy --force`

quota used today: 3064 of 9000
"""

DIED = """playlist-sync run 2026-11-19 03:30

== tidy: My Shazam Tracks ==
286 entries, nothing to remove
error: YouTube sign-in expired or revoked (invalid_grant); run `playlist-sync auth youtube`
"""


def test_clean_run_says_nothing():
    assert findings(CLEAN, "2026-11-17") == []


def test_quota_stop_while_matching_is_not_news():
    assert findings(HELD_MATCHING, "2026-11-17") == []


def test_ready_dry_run_and_a_stopped_tidy_need_a_person():
    out = findings(HELD_READY, "2026-11-18")
    assert any("Boom is held and its dry run is ready (139 changes)" in line for line in out)
    assert any("tidy: My Shazam Tracks: stopped: 118 removals" in line for line in out)


def test_missing_log_and_a_run_that_died():
    assert findings(None, "2026-11-19") == ["playlist-sync: no run logged for 2026-11-19"]
    out = findings(DIED, "2026-11-19")
    assert any("sign-in expired" in line for line in out)
    assert any("didn't finish" in line for line in out)


def test_only_the_last_run_counts():
    assert findings(HELD_READY + "\n" + CLEAN, "2026-11-18") == []
