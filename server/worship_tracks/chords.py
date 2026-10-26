"""Chord symbols and chord charts.

Reads the chords and key out of a chart (a PDF such as a SongSelect chord sheet, a
ChordPro file, or plain typed text) so the AI can use the right chords instead of
guessing every note.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NOTE_PCS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

_NOTE = r"[A-G](?:#|b|♯|♭)?"
_CHORD_RE = re.compile(
    rf"^(?P<root>{_NOTE})"
    r"(?P<quality>(?:maj|Maj|M|min|mi|m|dim|°|aug|\+|sus|add|no|omit|[0-9]|\(|\)|b|#|-)*)"
    rf"(?:/(?P<bass>{_NOTE}))?$"
)
# Things on a chord line that aren't chords: bar lines, repeat marks, "N.C." and so on.
_FILLER_RE = re.compile(r"^(\||\|\||/|-|–|\(|\)|\(?(?:x\d+|\d+x)\)?|\(?N\.?C\.?\)?|\.+|:\|\|?|\|\|?:|%)$", re.IGNORECASE)
_KEY_RE = re.compile(rf"\bKey\s*(?:of|[-:=–])?\s*({_NOTE}m?)\b")
_SECTION_RE = re.compile(
    r"^\s*(intro|verse|pre-?chorus|chorus|bridge|tag|outro|interlude|instrumental|turnaround|ending|vamp|refrain|channel)"
    r"\s*\d*\s*:?\s*$",
    re.IGNORECASE,
)
# SongSelect prints a short code under each section heading (I, V1, C, B, Vp...).
# "C" and "B" would otherwise read as chords.
_SECTION_CODE_RE = re.compile(r"^(I|In|Int|V\d*|Vp|PC\d*|Pc\d*|C\d*|Ch\d*|B\d*|Br\d*|T|Tg|Tag|O|Out|E|End|Ins|Inst|R|TA)$")
_TEMPO_RE = re.compile(r"\bTempo\s*[-:=–]?\s*(\d{2,3}(?:\.\d+)?)", re.IGNORECASE)
_TIME_RE = re.compile(r"\bTime\s*(?:Signature)?\s*[-:=–]?\s*(\d{1,2}/\d{1,2})", re.IGNORECASE)
_INLINE_RE = re.compile(r"\[([^\]\s]{1,12})\]")


def _pc(note: str) -> int:
    pc = NOTE_PCS[note[0]]
    for accidental in note[1:]:
        pc += 1 if accidental in "#♯" else -1
    return pc % 12


@dataclass(frozen=True)
class Chord:
    name: str
    root: int
    bass: int
    tones: frozenset[int]  # pitch classes

    @property
    def minor(self) -> bool:
        return (self.root + 3) % 12 in self.tones and (self.root + 4) % 12 not in self.tones


def parse_chord(symbol: str) -> Chord | None:
    """`G`, `Em7`, `Dsus4`, `C2`, `G/B`, `F#m7b5`... or None if it isn't a chord."""
    match = _CHORD_RE.match(symbol.strip())
    if not match:
        return None
    root = _pc(match["root"])
    q = match["quality"].replace("(", "").replace(")", "")
    third = 4
    fifth = 7
    extra: set[int] = set()

    if q.startswith(("maj", "Maj", "M")) and not q.startswith(("min", "mi")):
        rest = q[3:] if q[:3].lower() == "maj" else q[1:]
        if rest.startswith(("7", "9")):
            extra.add(11)
        q = rest[1:] if rest[:1] in ("7", "9") else rest
    elif q.startswith(("min", "mi", "m", "-")):
        third = 3
        q = q[3:] if q.startswith("min") else q[2:] if q.startswith("mi") else q[1:]
    elif q.startswith(("dim", "°")):
        third, fifth = 3, 6
        q = q[3:] if q.startswith("dim") else q[1:]
        if q.startswith("7"):
            extra.add(9)
            q = q[1:]
    elif q.startswith(("aug", "+")):
        fifth = 8
        q = q[3:] if q.startswith("aug") else q[1:]

    tokens = re.findall(r"sus[24]?|add\d+|no\d|omit\d|[b#]\d+|\d+", q)
    if "".join(tokens) != q:
        return None
    for token in tokens:
        if token.startswith("sus"):
            third = 2 if token == "sus2" else 5
        elif token in ("2", "add2", "add9", "9") and not extra & {10, 11}:
            extra.add(2)
            if token == "9":
                extra.add(10)
        elif token == "5":
            third = None
        elif token in ("6", "add6", "13"):
            extra.add(9)
        elif token in ("7", "11"):
            extra.add(10)
            if token == "11":
                extra.add(5)
        elif token == "4" or token == "add4":
            extra.add(5)
        elif token == "b5":
            fifth = 6
        elif token == "#5":
            fifth = 8
        elif token in ("b9", "#9", "#11", "b13"):
            pass  # colour tones; the core chord is enough to play along
        elif token.startswith(("no", "omit")):
            if token.endswith("3"):
                third = None
        elif not token.startswith("add"):
            return None
    tones = {root, (root + fifth) % 12} | {(root + e) % 12 for e in extra}
    if third is not None:
        tones.add((root + third) % 12)
    bass = _pc(match["bass"]) if match["bass"] else root
    return Chord(symbol.strip(), root, bass, frozenset(tones))


@dataclass
class Chart:
    key: str | None
    chords: list[str]  # every chord in the order written, repeats included
    sections: list[str] = field(default_factory=list)
    tempo: float | None = None  # beats per minute, as printed on the chart
    time: str | None = None  # time signature, e.g. "6/8"

    @property
    def vocabulary(self) -> list[Chord]:
        seen: dict[str, Chord] = {}
        for name in self.chords:
            if name not in seen and (chord := parse_chord(name)):
                seen[name] = chord
        return list(seen.values())

    def to_dict(self) -> dict:
        return {"key": self.key, "tempo": self.tempo, "time": self.time, "chords": self.chords,
                "sections": self.sections, "unique": [c.name for c in self.vocabulary]}

    @classmethod
    def from_dict(cls, data: dict) -> "Chart":
        return cls(data.get("key"), data.get("chords", []), data.get("sections", []),
                   data.get("tempo"), data.get("time"))


def _chord_line(tokens: list[str]) -> list[str] | None:
    """The chords on a line, if the line is a line of chords (and not lyrics)."""
    chords = []
    for token in tokens:
        if _FILLER_RE.match(token):
            continue
        if parse_chord(token) is None:
            return None
        chords.append(token)
    return chords or None


def parse_chart_text(text: str) -> Chart:
    key = tempo = time = None
    chords: list[str] = []
    sections: list[str] = []
    after_heading = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if key is None and (m := _KEY_RE.search(line)):
            key = m[1]
        if tempo is None and (m := _TEMPO_RE.search(line)):
            tempo = float(m[1])
        if time is None and (m := _TIME_RE.search(line)):
            time = m[1]
        if _SECTION_RE.match(line):
            sections.append(line.rstrip(":").strip())
            after_heading = True
            continue
        if after_heading and _SECTION_CODE_RE.match(line):
            after_heading = False
            continue
        after_heading = False
        inline = _INLINE_RE.findall(line)
        if inline:  # ChordPro: [G]Amazing [C]grace
            chords += [c for c in inline if parse_chord(c)]
            continue
        found = _chord_line(line.replace("|", " | ").split())
        if found:
            chords += found
    if key is None and chords:
        first = parse_chord(chords[0])
        key = NAMES[first.root] + ("m" if first.minor else "") if first else None
    return Chart(key, chords, sections, tempo, time)


def read_chart(path: Path) -> Chart:
    """Read a chord chart file. Raises ValueError with a friendly message if it can't."""
    if path.suffix.lower() == ".pdf":
        text = _pdf_text(path)
    else:
        text = path.read_text(errors="replace")
    chart = parse_chart_text(text)
    if not chart.chords:
        if path.suffix.lower() == ".pdf" and not text.strip():
            raise ValueError("This PDF looks like a scan (it has no text in it). Type the chords in instead.")
        raise ValueError("Couldn't find any chords in that chart. Type the chords in instead.")
    return chart


def _pdf_text(path: Path) -> str:
    import pdfplumber

    columns_text = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
            for column in _split_columns(words, float(page.width)):
                columns_text.append(_lines(column))
    return "\n".join(columns_text)


def _split_columns(words: list[dict], width: float) -> list[list[dict]]:
    """Two-column chord sheets: split at a gap down the middle that no word crosses."""
    if len(words) < 20:
        return [words]
    covered = np.zeros(int(width) + 1, dtype=bool)
    for w in words:
        covered[int(w["x0"]):int(w["x1"]) + 1] = True
    middle = range(int(width * 0.3), int(width * 0.7))
    gaps = [x for x in middle if not covered[x]]
    if not gaps:
        return [words]
    split = gaps[len(gaps) // 2]
    left = [w for w in words if w["x1"] <= split]
    right = [w for w in words if w["x0"] >= split]
    if min(len(left), len(right)) < 0.15 * len(words):
        return [words]
    return [left, right]


def _lines(words: list[dict]) -> str:
    """Rebuild each visual line from its words, so chords that sit above the lyrics come
    out as their own line."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - word["top"]) < 2.5:
            lines[-1].append(word)
        else:
            lines.append([word])
    return "\n".join(" ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"])) for line in lines)


def scale_pcs(key: str | None) -> set[int] | None:
    """Pitch classes of the key's scale (a minor key uses its natural minor scale)."""
    if not key or not (chord := parse_chord(key)):
        return None
    steps = (0, 2, 3, 5, 7, 8, 10) if chord.minor else (0, 2, 4, 5, 7, 9, 11)
    return {(chord.root + s) % 12 for s in steps}
