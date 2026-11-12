from playlist_sync.spotify import Track
from playlist_sync.sync import adopt
from playlist_sync.youtube import Video


def v(vid, title, channel, seconds):
    return Video(vid, title, channel, duration_s=seconds, views=1)


def test_existing_live_cut_is_kept_even_at_the_wrong_length():
    track = Track("t", "Tuesday's Gone", ("Lynyrd Skynyrd",), 452_000)
    existing = [
        v("a", "Lynyrd Skynyrd - Free Bird (Live)", "Rock Vault", 840),
        v("b", "Lynyrd Skynyrd - Tuesday's Gone (Live) | Winterland 1976", "Rock Vault", 700),
    ]
    assert adopt(track, existing, ()).video_id == "b"


def test_a_different_song_by_the_same_artist_is_not_adopted():
    track = Track("t", "Simple Man", ("Lynyrd Skynyrd",), 357_000)
    existing = [v("a", "Lynyrd Skynyrd - Free Bird (Live)", "Rock Vault", 840)]
    assert adopt(track, existing, ()) is None


def test_youtube_only_songs_stay_unclaimed():
    track = Track("t", "Dirt Road Anthem", ("Jason Aldean",), 229_000)
    existing = [v("a", "Brantley Gilbert - Country Must Be Country Wide", "BrantleyGilbertVEVO", 240)]
    assert adopt(track, existing, ()) is None
