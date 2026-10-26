"""The song library on disk, and the background worker that processes uploads one at a time.

Each song lives in `<data>/songs/<id>/` with a `song.json` describing it.
"""

import json
import logging
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from .chords import Chart

log = logging.getLogger(__name__)


class Library:
    def __init__(self, data_dir: Path, soundfont: Path):
        self.songs_dir = data_dir / "songs"
        self.songs_dir.mkdir(parents=True, exist_ok=True)
        self.soundfont = soundfont
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None

    # --- metadata ---

    def _meta_path(self, song_id: str) -> Path:
        return self.songs_dir / song_id / "song.json"

    def get(self, song_id: str) -> dict | None:
        if not _valid_id(song_id):
            return None
        try:
            return json.loads(self._meta_path(song_id).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save(self, meta: dict) -> None:
        path = self._meta_path(meta["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        tmp.replace(path)

    def _update(self, song_id: str, **changes) -> dict | None:
        with self._lock:
            meta = self.get(song_id)
            if meta is None:
                return None
            meta.update(changes)
            self._save(meta)
            return meta

    def list(self) -> list[dict]:
        songs = [self.get(p.name) for p in self.songs_dir.iterdir() if p.is_dir()]
        return sorted((s for s in songs if s), key=lambda s: s["created"], reverse=True)

    # --- changes ---

    def add(self, title: str, filename: str, upload_path: Path, chart: Chart | None = None) -> dict:
        song_id = uuid.uuid4().hex[:12]
        song_dir = self.songs_dir / song_id
        song_dir.mkdir()
        suffix = Path(filename).suffix.lower()[:8] or ".audio"
        shutil.move(str(upload_path), song_dir / f"source{suffix}")
        meta = {
            "id": song_id,
            "title": title.strip() or Path(filename).stem,
            "source": f"source{suffix}",
            "created": time.time(),
            "status": "queued",
            "step": "Waiting its turn",
            "progress": 0.0,
            "error": None,
            "duration": None,
            "tracks": [],
            "chords": [],
            "chart": None,
        }
        if chart:
            (song_dir / "chart.json").write_text(json.dumps(chart.to_dict(), indent=2))
            meta["chart"] = chart.to_dict()
        with self._lock:
            self._save(meta)
        self._queue.put(song_id)
        return meta

    def chart(self, song_id: str) -> Chart | None:
        try:
            data = json.loads((self.songs_dir / song_id / "chart.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return Chart(data.get("key"), data.get("chords", []), data.get("sections", []))

    def set_chart(self, song_id: str, chart: Chart | None) -> dict | None:
        """Save (or remove, with None) the song's chord chart and remake its tracks."""
        meta = self.get(song_id)
        if meta is None:
            return None
        path = self.songs_dir / song_id / "chart.json"
        if chart is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(chart.to_dict(), indent=2))
        summary = chart.to_dict() if chart else None
        revision = meta.get("revision", 0) + 1
        if meta["status"] == "queued":
            return self._update(song_id, chart=summary)  # read when its turn comes
        if meta["status"] == "processing":
            # It's being made with the old chart; it goes again with the new one when done.
            return self._update(song_id, chart=summary, revision=revision)
        separated = (self.songs_dir / song_id / "original" / "drums.m4a").exists()
        meta = self._update(song_id, chart=summary, revision=revision, job="rebuild" if separated else None,
                            status="queued", step="Waiting its turn", progress=0.0, error=None)
        self._queue.put(song_id)
        return meta

    def rename(self, song_id: str, title: str) -> dict | None:
        return self._update(song_id, title=title.strip())

    def retry(self, song_id: str) -> dict | None:
        meta = self._update(song_id, status="queued", step="Waiting its turn", progress=0.0, error=None)
        if meta:
            self._queue.put(song_id)
        return meta

    def delete(self, song_id: str) -> bool:
        if self.get(song_id) is None:
            return False
        shutil.rmtree(self.songs_dir / song_id, ignore_errors=True)
        return True

    def file(self, song_id: str, relative: str) -> Path | None:
        """A file inside a song's folder, refusing anything that escapes it."""
        if not _valid_id(song_id):
            return None
        song_dir = (self.songs_dir / song_id).resolve()
        path = (song_dir / relative).resolve()
        if not path.is_relative_to(song_dir) or not path.is_file():
            return None
        return path

    # --- processing ---

    def start(self) -> None:
        # Pick up anything left unfinished when the server last stopped.
        for meta in reversed(self.list()):
            if meta["status"] in ("queued", "processing"):
                self._update(meta["id"], status="queued", step="Waiting its turn", progress=0.0)
                self._queue.put(meta["id"])
        self._worker = threading.Thread(target=self._run, name="song-worker", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            song_id = self._queue.get()
            meta = self.get(song_id)
            if meta is None or meta["status"] != "queued":
                continue
            self._process(meta)

    def _process(self, meta: dict) -> None:
        from .pipeline import process_song, rebuild_tracks

        song_id = meta["id"]
        song_dir = self.songs_dir / song_id
        started = time.time()
        revision = meta.get("revision", 0)

        def on_progress(step: str, fraction: float) -> None:
            if self.get(song_id) is None:
                raise _Deleted()
            self._update(song_id, status="processing", step=step, progress=round(fraction, 3))

        try:
            on_progress("Starting", 0.0)
            chart = self.chart(song_id)
            if meta.get("job") == "rebuild":
                result = rebuild_tracks(song_dir, self.soundfont, chart, on_progress)
            else:
                result = process_song(song_dir, song_dir / meta["source"], self.soundfont, on_progress, chart=chart)
            changed = self.get(song_id).get("revision", 0) != revision
            self._update(song_id, **result, processing_seconds=round(time.time() - started), version=time.time())
            if changed:  # the chart changed while this ran
                self._update(song_id, status="queued", job="rebuild", step="Waiting its turn", progress=0.0)
                self._queue.put(song_id)
            else:
                self._update(song_id, status="ready", step="Ready", progress=1.0, job=None)
            log.info("Finished %s in %.0fs", meta["title"], time.time() - started)
        except _Deleted:
            shutil.rmtree(song_dir, ignore_errors=True)
        except Exception as exc:
            log.error("Failed to process %s:\n%s", meta["title"], traceback.format_exc())
            self._update(song_id, status="failed", step="Something went wrong", error=str(exc)[:500])


class _Deleted(Exception):
    """The song was deleted while it was being processed."""


def _valid_id(song_id: str) -> bool:
    return len(song_id) == 12 and all(c in "0123456789abcdef" for c in song_id)
