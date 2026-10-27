"""The live-style test song through the whole pipeline: the problems heard on a real
recording (mashed strums, stuttering drums, bass playing the melody) must stay fixed."""

import pytest
from eval_live import chord_name_at, load, onset_match, starts
from live_song import CHART, make_live_song
from worship_tracks import pipeline
from worship_tracks.chords import parse_chart_text, parse_chord


@pytest.fixture(scope="module")
def live(soundfont, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("live")
    song, stems, truth = make_live_song(tmp / "song", soundfont, bars=12)
    out = tmp / "out"
    result = pipeline.process_song(out, song, soundfont, lambda *_: None,
                                   separate_fn=lambda mix, progress: stems, chart=parse_chart_text(CHART))
    return out, result, truth


def test_chords_follow_the_song(live):
    _, result, truth = live
    checks = [(s + 0.1, n) for s, e, n in truth.chords] + [(e - 0.1, n) for s, e, n in truth.chords]
    right = sum(chord_name_at(result["chords"], t) == n for t, n in checks)
    assert right / len(checks) >= 0.9


def test_drums_are_locked_to_the_beat(live):
    out, _, truth = live
    drums = load(out, "drums")
    precision, recall = onset_match([n.start for n in drums], [n.start for n in truth.drums])
    assert precision >= 0.9 and recall >= 0.85


def test_guitar_strums_like_the_guitarist(live):
    out, result, truth = live
    guitar = load(out, "guitar")
    strums = starts(guitar, 0.1)
    precision, recall = onset_match(strums, truth.strums)
    # Mac and Linux builds of the audio libraries differ slightly: Linux scores 0.95+ here, a Mac 0.87.
    assert precision >= 0.75 and recall >= 0.8
    assert len(strums) <= len(truth.strums) * 1.3
    for note in guitar:
        assert note.pitch % 12 in parse_chord(chord_name_at(result["chords"], note.start + 0.03)).tones


def test_keys_play_only_where_the_keys_play(live):
    out, _, truth = live
    hits = starts(load(out, "keys"))
    precision, recall = onset_match(hits, truth.keys_hits)
    assert precision >= 0.85 and recall >= 0.9


def test_bass_stays_low_and_on_the_beat(live):
    out, _, truth = live
    bass = load(out, "bass")
    assert all(n.pitch <= pipeline.BASS_TOP for n in bass)
    precision, _ = onset_match(starts(bass), [n.start for n in truth.bass])
    assert precision >= 0.7
