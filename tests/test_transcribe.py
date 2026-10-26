from worship_tracks.transcribe import Note, drop_overtones, make_monophonic, merge_repeats


def test_monophonic_cuts_overlaps_and_keeps_louder_of_simultaneous_notes():
    notes = [Note(0.0, 1.0, 40, 100), Note(0.01, 1.0, 52, 120), Note(0.5, 1.5, 43, 90)]
    result = make_monophonic(notes)
    assert [(n.pitch, n.start, n.end) for n in result] == [(52, 0.01, 0.5), (43, 0.5, 1.5)]


def test_monophonic_skips_overtone_of_ringing_note():
    notes = [Note(0.0, 1.0, 36, 110), Note(0.5, 0.9, 48, 70)]
    assert [n.pitch for n in make_monophonic(notes)] == [36]


def test_monophonic_drops_tail_blip_before_same_pitch():
    notes = [Note(0.0, 0.9, 38, 110), Note(0.9, 0.98, 38, 100), Note(1.0, 1.9, 38, 110)]
    result = make_monophonic(notes)
    assert [(n.start, n.end) for n in result] == [(0.0, 0.9), (1.0, 1.9)]


def test_drop_overtones_keeps_loud_octaves():
    chord = [Note(0, 1, 55, 110), Note(0, 1, 67, 70), Note(0, 1, 59, 100), Note(0, 1, 71, 100)]
    assert sorted(n.pitch for n in drop_overtones(chord)) == [55, 59, 71]


def test_merge_repeats_joins_broken_sustain():
    notes = [Note(0, 1.0, 60, 80), Note(1.2, 2.0, 60, 90), Note(3.0, 4.0, 60, 80)]
    result = merge_repeats(notes, 0.35)
    assert [(n.start, n.end, n.velocity) for n in result] == [(0, 2.0, 90), (3.0, 4.0, 80)]
