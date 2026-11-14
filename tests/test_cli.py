"""The CLI imports and a held playlist parses."""
from playlist_sync import cli, config  # noqa: F401  (the import catches syntax errors in the CLI)


def test_hold_parses(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[[playlist]]\nname = "A"\nspotify_id = "x"\nhold = true\n'
        '[[playlist]]\nname = "B"\nspotify_id = "y"\n', encoding="utf-8")
    cfg = config.load(tmp_path)
    assert [(p.name, p.hold) for p in cfg.playlists] == [("A", True), ("B", False)]
