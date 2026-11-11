from __future__ import annotations

from pathlib import Path

from spotify_ipod.audio import aac_command
from spotify_ipod.match import query_variants, score_candidate


def test_aac_command_matches_ipod_settings():
    cmd = aac_command(
        Path("in.webm"),
        Path("out.m4a"),
        bitrate="256k",
        sample_rate=44100,
        channels=2,
        ffmpeg="/usr/bin/ffmpeg",
        encoder="aac_at",
    )
    assert cmd[:3] == ["/usr/bin/ffmpeg", "-y", "-i"]
    assert cmd[cmd.index("-c:a") + 1] == "aac_at"
    assert cmd[cmd.index("-b:a") + 1] == "256k"
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert "-vn" in cmd


def test_assert_ipod_aac_rejects_wrong_rate(tmp_path: Path, monkeypatch):
    from spotify_ipod import audio as audio_mod

    path = tmp_path / "song.m4a"
    path.write_bytes(b"fake")
    monkeypatch.setattr(
        audio_mod,
        "probe_audio",
        lambda _p: {"codec_name": "aac", "sample_rate": "48000", "channels": "2", "bit_rate": "256000"},
    )
    try:
        audio_mod.assert_ipod_aac(path)
        raise AssertionError("expected AudioError")
    except audio_mod.AudioError as exc:
        assert "sample rate" in str(exc)


def test_official_song_scores_higher_than_cover():
    track = {"title": "Clocks", "artists": "Coldplay", "duration_ms": 307000}
    official = {
        "title": "Clocks",
        "artists": [{"name": "Coldplay"}],
        "author": "Coldplay - Topic",
        "duration_seconds": 307,
        "source": "music",
    }
    cover = {
        "title": "Clocks (Cover)",
        "artists": [{"name": "Some Band"}],
        "author": "Random",
        "duration_seconds": 310,
        "source": "videos",
    }
    assert score_candidate(track, official) > score_candidate(track, cover)


def test_query_variants_include_isrc_and_ampersand():
    variants = query_variants({"title": "Stop & Stare", "artists": "OneRepublic", "isrc": "USUM708"})
    assert any("USUM708" in item for item in variants)
    assert any("and" in item.lower() for item in variants)
