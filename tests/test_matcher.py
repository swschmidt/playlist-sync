from playlist_sync.matcher import best, core_title, is_official, search_query
from playlist_sync.spotify import Track
from playlist_sync.youtube import Video

# Candidates recorded from real YouTube searches on 2026-11-03.


def t(title, artist, seconds):
    return Track("x", title, (artist,), seconds * 1000)


def v(vid, title, channel, seconds, views, description=""):
    return Video(vid, title, channel, description, seconds, views)


def test_core_title_drops_versions_and_brackets():
    assert core_title("Summer Breeze - 2004 Remaster") == "Summer Breeze"
    assert core_title("Do You Feel Like We Do - Live") == "Do You Feel Like We Do"
    assert core_title("Slow Ride (Single Version)") == "Slow Ride"
    assert search_query(t("Paranoid - 2012 - Remaster", "Black Sabbath", 168)) == "Black Sabbath Paranoid"


def test_official_channel_forms():
    assert is_official("TheRunawaysVEVO", ("The Runaways",))
    assert is_official("Blues Image - Topic", ("Blues Image",))
    assert is_official("Deep Purple Official", ("Deep Purple",))
    assert is_official("Official Sweet Channel", ("Sweet",))
    assert is_official("OfficialDrJohn", ("Dr. John",))
    assert is_official("The Midnight Special", ("Foghat",), trusted=("The Midnight Special",))
    assert not is_official("Rodrigo Lima", ("War",))
    assert not is_official("Warner Official Fan Club", ("War",))


def test_official_video_beats_official_audio_and_live():
    pick = best(t("Sweet Emotion", "Aerosmith", 274), [
        v("33ClrPlzuVQ", "Aerosmith - Sweet Emotion (Live)", "Aerosmith", 437, 11_803_142),
        v("bHlbryOTgYM", "Aerosmith - Sweet Emotion (Audio)", "Aerosmith", 194, 8_315_715),
        v("82cJgPXU-ik", "Aerosmith - Sweet Emotion", "Aerosmith", 281, 43_285_859),
        v("FVsMbIHfdUE", "Sweet Emotion by Aerosmith REMASTERED", "DrTomoculus", 279, 1_194_832),
    ])
    assert (pick.video_id, pick.tier, pick.confidence) == ("82cJgPXU-ik", "video", "high")


def test_marked_official_video_wins():
    pick = best(t("Paranoid - 2012 - Remaster", "Black Sabbath", 168), [
        v("ti36edmfaZk", "Black Sabbath - Paranoid (1970)", "Beat-Club", 167, 3_513_603),
        v("0qanF-91aJo", 'BLACK SABBATH - "Paranoid" (Official Video)', "Black Sabbath", 172, 406_584_788),
        v("cen1SvpTsYk", 'BLACK SABBATH - "Paranoid" Birmingham 2012 (Live Video)', "Black Sabbath", 274, 107_729_135),
    ])
    assert pick.video_id == "0qanF-91aJo"


def test_official_audio_beats_unofficial_video():
    pick = best(t("Ride Captain Ride", "Blues Image", 225), [
        v("HqFFjoRnltg", "Ride Captain Ride - Blues Image - 1970", "Gene Tourangeau", 200, 1_456_134),
        v("vOKaSr3B_II", "Ride Captain Ride (Single)", "Blues Image - Topic", 226, 5_742_276,
          "Provided to YouTube by Rhino"),
        v("eEQzKjcFHF4", "Ride Captain Ride - Blues Image - w/lyrics", "Keith Sousa", 254, 231_894),
    ])
    assert (pick.video_id, pick.tier) == ("vOKaSr3B_II", "audio")


def test_trusted_tv_channel_counts_as_official_live():
    pick = best(t("Free Ride", "The Edgar Winter Group", 188), [
        v("JAFyvePcR_o", "The Edgar Winter Group - Free Ride (Audio)", "Edgar Winter", 191, 2_844_621),
        v("rTEtdgdfXAo", "Free Ride - The Edgar Winter Group | The Midnight Special", "The Midnight Special",
          212, 748_082),
    ], trusted=("The Midnight Special",))
    assert (pick.video_id, pick.tier, pick.confidence) == ("rTEtdgdfXAo", "live", "high")


def test_lessons_and_unrelated_videos_are_not_matches():
    pick = best(t("Stranglehold", "Ted Nugent", 503), [
        v("MuIhkieg_48", "Learn 'Stranglehold' in 10 Minutes! #tednugent", "All About Guitar Academy", 543, 6_456),
    ])
    assert pick.video_id is None and pick.confidence == "none"


def test_only_wrong_length_fan_upload_is_low_confidence():
    pick = best(t("Balinese", "ZZ Top", 159), [
        v("xyz", "ZZ Top - Balinese (extended jam)", "Some Fan", 600, 1_000),
    ])
    assert pick.confidence == "low"
