from playlist_sync.quota import Quota, QuotaExhausted
from playlist_sync.spotify import parse_entry
from playlist_sync.youtube import parse_duration, video_from_api

import pytest


def test_spotify_entry_new_and_old_shapes():
    track = {"type": "track", "id": "abc", "name": "Tush", "artists": [{"name": "ZZ Top"}], "duration_ms": 137000}
    assert parse_entry({"item": track}).label == "Tush – ZZ Top"
    assert parse_entry({"track": track}).id == "abc"


def test_spotify_entry_skips_local_files_and_episodes():
    assert parse_entry({"item": {"type": "episode", "id": "e", "name": "Pod"}}) is None
    assert parse_entry({"item": {"type": "track", "id": None, "name": "Local", "is_local": True}}) is None
    assert parse_entry({"item": None}) is None


def test_iso_durations():
    assert parse_duration("PT4M34S") == 274
    assert parse_duration("PT1H2M") == 3720
    assert parse_duration("P0D") == 0
    assert parse_duration("") == 0


def test_region_block_and_privacy_make_a_video_unavailable():
    base = {"id": "v", "snippet": {"title": "t", "channelTitle": "c"}, "status": {"privacyStatus": "public"}}
    assert video_from_api(base, "US").available
    blocked = {**base, "contentDetails": {"regionRestriction": {"blocked": ["US"]}}}
    assert not video_from_api(blocked, "US").available
    allowed = {**base, "contentDetails": {"regionRestriction": {"allowed": ["DE"]}}}
    assert not video_from_api(allowed, "US").available
    private = {**base, "status": {"privacyStatus": "private"}}
    assert not video_from_api(private, "US").available


def test_quota_spends_stops_and_resets_daily(tmp_path):
    day = {"value": "2026-11-12"}
    q = Quota(tmp_path / "q.json", budget=250, today=lambda: day["value"])
    q.spend("search")
    q.spend("playlistItems.insert")
    assert q.remaining == 100
    q.spend("search")
    with pytest.raises(QuotaExhausted):
        q.spend("videos.list")
    assert Quota(tmp_path / "q.json", budget=250, today=lambda: day["value"]).used == 250
    day["value"] = "2026-11-13"
    assert q.remaining == 250
