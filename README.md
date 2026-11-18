# playlist-sync

Two-way sync between Spotify playlists and YouTube playlists that picks **music videos**, not just audio.

Spotify sets the order. For each playlist you list:

- every Spotify track is matched to the best YouTube video, and the YouTube playlist follows Spotify's order;
- a song added on YouTube that Spotify doesn't have is found with Spotify search and added to Spotify after its YouTube neighbour. If search can't find it, it stays on YouTube and is listed by `playlist-sync review`;
- a song removed on either side is removed from the other on the next run. Removals are detected against the pairs recorded at the end of the last complete run, so nothing is removed on a first run;
- duplicates on YouTube are deleted: a second copy of a video, another upload of a song already there, or a karaoke or cover copy of it. A song Spotify lists twice under different track IDs gets one YouTube copy.

Set `two_way = false` on a playlist for one-way Spotify → YouTube, where anything edited by hand on YouTube is overwritten. Change a pick by pinning it in `config.toml`.

## How a video is picked

Official uploads only (the artist's channel, VEVO, a record label or a trusted channel), ranked by class:

| Class | |
|---|---|
| 5 | official music video |
| 4 | official alternate video (dance, fan or alternate cut) |
| 3 | official lyric video or visualizer |
| 2 | official live, session or TV performance (e.g. *The Midnight Special*) |
| 1 | official audio (Topic channel or "Provided to YouTube" art track) |
| 0 | an unofficial upload, only when nothing official matches |

Remixes, covers, karaoke, sped-up and other different recordings are rejected unless the Spotify track is that recording.

**Nothing is downgraded.** A video already in the YouTube playlist is kept unless a strictly higher class is found, and never replaced by a live or audio-only clip, since that changes the recording; spare daily quota goes to searching for upgrades, lowest class first. Low-confidence picks are **not** added; they're listed by `playlist-sync review` so you can pin one. Pins under `[overrides]` always win.

## Limits you inherit

- **Spotify development mode** (February 2026 rules): the app owner needs Premium, and the API only returns the items of playlists you own or collaborate on.
- **YouTube quota:** 10,000 units a day. A search costs 100 and each insert, move or delete costs 50, so a new playlist fills at roughly 60 tracks a day. Matches are cached, so steady-state runs are cheap. A playlist isn't written until all of its tracks have been matched.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

1. **Spotify app.** In the [Spotify developer dashboard](https://developer.spotify.com/dashboard), create an app using the Web API with redirect URI `http://127.0.0.1:8888/callback`. Set `PLAYLIST_SYNC_SPOTIFY_CLIENT_ID` in the environment or `~/.env.local`. No client secret is needed (PKCE). Sign-in asks to read and modify your playlists; nothing else.
2. **Google OAuth client.** In Google Cloud:
   - create a project and enable **YouTube Data API v3**;
   - configure the OAuth consent screen (External), add the `.../auth/youtube` scope, and **publish it to "In production"**. In "Testing", refresh tokens expire after 7 days and scheduled runs stop. Google shows an "unverified app" warning at sign-in; that's expected for a personal app;
   - create an OAuth client of type **Desktop app** and save its JSON as `.state/google_client.json` (or point `PLAYLIST_SYNC_GOOGLE_CLIENT_FILE` at it).
3. Sign in once each. Refresh tokens are stored in the OS credential store.
   ```
   uv run playlist-sync auth spotify
   uv run playlist-sync auth youtube
   ```
4. `uv run playlist-sync playlists` lists your Spotify playlist IDs. Copy `config.example.toml` to `config.toml` and add the ones to sync.

## Use

```
uv run playlist-sync run --dry-run   # match and show the planned changes; writes nothing to Spotify or YouTube
uv run playlist-sync run             # tidy, then sync
uv run playlist-sync tidy --dry-run  # show what the cleanup would remove
uv run playlist-sync tidy --force    # apply a cleanup larger than max_remove
uv run playlist-sync review          # tracks that need a manual pick
uv run playlist-sync check           # what needs a person from today's run log (--date); silent when clean
uv run playlist-sync status          # quota used today, cache, playlist mapping
```

Runs from the repo directory, or set `PLAYLIST_SYNC_HOME`. `run --log` also appends output to `.state/logs/`.

### Daily schedule (Windows)

A daily run carries on where the last one stopped: matches are cached, so a new playlist that needs more searches than one day's quota finishes over several days. YouTube's quota resets at midnight Pacific, so schedule just after it. The task runs as you while you're signed in, because the refresh tokens are in your credential store.

```powershell
$repo = (Get-Location).Path
$uv = (Get-Command uv).Source
$action = New-ScheduledTaskAction -Execute $uv -Argument 'run playlist-sync run --log' -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At 3:30am   # Eastern; midnight Pacific + 30 min
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName 'playlist-sync' -Action $action -Trigger $trigger -Settings $settings `
  -Description 'Spotify and YouTube playlist sync'
```

**Hold a new playlist** with `hold = true` until you've read its plan. Scheduled runs then only match and dry-run it, and print what they would change; live playlists run first so their daily writes aren't starved, and upgrade searches pause while anything is held or hasn't finished its first sync. Set `hold = false` once the plan looks right.

## Cleanup-only playlists

A `[[tidy]]` entry cleans up a Spotify playlist that an app keeps appending to, such as Shazam's **My Shazam Tracks**, which adds every Shazam again, repeats included. It is never synced to YouTube. `run` tidies first, so the daily task keeps it clean.

- `dedupe = true`: each song stays once, the earliest copy.
- `drop_owned = true`: a song that is already in another playlist you own is removed.
- The same song is the same track ID, or the same full title and lead artist. A remix or other version with a different title counts as a different song.
- Spotify's remove call ignores positions and takes out every copy of a track. A repeated track is therefore removed and its earliest copy re-added in its old place, and its "added" date becomes the day of the cleanup.
- Every cleanup first writes a snapshot of the whole playlist (positions and dates) to `.state/tidy/`.
- A run that would remove more than `max_remove` (default 25) stops and says so. Check `tidy --dry-run`, then apply it with `tidy --force`.

## Files

| Path | Tracked | Contents |
|---|---|---|
| `config.toml` | no | playlists, pins, trusted channels |
| `.state/matches.json` | no | cached track → video matches |
| `.state/state.json` | no | playlists created by the tool; track → video pairs from the last complete run |
| `.state/quota.json` | no | units spent today (Pacific) |
| `.state/review.md` | no | the latest review list |
| `.state/tidy/` | no | a snapshot of each tidied playlist, taken before every cleanup |

## Privacy

playlist-sync runs on your own machine and has no server. It reads your Spotify playlists, and edits the ones you list plus the matching YouTube playlists on your account. The Spotify sign-in also grants Liked Songs, listening history, top tracks, followed artists and playlist covers (see `SCOPES` in `spotify.py`) for reviews you run yourself; the sync and tidy don't use them. After a scope change, run `playlist-sync auth spotify` again. Nothing is sent anywhere else. OAuth refresh tokens are kept in your operating system's credential store, and match results stay in the local `.state/` folder. Revoke access at any time from your [Google account permissions](https://myaccount.google.com/permissions) and [Spotify apps](https://www.spotify.com/account/apps/) pages.

## License

MIT
