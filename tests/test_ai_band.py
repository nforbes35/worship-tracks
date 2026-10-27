"""The AI band (ACE-Step) path, against a stand-in ACE-Step server."""

import time

import numpy as np
import pytest
from fake_acestep import FakeAceStep
from fastapi.testclient import TestClient
from live_song import CHART, make_live_song
from worship_tracks import audio, backing, pipeline, structure
from worship_tracks.acestep import AceStep, AceStepError
from worship_tracks.app import create_app
from worship_tracks.chords import parse_chart_text
from worship_tracks.rhythm import make_grid


@pytest.fixture(scope="module")
def live(soundfont, tmp_path_factory):
    """A 12-bar live-style song in 6/8 (bars are 1.6 s; the melody comes in every 4 bars)."""
    return make_live_song(tmp_path_factory.mktemp("live"), soundfont, bars=12)


def fake_separator(stems):
    def separate(mix, on_progress):
        on_progress(1.0)
        n = mix.shape[-1]
        return {k: np.pad(v, ((0, 0), (0, max(0, n - v.shape[1]))))[:, :n] for k, v in stems.items()}
    return separate


def test_clean_caption_drops_singing():
    caption = backing.clean_caption(
        "A heartfelt modern worship song with warm male vocals, acoustic guitar strumming, piano and live drums")
    assert "vocal" not in caption.lower()
    assert caption.startswith("Instrumental.")
    assert "acoustic guitar strumming" in caption and "live drums" in caption


def test_section_names_from_lyrics():
    assert structure.section_names_from_lyrics("[Intro]\n\n[verse 1]\nla\n[Chorus]\nla la\n[Outro]") == \
        ["Intro", "Verse 1", "Chorus", "Outro"]


def test_key_names():
    assert structure.key_name("C") == "C Major"
    assert structure.key_name("Em") == "E Minor"
    assert structure.key_name("F#m") == "F# Minor"


def test_align_finds_a_late_recording():
    sr = audio.SAMPLE_RATE
    rng = np.random.default_rng(0)
    clicks = np.zeros(sr * 20)
    for t in np.cumsum(rng.uniform(0.2, 0.6, 60)):
        if t < 19:
            clicks[int(t * sr):int(t * sr) + 200] = rng.uniform(0.3, 1.0)
    late = np.concatenate([np.zeros(int(0.3 * sr)), clicks])[: len(clicks)]
    shift, drift = backing.align(clicks, late, sr, max_shift=0.5)
    assert abs(shift - 0.3) < 0.03 and abs(drift) < 0.03


def test_layout_of_live_song(live):
    song, stems, truth = live
    mono = {k: v.mean(axis=0) for k, v in stems.items()}
    mix = audio.read(song).mean(axis=0)
    duration = len(mix) / audio.SAMPLE_RATE
    grid = make_grid(mix, audio.SAMPLE_RATE, duration, 75, "6/8")
    harmony = mono["guitar"] + mono["piano"] + mono["other"] + 0.5 * mono["bass"]
    assert structure.detect_key(harmony, audio.SAMPLE_RATE) == "C Major"
    sections = structure.find_sections(harmony, mono["vocals"], audio.SAMPLE_RATE, duration, grid)
    assert sections[0].start == 0 and abs(sections[-1].end - duration) < 0.01
    assert all(a.end == b.start for a, b in zip(sections, sections[1:]))
    # Section boundaries fall on bar lines (bars are 1.6 s, the grid is within a few ms).
    for s in sections[1:]:
        assert abs(s.start / 1.6 - round(s.start / 1.6)) < 0.05


def test_acestep_client(tmp_path, live):
    song, _, _ = live
    with FakeAceStep() as fake:
        engine = AceStep(fake.url)
        assert engine.available()
        heard = engine.analyse(song)
        assert heard["bpm"] == 75 and "acoustic guitar" in heard["prompt"]
        out = tmp_path / "cover.wav"
        engine.cover(song, out, caption="Instrumental. piano", bpm=75.2, key="C Major", time_signature="6/8",
                     duration=19.2, strength=0.9, seed=7)
        assert out.stat().st_size > 1000
        sent = fake.requests[-1]
        assert sent["task_type"] == "cover" and sent["lyrics"] == "[Instrumental]"
        assert (sent["bpm"], sent["key_scale"], sent["time_signature"]) == ("75", "C Major", "6/8")
        assert sent["audio_cover_strength"] == "0.90" and sent["seed"] == "7" and sent["use_random_seed"] == "false"
        assert sent["use_cot_caption"] == "false" and sent["thinking"] == "false"
    assert not AceStep(fake.url).available()  # stopped


def test_ai_band_song(tmp_path, soundfont, live):
    song, stems, _ = live
    with FakeAceStep(delay=0.25) as fake:
        result = pipeline.process_song(tmp_path, song, soundfont, lambda *_: None,
                                       separate_fn=fake_separator(stems), chart=parse_chart_text(CHART),
                                       engine=AceStep(fake.url))
        cover_request = fake.requests[-1]
    assert result["engine"] == "ai-band"
    layout = result["layout"]
    assert (layout["tempo"], layout["key"], layout["time"]) == (75.0, "C Major", "6/8")
    assert "vocal" not in layout["instruments"].lower() and "acoustic guitar" in layout["instruments"]
    assert layout["sections"] and layout["sections"][0]["start"] == 0
    # The band was asked to play the instrumental, in the song's tempo, key and time.
    sent = audio.read(cover_request["src_audio"])
    assert sent.shape[0] == 2 and abs(sent.shape[1] / audio.SAMPLE_RATE - result["duration"]) < 0.05
    assert cover_request["prompt"] == layout["instruments"]
    # The late recording was pulled back into line.
    assert abs(result["backing"]["shift"] - 0.25) < 0.03
    tracks = {t["id"]: t for t in result["tracks"]}
    assert tracks["melody"]["label"] == "Vocals (original)" and tracks["melody"]["ai"] is False
    for track_id in ("drums", "bass", "guitar", "keys", "pads", "melody"):
        assert (tmp_path / "tracks" / f"{track_id}.m4a").exists(), track_id
    assert (tmp_path / "backing.m4a").exists()


def wait_ready(client, song_id):
    deadline = time.time() + 300
    while (song := client.get(f"/api/songs/{song_id}").json())["status"] not in ("ready", "failed"):
        assert time.time() < deadline, song
        time.sleep(0.3)
    return song


def test_new_take_and_fallbacks(tmp_path, soundfont, live, monkeypatch):
    song, stems, _ = live
    monkeypatch.setattr(pipeline, "separate", fake_separator(stems))
    for name in ("process_song", "rebuild_tracks"):
        real = getattr(pipeline, name)
        monkeypatch.setattr(pipeline, name, lambda *a, _real=real, **k: _real(*a, separate_fn=fake_separator(stems), **k))

    with FakeAceStep() as fake:
        engine = {"value": AceStep(fake.url)}
        app = create_app(data_dir=tmp_path, soundfont=soundfont, passcode="", engine_factory=lambda: engine["value"])
        with TestClient(app) as client:
            assert client.get("/api/engine").json() == {"aiBand": True}
            with open(song, "rb") as f:
                song_id = client.post("/api/songs", files={"file": ("song.wav", f, "audio/wav")}).json()["id"]
            first = wait_ready(client, song_id)
            assert first["status"] == "ready" and first["engine"] == "ai-band" and not first["backing_error"]

            take = client.post(f"/api/songs/{song_id}/backing", json={"caption": "Instrumental. gospel organ", "strength": 0.7})
            assert take.status_code == 200
            second = wait_ready(client, song_id)
            assert second["backing"]["caption"] == "Instrumental. gospel organ"
            assert second["backing"]["strength"] == 0.7 and second["version"] > first["version"]
            assert fake.requests[-1]["prompt"] == "Instrumental. gospel organ"

            # The AI band fails: the song keeps its tracks and says why.
            fake.fail = True
            client.post(f"/api/songs/{song_id}/backing", json={"caption": ""})
            third = wait_ready(client, song_id)
            assert third["status"] == "ready" and "couldn't play" in third["backing_error"]
            assert third["engine"] == "ai-band"

            # ACE-Step not running: new takes are refused, new songs are made note by note.
            engine["value"] = None
            assert client.post(f"/api/songs/{song_id}/backing", json={}).status_code == 503
            with open(song, "rb") as f:
                other = client.post("/api/songs", files={"file": ("b.wav", f, "audio/wav")}).json()["id"]
            notes = wait_ready(client, other)
            assert notes["status"] == "ready" and notes["engine"] == "notes" and notes["layout"]["sections"]


def test_failing_band_falls_back_to_notes(tmp_path, soundfont, live):
    song, stems, _ = live
    with FakeAceStep(fail=True) as fake, pytest.raises(AceStepError):
        pipeline.process_song(tmp_path, song, soundfont, lambda *_: None, separate_fn=fake_separator(stems),
                              engine=AceStep(fake.url))
