from pathlib import Path

import pytest
from fpdf import FPDF
from worship_tracks.chords import parse_chart_text, parse_chord, read_chart, scale_pcs


def pcs(*names):
    return frozenset({"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "Ab": 8, "A": 9,
                      "Bb": 10, "B": 11}[n] for n in names)


@pytest.mark.parametrize(("symbol", "tones", "bass"), [
    ("G", pcs("G", "B", "D"), "G"),
    ("Em", pcs("E", "G", "B"), "E"),
    ("Em7", pcs("E", "G", "B", "D"), "E"),
    ("D/F#", pcs("D", "F#", "A"), "F#"),
    ("Dsus4", pcs("D", "G", "A"), "D"),
    ("Asus", pcs("A", "D", "E"), "A"),
    ("Asus2", pcs("A", "B", "E"), "A"),
    ("C2", pcs("C", "D", "E", "G"), "C"),
    ("Cadd9", pcs("C", "D", "E", "G"), "C"),
    ("Cmaj7", pcs("C", "E", "G", "B"), "C"),
    ("Bbmaj7", pcs("Bb", "D", "F", "A"), "Bb"),
    ("F#m7b5", pcs("F#", "A", "C", "E"), "F#"),
    ("G5", pcs("G", "D"), "G"),
    ("Bdim", pcs("B", "D", "F"), "B"),
    ("Ab/Eb", pcs("Ab", "C", "Eb"), "Eb"),
    ("E(sus4)", pcs("E", "A", "B"), "E"),
])
def test_parse_chord(symbol, tones, bass):
    chord = parse_chord(symbol)
    assert chord is not None
    assert chord.tones == tones
    assert chord.bass == next(iter(pcs(bass)))


@pytest.mark.parametrize("word", ["Amazing", "Be", "Grace", "And", "Chorus", "Dm7x", "H", "ABBA"])
def test_words_are_not_chords(word):
    assert parse_chord(word) is None


CHART = """\
10,000 Reasons (Bless The Lord)
Key - G | Tempo - 73 | Time - 4/4

Chorus
C           G     D       Em
  Bless the Lord O my soul
C        G       D
  O my soul

Verse 1
C     G   D        Em
The sun comes up
| C  /  G/B  / | Am7 / D / |
Instrumental
G  D/F#  Em7  Csus2 (x2)
"""


def test_parse_chart_text():
    chart = parse_chart_text(CHART)
    assert chart.key == "G"
    assert chart.sections == ["Chorus", "Verse 1", "Instrumental"]
    assert chart.chords == ["C", "G", "D", "Em", "C", "G", "D", "C", "G", "D", "Em",
                            "C", "G/B", "Am7", "D", "G", "D/F#", "Em7", "Csus2"]


def test_chordpro_and_missing_key():
    chart = parse_chart_text("{title: Test}\n[D]Here I [A]am to [G]worship\n[Bm]Here I am")
    assert chart.chords == ["D", "A", "G", "Bm"]
    assert chart.key == "D"  # guessed from the first chord


def test_scale():
    assert scale_pcs("G") == {7, 9, 11, 0, 2, 4, 6}
    assert scale_pcs("Em") == {4, 6, 7, 9, 11, 0, 2}


def make_pdf(path: Path, with_text: bool = True, columns: int = 1) -> Path:
    """A chart laid out like a SongSelect PDF: chords on their own lines above the lyrics,
    optionally in two columns (the second column continuing the song)."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    lines = CHART.splitlines()
    if with_text:
        per_column = -(-len(lines) // columns)
        for c in range(columns):
            y = 20
            for line in lines[c * per_column:(c + 1) * per_column]:
                if line.strip():
                    # Place each word at its column, like a real chart, rather than as one string.
                    x = 15 + c * 100
                    for word in line.split(" "):
                        if word:
                            pdf.text(x, y, word)
                        x += pdf.get_string_width(word + " ")
                y += 7
    pdf.output(str(path))
    return path


@pytest.mark.parametrize("columns", [1, 2])
def test_read_pdf_chart(tmp_path, columns):
    chart = read_chart(make_pdf(tmp_path / "chart.pdf", columns=columns))
    assert chart.key == "G"
    assert chart.chords[:4] == ["C", "G", "D", "Em"]
    assert len(chart.chords) == 19


def test_scanned_pdf_is_explained(tmp_path):
    with pytest.raises(ValueError, match="scan"):
        read_chart(make_pdf(tmp_path / "scan.pdf", with_text=False))


# Laid out the way SongSelect chord sheets come out of the PDF (made-up words).
SONGSELECT = """\
Song Title (Live)
Artist Name
I V1 C V2 C B Vp C B Vp
INTRO
I
Piano in softly
C C2sus4 C Csus4
VERSE 1
V1
C
Words go here
F/C C
More words here
CHORUS
C
Full Band (except Drums)
Am7 Gadd4
Sing it out
BRIDGE
B
Build
C/E F2
Hallelujah
E1 D1 C1
Page: 1/4
Key: C Tempo: 75 Time: 6/8
"""


def test_songselect_layout():
    chart = parse_chart_text(SONGSELECT)
    assert (chart.key, chart.tempo, chart.time) == ("C", 75.0, "6/8")
    assert chart.sections == ["INTRO", "VERSE 1", "CHORUS", "BRIDGE"]
    # The section codes under each heading ("C" for chorus, "B" for bridge) aren't chords.
    assert chart.chords == ["C", "C2sus4", "C", "Csus4", "C", "F/C", "C", "Am7", "Gadd4", "C/E", "F2"]
