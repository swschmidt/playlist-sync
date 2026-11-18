"""Quality classes, keep-unless-better, dedupe and the two-way merge (cases from Dirt Road, 2026-11-13)."""
from playlist_sync.matcher import norm, score
from playlist_sync.spotify import Track
from playlist_sync.sync import adopt, merge, youtube_query
from playlist_sync.youtube import Video


def t(title, artist, seconds=200, tid="x"):
    return Track(tid, title, (artist,), seconds * 1000)


def v(vid, title, channel, seconds=200, description=""):
    return Video(vid, title, channel, description, seconds, 1000)


def cls(track, video):
    m = score(track, video)
    return m.cls if m else None


def test_classes():
    toes = t("Toes", "Zac Brown Band", 261)
    assert cls(toes, v("a", "Zac Brown Band - Toes (Official Music Video) | The Foundation", "Zac Brown Band", 280)) == 5
    assert cls(toes, v("b", "Zac Brown Band - Toes (Recorded Live from Southern Ground HQ)", "Zac Brown Band")) == 2
    house = t("House Party", "Sam Hunt")
    assert cls(house, v("c", "Sam Hunt - House Party (Alternate Video)", "SamHuntVEVO")) == 4
    hell = t("Hell Of A View", "Eric Church")
    assert cls(hell, v("d", "Eric Church - Hell Of A View (Official Lyric Video)", "EricChurchVEVO")) == 3
    assert cls(hell, v("e", "Eric Church - Hell Of A View (In Studio Performance)", "EricChurchVEVO")) == 2
    assert cls(t("Homegrown", "Zac Brown Band"),
               v("f", "Zac Brown Band - Homegrown (Official Audio)", "ZacBrownBandVEVO")) == 1
    assert cls(t("Day Drunk", "Morgan Evans"), v("g", "Day Drunk", "Morgan Evans - Topic")) == 1
    assert cls(t("Chicken Fried", "Zac Brown Band"),
               v("h", "Chicken Fried - Zac Brown Band (Lyrics)", "The Rhythm")) == 0


def test_a_long_official_video_still_counts():
    # Dixieland Delight's official video runs well past the album cut.
    m = score(t("Dixieland Delight", "Alabama", 314), v("x", "Alabama - Dixieland Delight (Official Video)",
                                                      "Alabama", 520))
    assert m.cls == 5 and m.confidence == "high"


def test_other_recordings_are_not_the_track():
    assert cls(t("Cruise", "Florida Georgia Line"),
               v("a", "Florida Georgia Line - Cruise (Remix) ft. Nelly", "FlaGeorgiaLineVEVO")) is None
    assert cls(t("Cruise - Remix", "Florida Georgia Line"),
               v("a", "Florida Georgia Line - Cruise (Remix) ft. Nelly", "FlaGeorgiaLineVEVO")) == 5
    assert cls(t("I Run To You", "Lady A"),
               v("k", "I Run to You - Lady A | Karaoke Version | KaraFun", "KaraFun Karaoke")) is None


def test_apostrophes_and_initials_normalize():
    assert norm("Ain’t That Some") == norm("Ain't That Some") == "aint that some"
    assert norm("Fake I.D.") == norm("Fake ID")
    assert score(t("Fake ID (feat. Gretchen Wilson)", "Big & Rich"),
                 v("a", "Big & Rich - Fake I.D. (Feat. Gretchen Wilson)", "Juliawan Huang")) is not None


def test_adopt_keeps_the_best_existing_copy():
    track = t("We Got History", "Mitchell Tenpenny")
    existing = [v("acoustic", "Mitchell Tenpenny - We Got History (Acoustic)", "MitchellTenpennyVEVO"),
                v("mv", "Mitchell Tenpenny - We Got History (Official Music Video)", "MitchellTenpennyVEVO")]
    assert adopt(track, existing, ()).video_id == "mv"


def test_youtube_query():
    assert youtube_query(v("a", "Luke Bryan - Crash My Party (Official Music Video)", "LukeBryanVEVO")) \
        == "Luke Bryan Crash My Party"
    assert youtube_query(v("b", "Day Drunk", "Morgan Evans - Topic")) == "Morgan Evans Day Drunk"
    assert youtube_query(v("c", "Tim McGraw - Highway Don't Care ft. Taylor Swift, Keith Urban",
                           "TimMcGrawVEVO")) == "Tim McGraw Highway Don't Care"


def test_merge_places_youtube_only_songs_after_their_neighbour():
    a, b, c = t("A", "x", tid="a"), t("B", "x", tid="b"), t("C", "x", tid="c")
    new = t("New", "y", tid="n")
    chosen = {"a": "va", "b": "vb", "c": "vc"}
    current = [("1", "va"), ("2", "vb"), ("3", "vnew"), ("4", "vkeep"), ("5", "vc")]
    seq = merge([a, b, c], {"vnew": new}, {"vkeep"}, current, chosen)
    assert seq == [("track", a), ("track", b), ("add", new), ("keep", "vkeep"), ("track", c)]


def test_merge_puts_a_leading_youtube_only_song_first():
    a = t("A", "x", tid="a")
    new = t("New", "y", tid="n")
    seq = merge([a], {"vnew": new}, set(), [("1", "vnew"), ("2", "va")], {"a": "va"})
    assert seq == [("add", new), ("track", a)]


def test_label_channels_post_official_videos():
    assert cls(t("Toes", "Zac Brown Band", 262),
               v("a", "Zac Brown Band - Toes (Official Video)", "Atlantic Records", 291)) == 5
    assert cls(t("Heads Carolina, Tails California", "Jo Dee Messina", 208),
               v("b", "Jo Dee Messina - Heads Carolina, Tails California (Official Music Video)", "Curb Records")) == 5
    # A fan channel that merely ends in "Music" stays unofficial.
    assert cls(t("Be Like That", "Kane Brown"),
               v("c", "Kane Brown - Be Like That (Official Lyric Video)", "Vibe Music")) == 0


def test_an_explicit_music_video_beats_a_bare_title():
    track = t("We Got History", "Mitchell Tenpenny", 203)
    existing = [v("bare", "Mitchell Tenpenny - We Got History", "MitchellTenpennyVEVO", 203),
                v("mv", "Mitchell Tenpenny - We Got History (Official Music Video)", "MitchellTenpennyVEVO", 204)]
    assert adopt(track, existing, ()).video_id == "mv"


def test_karaoke_copies_of_playlist_songs_are_duplicates():
    from playlist_sync.sync import knockoff_of
    songs = [t("I Run To You", "Lady A"), t("Bartender Song (Sittin' At A Bar) - Alt/Rock Mix", "Rehab")]
    assert knockoff_of(songs, v("k", "I Run to You - Lady A | Karaoke Version | KaraFun", "KaraFun Karaoke"))
    assert knockoff_of(songs, v("s", "Rehab - Bartender Song (Sittin' At A Bar) - Alt/Rock Mix (Lyrics version)",
                                "Sing-With Karaoke"))
    assert not knockoff_of(songs, v("c", "Teddy Swims covers Cruel Summer by Taylor Swift", "BBC Radio 1"))


def test_youtube_query_keeps_remix():
    assert youtube_query(v("r", "Florida Georgia Line - Cruise (Remix) ft. Nelly", "FlaGeorgiaLineVEVO")) \
        == "Florida Georgia Line Cruise remix"


def test_band_videos_channel_and_label_bare_titles_are_official():
    # Angry White Guys, 2026-11-16: both official videos scored 0 and were swapped for live clips.
    assert cls(t("Outside", "Staind", 292), v("a", "Staind - Outside (Official Video)", "Staind Videos", 300)) == 5
    assert cls(t("Self Esteem", "The Offspring", 257),
               v("b", 'The Offspring - "Self Esteem"', "Epitaph Records", 260)) == 5
    # The looser label suffixes still need "official" in the title.
    assert cls(t("Self Esteem", "The Offspring", 257),
               v("c", "The Offspring - Self Esteem", "Throwback Entertainment", 260)) == 0


def test_search_falls_back_to_the_artist_when_youtube_returns_nothing():
    from playlist_sync.sync import _search

    class FakeYT:
        def __init__(self):
            self.queries = []

        def search(self, query, results=10):
            self.queries.append(query)
            return [] if "Sex" in query else ["a"]

        def videos(self, ids):
            return {i: v(i, "Color Me Badd - I Wanna Sex You Up (Official Music Video)", "Warner Records", 245)
                    for i in ids}

    yt = FakeYT()
    m = _search(yt, t("I Wanna Sex You Up - Single Mix", "Color Me Badd", 245), ())
    assert yt.queries == ["Color Me Badd I Wanna Sex You Up", "Color Me Badd"]
    assert m.video_id == "a" and m.cls == 5


def test_featured_credits_dont_count_against_title_coverage():
    # Boom, 2026-11-17: these YouTube videos never found their Spotify tracks.
    from playlist_sync.sync import _segments
    loyal = _segments("Chris Brown - Loyal (Official Video) ft. Lil Wayne, Tyga", ("Chris Brown", "Lil Wayne", "Tyga"))
    assert {"loyal"} in loyal
    where = _segments('Skrillex and Diplo - "Where Are Ü Now" with Justin Bieber (Official Video)',
                      ("Jack Ü", "Skrillex", "Diplo", "Justin Bieber"))
    assert {"where", "are", "now", "with"} in where


def test_a_title_word_shared_with_an_artist_name_still_counts():
    from playlist_sync.sync import _segments
    segs = _segments('Skrillex and Diplo - "Where Are Ü Now" with Justin Bieber (Official Video)',
                     ("Jack Ü", "Skrillex", "Diplo", "Justin Bieber"), {"where", "are", "u", "now"})
    assert {"where", "are", "u", "now", "with"} in segs
