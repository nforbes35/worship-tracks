import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from worship_tracks import audio, library, pipeline
from worship_tracks.app import create_app
from worship_tracks.instruments import TRACKS


def test_process_song_makes_aligned_tracks(tmp_path, soundfont, synthetic_song, fake_separator):
    song_wav, stems, _ = synthetic_song
    # A track the song doesn't have should be reported missing, not invented.
    stems = {**stems, "piano": np.zeros_like(stems["piano"])}
    steps = []
    result = pipeline.process_song(
        tmp_path, song_wav, soundfont, lambda step, f: steps.append(f),
        separate_fn=lambda mix, progress: stems,
    )

    assert steps[-1] == 1.0 and steps == sorted(steps)
    present = {t["id"]: t["present"] for t in result["tracks"]}
    assert present == {t.id: t.id != "keys" for t in TRACKS}

    duration = sf.info(str(song_wav)).duration
    assert abs(result["duration"] - duration) < 0.01
    for t in TRACKS:
        assert (tmp_path / "original" / f"{t.id}.m4a").is_file()
        if present[t.id]:
            assert (tmp_path / "midi" / f"{t.id}.mid").is_file()
            # Every track is exactly as long as the song, so they line up in the player.
            decoded = tmp_path / f"{t.id}.wav"
            audio.decode(tmp_path / "tracks" / f"{t.id}.m4a", decoded)
            assert abs(sf.info(str(decoded)).duration - duration) < 0.05


def test_recreated_track_matches_original_level(tmp_path, soundfont, synthetic_song, fake_separator):
    song_wav, stems, _ = synthetic_song
    pipeline.process_song(tmp_path, song_wav, soundfont, lambda *_: None, separate_fn=fake_separator)
    decoded = tmp_path / "bass.wav"
    audio.decode(tmp_path / "tracks" / "bass.m4a", decoded)
    ratio = audio.rms(audio.read(decoded)) / audio.rms(stems["bass"])
    assert 0.6 < ratio < 1.6


def test_upload_to_playback(tmp_path, soundfont, synthetic_song, fake_separator, monkeypatch):
    song_wav, _, _ = synthetic_song
    real = pipeline.process_song
    monkeypatch.setattr(
        pipeline, "process_song",
        lambda *args, **kwargs: real(*args, separate_fn=fake_separator, **kwargs),
    )
    app = create_app(data_dir=tmp_path, soundfont=soundfont, passcode="")
    with TestClient(app) as client:
        with open(song_wav, "rb") as f:
            created = client.post("/api/songs", files={"file": ("Test Song.wav", f, "audio/wav")}).json()
        assert created["title"] == "Test Song" and created["status"] == "queued"

        deadline = time.time() + 300
        while (song := client.get(f"/api/songs/{created['id']}").json())["status"] not in ("ready", "failed"):
            assert time.time() < deadline, song
            time.sleep(0.5)
        assert song["status"] == "ready", song["error"]
        assert [s["id"] for s in client.get("/api/songs").json()] == [created["id"]]

        track = client.get(f"/api/songs/{created['id']}/files/tracks/drums.m4a")
        assert track.status_code == 200 and track.headers["content-type"] == "audio/mp4"
        assert client.get(f"/api/songs/{created['id']}/files/song.json").status_code == 404
        assert client.get(f"/api/songs/{created['id']}/files/..%2F..%2F.secret").status_code == 404

        assert client.delete(f"/api/songs/{created['id']}").status_code == 204
        assert client.get("/api/songs").json() == []


def test_unfinished_songs_resume_after_restart(tmp_path, monkeypatch):
    processed = []
    monkeypatch.setattr(library.Library, "_process", lambda self, meta: processed.append(meta["id"]))
    lib = library.Library(tmp_path, tmp_path / "none.sf2")
    upload = tmp_path / "upload"
    upload.write_bytes(b"x")
    song = lib.add("A", "a.mp3", upload)
    lib._update(song["id"], status="processing", progress=0.4)

    restarted = library.Library(tmp_path, tmp_path / "none.sf2")
    restarted.start()
    deadline = time.time() + 5
    while not processed and time.time() < deadline:
        time.sleep(0.05)
    assert processed == [song["id"]]
    assert restarted.get(song["id"])["progress"] == 0.0


def test_real_separation(synthetic_song):
    """Runs the actual Demucs model (downloaded by scripts/setup.sh) on the synthetic song."""
    try:
        pipeline._get_separator()
    except Exception as exc:  # noqa: BLE001 - no model available offline is a skip, not a failure
        pytest.skip(f"Demucs model not available: {exc}")
    song_wav, stems, _ = synthetic_song
    separated = pipeline.separate(audio.read(song_wav), lambda f: None)
    assert set(separated) == {t.stem for t in TRACKS}
    for name in ("drums", "bass"):
        # Most of what Demucs calls drums (or bass) should really be that instrument.
        truth = stems[name]
        n = min(truth.shape[1], separated[name].shape[1])
        error = audio.rms(separated[name][:, :n] - truth[:, :n])
        assert error < audio.rms(truth[:, :n]) * 0.7, name
